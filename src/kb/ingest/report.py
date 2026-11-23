"""`kb ingest --report`: gate whatever the agent drafted since `base` (A-16)."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from kb.config.load import ConfigError, load_config
from kb.config.schema import KbConfig
from kb.gates.anchors import anchor_gate
from kb.gates.base import GateContext, run_gates
from kb.gates.decisions import decision_guard
from kb.gates.ids import id_gate
from kb.gates.quotes import missing_evidence, quote_gate
from kb.gates.sources import STORE_FILE, bad_segment_ids, recover_hint, sha256_file, source_gate, stored_ids
from kb.gitfiles import GitError, file_at, read_tree_files, require_commit, rev_parse
from kb.ingest.brief import recorded_base
from kb.issues import Issue, new_since
from kb.records.models import Decision, Invariant, Requirement
from kb.records.repo import KbRepo
from kb.textfmt import inline_code
from kb.yamlio import YamlError, load_yaml_strict

GATES = [id_gate, decision_guard, quote_gate, anchor_gate, source_gate]
BASE_GATES = [g for g in GATES if g is not source_gate]
CONFIG = "kb.config.yaml"
ORIGINAL_CHANGED = (
    "the source document changed in the same change as the drafts; commit document updates separately, before kb ingest"
)


@dataclass
class IngestReport:
    accepted: list[str]
    rejected: list[Issue]  # every error that fails --strict, including `caused`
    flagged: list[Issue]
    untriaged: list[str]
    proposed_modules: list[str]
    sources: list[Issue] = field(default_factory=list)  # source_gate, source.tampered, ingest.original_changed
    caused: list[Issue] = field(default_factory=list)  # new errors on records the change did not touch
    base: str = ""  # the commit compared against
    drafted: bool = True  # whether anything (records, triage, modules) was added or changed since base

    @property
    def failed(self) -> bool:
        """What --strict fails on."""
        return bool(self.rejected) or any(i.severity == "error" for i in self.sources)


def _base_config(root: Path, ref: str) -> KbConfig | None:
    data = read_tree_files(root, ref, CONFIG).get(CONFIG)
    if data is None:
        return None
    try:
        return KbConfig.model_validate(load_yaml_strict(data.decode("utf-8")))
    except (UnicodeDecodeError, YamlError, ValidationError) as e:
        raise ConfigError([f"{CONFIG} at {ref}: {e}"]) from e


def _config_issues(head: KbConfig, base: KbConfig | None) -> list[Issue]:
    """Ingest may only add modules, without paths: everything else in the config must be as it was at base."""
    known = {m.id for m in base.modules} if base else set()
    issues = [
        Issue("ingest.module_paths", f"{m.id}: modules are proposed without paths; paths come from `kb module paths {m.id} --accept`",
              path=CONFIG, record=m.id)
        for m in head.modules if m.id not in known and m.paths
    ]
    if base is None:
        return issues
    now = head.model_dump()
    now["modules"] = [m for m in now["modules"] if m["id"] in known]
    if now != base.model_dump():
        issues.append(Issue("ingest.config_changed", f"{CONFIG} changed other than by adding modules; ingest may only add modules", path=CONFIG))
    return issues


def _drafted_paths(head: KbRepo, before: KbRepo) -> set[str]:
    """kb/ paths the agent added, changed or deleted since base, including ones that now fail to load."""
    changed = {p for p in set(head.raw) | set(before.raw) if head.raw.get(p) != before.raw.get(p)}
    changed |= {i.path for i in head.errors if i.path} - {i.path for i in before.errors if i.path}
    return {p for p in changed if not STORE_FILE.fullmatch(p)}


def _originals_changed(root: Path, before: KbRepo, ref: str) -> list[Issue]:
    """Sources stored at base whose original document differs at head (from base's source.yaml, or
    from the original's bytes at base in git): drafts must not land together with document edits."""
    issues = []
    for src_id, src in sorted(before.sources.items()):
        try:
            now = sha256_file(root / src.original)
        except (ValueError, OSError):
            now = None
        at_base = file_at(root, ref, src.original)
        git_sha = hashlib.sha256(at_base).hexdigest() if at_base is not None else None
        if now != src.original_sha256 or (git_sha is not None and git_sha != now):
            issues.append(Issue("ingest.original_changed", ORIGINAL_CHANGED, path=src.original, record=src_id))
    return issues


def _citers(repo: KbRepo, src_ids: set[str]) -> list[tuple[str, str]]:
    """(record id, source) for records whose evidence cites one of `src_ids`."""
    out = []
    for rid, rec in sorted(repo.records.items()):
        if isinstance(rec, (Requirement, Decision, Invariant)) and rec.evidence:
            cited = sorted({q.source.partition("#")[0] for q in rec.evidence.quotes} & src_ids)
            out.extend((rid, src) for src in cited)
    return out


def _untriaged(repo: KbRepo) -> list[str]:
    """Segments with no triage entry; sources with invalid segment ids are skipped (reported as tampered)."""
    out = []
    bad = set(bad_segment_ids(repo))
    for src_id in sorted(set(repo.sources) - bad):
        tri = repo.triage(src_id)
        done = tri.segments if tri else {}
        for seg in sorted(repo.segments(src_id), key=lambda s: int(s.id.rsplit("-", 1)[1])):
            if seg.id not in done:
                out.append(f"{src_id}#{seg.id} · {seg.heading}")
    return out


def ingest_report(root: Path, base: str | None) -> IngestReport:
    """Gate the records added, changed or deleted since `base`, and the errors the change causes elsewhere.
    Default base: the commit the last `kb ingest` ran on (when it is an ancestor of HEAD), else HEAD.
    Unchanged records with unchanged problems are not reported."""
    root = Path(root)
    require_commit(root)
    ref = base or recorded_base(root) or "HEAD"
    commit = rev_parse(root, ref)
    if commit is None:
        raise GitError(f"unknown base {ref!r}")
    config = load_config(root / CONFIG)
    head, before = KbRepo.load(root), KbRepo.at_ref(root, ref)
    base_config = _base_config(root, ref)
    issues = run_gates(GateContext(root, config, head, before, ref), GATES)
    # source issues are never "caused" (they are reported under Sources), so the base needs no source gate
    at_base = run_gates(GateContext(root, base_config or config, before), BASE_GATES)

    drafted = _drafted_paths(head, before)
    ids = {rid for rid, path in head.id_paths if path in drafted}
    source_issues = [i for i in issues if i.code.startswith("source.")]
    mine = [i for i in issues if (i.record in ids or i.path in drafted) and not i.code.startswith("source.")]
    # whoever wrote it, a decision or invariant drafted in this range must quote its document (C2)
    mine = [i for i in mine if i.code != "evidence.missing"] + [
        missing_evidence(rid, rec, "error", head.paths[rid])
        for rid, rec in sorted(head.records.items())
        if rid in ids and isinstance(rec, (Decision, Invariant)) and rec.evidence is None
    ]
    direct = [i for i in mine if i.severity == "error"]
    caused = new_since([i for i in issues if i.severity == "error" and i not in mine and not i.code.startswith("source.")], at_base)

    source_issues += [  # `kb ingest` never deletes a source
        Issue("source.tampered", f"kb/sources/{s} was deleted; {recover_hint(ref)}", path=f"kb/sources/{s}", record=s)
        for s in sorted(stored_ids(before) - stored_ids(head))
    ]
    source_issues += _originals_changed(root, before, ref)
    tampered = [i for i in source_issues if i.code == "source.tampered"]
    # a source with any error is not evidence; a secret in the original is masked in the store, so its
    # stored text still stands
    bad_sources = {i.record: i.code for i in reversed(source_issues)
                   if i.severity == "error" and i.record and i.code != "source.secret_in_original"}
    tainted = [
        Issue(bad_sources[src], f"{rid} cites {src}, which has a source error ({bad_sources[src]}); it is not evidence until fixed",
              path=head.paths[rid], record=rid)
        for rid, src in _citers(head, set(bad_sources))
    ]
    rejected = direct + _config_issues(config, base_config) + tampered + tainted + caused
    bad_paths = {i.path for i in rejected}
    bad = {i.record for i in rejected} | {rid for rid, path in head.id_paths if path in bad_paths}
    proposed = sorted({m.id for m in config.modules} - {m.id for m in (base_config.modules if base_config else [])})
    return IngestReport(
        accepted=sorted(ids - bad),
        rejected=rejected,
        flagged=[i for i in mine if i.severity == "warning"],
        untriaged=_untriaged(head),
        proposed_modules=proposed,
        sources=source_issues,
        caused=caused,
        base=commit,
        drafted=bool(drafted) or proposed != [],
    )


def _issue_lines(issues: list[Issue]) -> list[str]:
    return [f"- {inline_code(i.record or i.path or '')} · `{i.code}` · {inline_code(i.message)}" for i in issues]


def _untriaged_line(entry: str) -> str:
    ref, _, heading = entry.partition(" · ")
    return f"- {inline_code(ref)} · {inline_code(heading)}"


def _section(title: str, lines: list[str], level: str = "##") -> list[str]:
    return [f"{level} {title} ({len(lines)})", "", *(lines or ["- none"]), ""]


def render_report(r: IngestReport) -> str:
    """Markdown; every piece of untrusted text (headings, messages, paths) is one-line inline code."""
    direct = [i for i in r.rejected if i not in r.caused]
    out = ["# Ingest report", ""]
    out += _section("Accepted", [f"- {rid}" for rid in r.accepted])
    out += [f"## Rejected ({len(r.rejected)})", "", *(_issue_lines(direct) or ["- none"]), ""]
    if r.caused:
        out += _section("Caused by this change", _issue_lines(r.caused), "###")
    out += _section("Flagged", _issue_lines(r.flagged))
    out += _section("Sources", _issue_lines(r.sources))
    out += _section("Untriaged segments", [_untriaged_line(s) for s in r.untriaged])
    out += _section("Proposed modules", [f"- {m}" for m in r.proposed_modules])
    return "\n".join(out)
