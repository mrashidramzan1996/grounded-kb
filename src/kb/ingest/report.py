"""`kb ingest --report`: gate whatever the agent drafted since `base` (A-16)."""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from kb.config.load import ConfigError, load_config
from kb.config.schema import KbConfig
from kb.gates.anchors import anchor_gate
from kb.gates.base import GateContext, run_gates
from kb.gates.ids import id_gate
from kb.gates.quotes import quote_gate
from kb.gates.sources import source_gate
from kb.gitfiles import read_tree_files
from kb.issues import Issue
from kb.records.repo import KbRepo
from kb.yamlio import YamlError, load_yaml_strict

GATES = [id_gate, quote_gate, anchor_gate, source_gate]
CONFIG = "kb.config.yaml"
# files `kb ingest` writes itself; everything else under kb/ is the agent's
_STORE_FILE = re.compile(r"kb/sources/SRC-[^/]+/(?:source\.yaml|text\.md|segments\.jsonl)")


@dataclass
class IngestReport:
    accepted: list[str]
    rejected: list[Issue]
    flagged: list[Issue]
    untriaged: list[str]
    proposed_modules: list[str]


def _base_modules(root: Path, ref: str) -> set[str]:
    data = read_tree_files(root, ref, CONFIG).get(CONFIG)
    if data is None:
        return set()
    try:
        config = KbConfig.model_validate(load_yaml_strict(data.decode("utf-8")))
    except (UnicodeDecodeError, YamlError, ValidationError) as e:
        raise ConfigError([f"{CONFIG} at {ref}: {e}"]) from e
    return {m.id for m in config.modules}


def _drafted_paths(head: KbRepo, before: KbRepo) -> set[str]:
    """kb/ paths the agent added or changed since base, including ones that now fail to load."""
    changed = {p for p, data in head.raw.items() if before.raw.get(p) != data}
    changed |= {i.path for i in head.errors if i.path} - {i.path for i in before.errors if i.path}
    return {p for p in changed if not _STORE_FILE.fullmatch(p)}


def _untriaged(repo: KbRepo) -> list[str]:
    out = []
    for src_id in sorted(repo.sources):
        tri = repo.triage(src_id)
        done = tri.segments if tri else {}
        for seg in sorted(repo.segments(src_id), key=lambda s: int(s.id.rsplit("-", 1)[1])):
            if seg.id not in done:
                out.append(f"{src_id}#{seg.id} · {seg.heading}")
    return out


def ingest_report(root: Path, base: str | None) -> IngestReport:
    """Gate the records added or changed since `base` (default HEAD); unchanged records are not reported."""
    root = Path(root)
    ref = base or "HEAD"
    config = load_config(root / CONFIG)
    head, before = KbRepo.load(root), KbRepo.at_ref(root, ref)
    issues = run_gates(GateContext(root, config, head, before), GATES)

    drafted = _drafted_paths(head, before)
    ids = {rid for rid, path in head.id_paths if path in drafted}
    mine = [i for i in issues if i.record in ids or i.path in drafted]
    rejected = [i for i in mine if i.severity == "error"]
    bad_paths = {i.path for i in rejected}
    bad = {i.record for i in rejected} | {rid for rid, path in head.id_paths if path in bad_paths}
    return IngestReport(
        accepted=sorted(ids - bad),
        rejected=rejected,
        flagged=[i for i in mine if i.severity == "warning"],
        untriaged=_untriaged(head),
        proposed_modules=sorted({m.id for m in config.modules} - _base_modules(root, ref)),
    )


def _issue_lines(issues: list[Issue]) -> list[str]:
    return [f"- {i.record or i.path} · `{i.code}` · {i.message}" for i in issues]


def render_report(r: IngestReport) -> str:
    sections = [
        ("Accepted", [f"- {rid}" for rid in r.accepted]),
        ("Rejected", _issue_lines(r.rejected)),
        ("Flagged", _issue_lines(r.flagged)),
        ("Untriaged segments", [f"- {s}" for s in r.untriaged]),
        ("Proposed modules", [f"- {m}" for m in r.proposed_modules]),
    ]
    out = ["# Ingest report", ""]
    for title, lines in sections:
        out += [f"## {title} ({len(lines)})", "", *(lines or ["- none"]), ""]
    return "\n".join(out)
