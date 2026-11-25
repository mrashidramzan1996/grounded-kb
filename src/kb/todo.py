"""`kb todo`: what the user's coding agent does next (`do`), and what it must ask the person (`ask`).

Built from `kb check` and the source store; it never answers an `ask` item. Ids are stable across runs.
"""

import json
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from kb.check import run_check
from kb.config.load import load_config
from kb.config.schema import KbConfig
from kb.gates.base import GateContext
from kb.gates.sources import STORE_NAMES, changed_message, differs_prefix, source_gate
from kb.gitfiles import has_commits, head_commit
from kb.ingest.redraft import changed_segments, citing, new_text, unfound
from kb.issues import Issue
from kb.records.repo import KbRepo
from kb.textfmt import inline_code


@dataclass(frozen=True)
class DoItem:
    id: str  # stable, e.g. "sync", "ingest:docs/refund-policy.md", "fix:quote.not_found:REQ-F-002"
    code: str  # the issue code it comes from, or "kb.init" / "kb.ingest" / "kb.sync" / "kb.redraft"
    message: str
    command: str | None  # what the agent runs, e.g. "kb source sync"
    path: str | None
    record: str | None


@dataclass(frozen=True)
class Option:
    key: str  # short, e.g. "target", "keep", "retire"
    label: str  # what the person sees
    then: str  # what the agent does if chosen (plain sentence, may name commands)


@dataclass(frozen=True)
class AskItem:
    id: str  # stable, e.g. "vague:REQ-NF-001", "owner:policy.approvers.intent", "freeze:v1"
    kind: str  # vague | conflicting | untestable | owner | secret | freeze
    question: str
    evidence: dict  # {} or {"record": id, "statement": ..., "source": "SRC-x#seg-NN", "quote": ..., ...}
    options: tuple[Option, ...]


@dataclass(frozen=True)
class Todo:
    do: tuple[DoItem, ...]  # sorted by id
    ask: tuple[AskItem, ...]  # sorted by id
    state: dict  # initialized, sources, records, errors, warnings, baseline ("vN" or None), clean


# errors that become `ask` items: only the person can settle them
ASK_CODES = {"config.owner_todo", "source.secret_in_original"}
# source.outdated is sorted by _source_state (a sync, or a fix: item when the original is missing or unreadable)
COVERED_CODES = {"source.outdated"}
_BASELINE = re.compile(r"v(\d+)\.yaml")
_SHOWN_REFS = 10


def _baseline(root: Path) -> str | None:
    """The highest kb/baseline/vN.yaml version, or None."""
    folder = root / "kb/baseline"
    nums = [int(m.group(1)) for p in folder.iterdir() if (m := _BASELINE.fullmatch(p.name))] if folder.is_dir() else []
    return f"v{max(nums)}" if nums else None


def _refs(refs: list[str]) -> str:
    more = f" … and {len(refs) - _SHOWN_REFS} more" if len(refs) > _SHOWN_REFS else ""
    return ", ".join(refs[:_SHOWN_REFS]) + more


def _ingest_items(repo: KbRepo, issues: list[Issue]) -> list[DoItem]:
    missing = [i.path for i in issues if i.code == "source.not_ingested" and i.path]
    if not missing:
        return []
    if not repo.sources:  # nothing stored yet: ingest everything at once
        return [DoItem("ingest:all", "kb.ingest", "documents under config.sources were never ingested", "kb ingest", None, None)]
    return [
        DoItem(f"ingest:{p}", "kb.ingest", f"{inline_code(p)} is a document under config.sources that was never ingested",
               f"kb ingest {shlex.quote(p)}", p, None)
        for p in missing
    ]


def _changed(i: Issue, repo: KbRepo, head: KbRepo | None) -> bool:
    """Whether `i` says the original changed since it was stored and reads as it is now: what a sync fixes."""
    originals = {r.sources[i.record].original for r in (repo, head) if r is not None and i.record in r.sources}
    return i.code == "source.outdated" and any(i.message == changed_message(o) for o in originals)


def _source_state(root: Path, config: KbConfig, repo: KbRepo, head: KbRepo | None,
                  issues: list[Issue]) -> tuple[set[str], list[Issue]]:
    """(sources a sync brings up to date, source.outdated issues only a person or agent can fix: the original is
    missing or cannot be read). `kb check` without a base cannot tell a changed original from an edited store
    (both are source.tampered), so when HEAD exists the source gate runs again against it, as
    `kb check --base` would."""
    # defensive: plain run_check has no base, so it reports no source.outdated today
    found = [i for i in issues if i.code == "source.outdated"]
    if head is not None:
        found += [i for i in source_gate(GateContext(root, config, repo, head, head_commit(root))) if i.code == "source.outdated"]
    outdated = {i.record for i in found if i.record and _changed(i, repo, head)}
    broken = list(dict.fromkeys(i for i in found if not _changed(i, repo, head)))
    return outdated, broken


def _rebuilt_by_sync(i: Issue, repo: KbRepo, outdated: set[str]) -> bool:
    """A store file that differs from what its changed original gives: the sync rewrites it. A store that cannot
    be re-derived at all is never covered."""
    src = repo.sources.get(i.record or "")
    return (
        i.code == "source.tampered" and i.record in outdated and src is not None
        and any(i.message.startswith(differs_prefix(i.record, name, src.original)) for name in STORE_NAMES)
    )


def _redraft_items(repo: KbRepo, head: KbRepo | None) -> list[DoItem]:
    """Records citing text changed since HEAD or with a quote not found; untriaged text no record cites.
    With no commits there is no HEAD to compare with: only the quotes not found count."""
    stale = set(unfound(repo)) | (set(citing(repo, changed_segments(head, repo))) if head else set())
    fresh = new_text(repo, head) if head else []
    out = []
    if stale:
        out.append(DoItem("redraft:changed", "kb.redraft",
                          f"records quote text that changed or is no longer found: {', '.join(sorted(stale))}",
                          "kb redraft --changed", None, None))
    if fresh:
        out.append(DoItem("redraft:new", "kb.redraft", f"document text no record covers yet: {_refs(fresh)}",
                          "kb redraft --new", None, None))
    return out


def _fix_items(issues: list[Issue], repo: KbRepo, outdated: set[str], broken: list[Issue]) -> list[DoItem]:
    """One item per (code, record or path) for every error but those of ASK_CODES, COVERED_CODES and the store
    files the sync rewrites, for each source.bad_filename warning, and for each `broken` source; messages of
    the same item are joined."""
    by_id: dict[str, list[Issue]] = {}
    wanted = [
        i for i in issues
        if (i.severity == "error" and i.code not in ASK_CODES | COVERED_CODES and not _rebuilt_by_sync(i, repo, outdated))
        or i.code == "source.bad_filename"
    ]
    for i in wanted + broken:
        by_id.setdefault(f"fix:{i.code}:{i.record or i.path or '-'}", []).append(i)
    return [
        DoItem(key, group[0].code, "; ".join(dict.fromkeys(i.message for i in group)), None, group[0].path, group[0].record)
        for key, group in by_id.items()
    ]


def _triage_items(issues: list[Issue]) -> list[DoItem]:
    counts: dict[str, int] = {}
    for i in issues:
        if i.code == "segment.untriaged" and i.record:
            counts[i.record] = counts.get(i.record, 0) + 1
    return [
        DoItem(f"triage:{src}", "segment.untriaged",
               f"{src}: {n} segment{'s' if n != 1 else ''} without a triage entry; give every segment a triage entry",
               None, f"kb/sources/{src}/triage.yaml", src)
        for src, n in counts.items()
    ]


def build_todo(root: Path) -> Todo:
    """Raises ConfigError / GitError (and the other tool errors of `kb check`); the caller maps them to exit 2."""
    root = Path(root)
    if not (root / "kb.config.yaml").exists():
        init = DoItem("init", "kb.init", "kb.config.yaml not found; set up the knowledge base", "kb init", None, None)
        state = {"initialized": False, "sources": 0, "records": 0, "errors": 0, "warnings": 0, "baseline": None, "clean": False}
        return Todo((init,), (), state)
    issues = run_check(root).issues
    config = load_config(root / "kb.config.yaml")
    repo = KbRepo.load(root)
    head = KbRepo.at_ref(root, "HEAD") if has_commits(root) else None
    outdated, broken = _source_state(root, config, repo, head, issues)
    do = _ingest_items(repo, issues)
    if outdated:
        do.append(DoItem("sync", "kb.sync", "documents changed since they were stored", "kb source sync", None, None))
    do += _redraft_items(repo, head) + _fix_items(issues, repo, outdated, broken) + _triage_items(issues)
    ask: list[AskItem] = []
    errors = sum(i.severity == "error" for i in issues)
    state = {
        "initialized": True, "sources": len(repo.sources), "records": len(repo.records), "errors": errors,
        "warnings": len(issues) - errors, "baseline": _baseline(root), "clean": not errors and not do and not ask,
    }
    return Todo(tuple(sorted(do, key=lambda d: d.id)), tuple(sorted(ask, key=lambda a: a.id)), state)


def _line(text: str) -> str:
    return " ".join(text.split())


def render_todo_text(t: Todo) -> str:
    lines = ["Do:"]
    for d in t.do:
        lines.append(f"- {_line(d.message)}")
        if d.command:
            lines.append(f"  run: {_line(d.command)}")
    if not t.do:
        lines.append("- nothing")
    lines += ["", "Ask the user:"]
    for a in t.ask:
        lines.append(f"- {_line(a.question)}")
        lines.extend(f"  {o.key}: {_line(o.label)}" for o in a.options)
    if not t.ask:
        lines.append("- nothing")
    return "\n".join(lines) + "\n"


def render_todo_json(t: Todo) -> str:
    data = {"do": [asdict(d) for d in t.do], "ask": [asdict(a) for a in t.ask], "state": t.state}
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


def summary_line(t: Todo) -> str:
    return f"{len(t.do)} to do, {len(t.ask)} questions for the user"
