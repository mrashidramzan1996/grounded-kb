"""`kb todo`: what the user's coding agent does next (`do`), and what it must ask the person (`ask`).

Built from `kb check` and the source store; it never answers an `ask` item. Ids are stable across runs.
"""

import json
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from kb.baseline import requirement_hashes
from kb.check import run_check
from kb.config.load import load_config
from kb.config.schema import KbConfig
from kb.gates.base import GateContext
from kb.gates.sources import STORE_NAMES, changed_message, differs_prefix, source_gate
from kb.gitfiles import (
    find_default_branch,
    has_commits,
    head_commit,
    is_dirty,
    rev_parse,
    tag_exists,
)
from kb.ingest.redraft import changed_segments, citing, new_text, unfound
from kb.issues import Issue
from kb.records.lifecycle import live_ids
from kb.records.models import Requirement
from kb.records.repo import KbRepo
from kb.textfmt import inline_code
from kb.yamlio import YamlError, load_yaml_strict


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
    question: str  # raw, for JSON
    evidence: dict  # {} or {"record": id, "statement": ..., "source": "SRC-x#seg-NN", "quote": ..., ...}
    options: tuple[Option, ...]
    question_text: str  # the question for text output: its untrusted parts as inline code; not in JSON


@dataclass(frozen=True)
class Todo:
    do: tuple[DoItem, ...]  # sorted by id
    ask: tuple[AskItem, ...]  # sorted by id
    state: dict  # initialized, sources, records, errors, warnings, baseline ("vN" or None), clean, blocking_asks


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


_KEEP = Option("keep", "Keep it flagged for now", "leave it; it stays a warning")
_APPROVER_QUESTIONS = {
    "policy.approvers.intent": "Who approves changes to requirements? Give a forge handle such as @org/team or @user.",
    "policy.approvers.decisions":
        "Who approves decisions and invariants? Give a forge handle such as @org/team or @user.",
}
_OWNER_TODO_SUFFIX = ": owner is still TODO"  # kb.config.validate: f"{where}: owner is still TODO"


def _question(template: str, quoted: bool = False, **untrusted: str) -> tuple[str, str]:
    """(JSON form, text form) of `template` filled with untrusted values: raw in the JSON form (in double quotes
    when `quoted`), as inline code in the text form. The template itself is trusted text."""
    raw = {k: f'"{v}"' if quoted else v for k, v in untrusted.items()}
    return template.format(**raw), template.format(**{k: inline_code(v) for k, v in untrusted.items()})


def _evidence(rid: str, rec: Requirement, repo: KbRepo) -> dict:
    """Where the requirement's first quote comes from; a missing source or segment gives empty values."""
    q = rec.evidence.quotes[0]
    src_id, _, seg_id = q.source.partition("#")
    src = repo.sources.get(src_id)
    seg = next((s for s in repo.segments(src_id) if s.id == seg_id), None)
    return {
        "record": rid, "statement": rec.statement, "source": q.source, "quote": q.text,
        "document": src.original if src else "", "page": seg.page if seg else None, "heading": seg.heading if seg else "",
    }


def _flag_ask(flag: str, rid: str, rec: Requirement, repo: KbRepo) -> AskItem:
    if flag == "vague":
        question = _question(f"{rid} says {{statement}} but gives no measurable target. What should it be?",
                             quoted=True, statement=rec.statement)
        answer = Option("target", "Give a measurable target (a number and a unit)",
                        f"record the answer with `kb clarify`, then `kb redraft {rid} --new` and quote the new sentence")
    elif flag == "conflicting":
        question = _question(f"{rid} contradicts another requirement. Which document is right?")
        answer = Option("decide", "Say which is right",
                        "record it with `kb clarify`, update or retire the other requirement with `kb redraft`")
    else:  # untestable
        question = _question(f"{rid} cannot be tested as written. How should it be checked?")
        answer = Option("define", "Describe how to check it", f"record it with `kb clarify`, then `kb redraft {rid} --new`")
    retire = Option("retire", "It is not a real requirement", f"set `status: rejected` on {rid}; never delete the file")
    return AskItem(f"{flag}:{rid}", flag, question[0], _evidence(rid, rec, repo), (answer, _KEEP, retire), question[1])


def _flag_asks(repo: KbRepo) -> list[AskItem]:
    """One item per flag of every live requirement."""
    live = live_ids(repo)
    return [
        _flag_ask(flag, rid, rec, repo)
        for rid, rec in sorted(repo.records.items())
        if rid in live and isinstance(rec, Requirement)
        for flag in dict.fromkeys(rec.flags)
    ]


def _owner_asks(issues: list[Issue], config: KbConfig) -> list[AskItem]:
    """One item per config location whose owner is still TODO."""
    titles = {m.id: m.title for m in config.modules}
    out: dict[str, AskItem] = {}
    for i in issues:
        if i.code != "config.owner_todo":
            continue
        if i.record:  # a module
            where = f"modules.{i.record}.owner"
            title = titles.get(i.record)
            question = (_question(f"Who owns {i.record} ({{title}})? Give a forge handle.", title=title) if title
                        else _question(f"Who owns {i.record}? Give a forge handle."))
        else:
            where = i.message.removesuffix(_OWNER_TODO_SUFFIX)
            question = _question(_APPROVER_QUESTIONS.get(where, "Who should be the owner at {where}? Give a forge handle."),
                                 where=where)
        # a redraft brief rejects any config change (ingest.config_changed): the answer is committed first
        handle = Option("handle", "Give a handle", f"write it into kb.config.yaml at {where}, then commit "
                        "kb.config.yaml on its own before running `kb redraft` (a redraft rejects config changes)")
        out[f"owner:{where}"] = AskItem(f"owner:{where}", "owner", question[0], {}, (handle,), question[1])
    return list(out.values())


def _secret_asks(issues: list[Issue], repo: KbRepo) -> list[AskItem]:
    out: dict[str, AskItem] = {}
    for i in issues:
        if i.code != "source.secret_in_original" or not i.record:
            continue
        src = repo.sources.get(i.record)
        document = src.original if src else i.record
        question = _question("{document} contains a secret (the stored copy is masked). Remove it from the original?",
                             document=document)
        out[i.record] = AskItem(
            f"secret:{i.record}", "secret", question[0], {"document": document, "source": i.record},
            (
                Option("remove", "Remove it from the document",
                       f"delete that paragraph from {inline_code(document)} (or ask the person to, for PDF or Word), then "
                       "`kb source sync`; tell the person to rotate the key"),
                Option("self", "I will handle it", "leave it; kb check keeps reporting it"),
            ),
            question[1],
        )
    return list(out.values())


def _baseline_requirements(root: Path, version: str) -> object:
    """What kb/baseline/<version>.yaml records under `requirements`, or None when it cannot be read."""
    try:
        data = load_yaml_strict((root / f"kb/baseline/{version}.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YamlError):
        return None
    return data.get("requirements") if isinstance(data, dict) else None


def _freeze_asks(root: Path, repo: KbRepo, latest: str | None, errors: int, pending: bool) -> list[AskItem]:
    """Offer the next baseline when nothing else is left (no error, no do item, no ask but flag asks: keeping a
    flag is a valid answer), there is a live requirement, HEAD is the tip of the default branch with kb/ and
    kb.config.yaml committed, the live requirements differ from the `latest` baseline (or there is none), and
    `kb baseline freeze` would accept the next version (kb and kb/baseline are no symlinks, its tag is free)."""
    if errors or pending or not has_commits(root):
        return []
    folder = root / "kb/baseline"
    if (root / "kb").is_symlink() or folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        return []
    reqs = requirement_hashes(repo)
    if not reqs or is_dirty(root, "kb/") or is_dirty(root, "kb.config.yaml"):
        return []
    branch = find_default_branch(root)
    if branch is None or rev_parse(root, branch) != head_commit(root):
        return []
    if latest is not None and _baseline_requirements(root, latest) == reqs:
        return []
    version = f"v{int(latest[1:]) + 1}" if latest else "v1"
    if tag_exists(root, f"kb-baseline-{version}"):  # frozen already, its file not merged yet
        return []
    question = _question(f"The knowledge base has no errors. Freeze baseline {version}?")
    return [AskItem(
        f"freeze:{version}", "freeze", question[0], {},
        (
            Option("freeze", "Freeze it", f"`kb baseline freeze {version}`, commit, and open a PR for review"),
            Option("later", "Not yet", "leave it"),
        ),
        question[1],
    )]


def build_todo(root: Path) -> Todo:
    """Raises ConfigError / GitError (and the other tool errors of `kb check`); the caller maps them to exit 2."""
    root = Path(root)
    if not (root / "kb.config.yaml").exists():
        init = DoItem("init", "kb.init", "kb.config.yaml not found; set up the knowledge base", "kb init", None, None)
        state = {
            "initialized": False, "sources": 0, "records": 0, "errors": 0, "warnings": 0, "baseline": None,
            "clean": False, "blocking_asks": 0,
        }
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
    # owners and secrets block; flag and freeze asks are optional decisions ("keep it flagged" and "not yet" are
    # answers), so they never make the KB unclean
    blocking = _owner_asks(issues, config) + _secret_asks(issues, repo)
    errors = sum(i.severity == "error" for i in issues)
    latest = _baseline(root)
    ask = _flag_asks(repo) + blocking + _freeze_asks(root, repo, latest, errors, bool(do or blocking))
    state = {
        "initialized": True, "sources": len(repo.sources), "records": len(repo.records), "errors": errors,
        "warnings": len(issues) - errors, "baseline": latest, "clean": not errors and not do and not blocking,
        "blocking_asks": len(blocking),
    }
    return Todo(tuple(sorted(do, key=lambda d: d.id)), tuple(sorted(ask, key=lambda a: a.id)), state)


def _line(text: str) -> str:
    return " ".join(text.split())


def _evidence_text(ev: dict) -> str:
    """`<document>, page <page>, <heading>: <quote>`, leaving out what is empty; untrusted text as inline code."""
    parts = [
        inline_code(ev["document"]) if ev.get("document") else "",
        f"page {ev['page']}" if ev.get("page") is not None else "",
        inline_code(ev["heading"]) if ev.get("heading") else "",
    ]
    where = ", ".join(p for p in parts if p)
    if "quote" not in ev:
        return where
    return f"{where}: {inline_code(ev['quote'])}" if where else inline_code(ev["quote"])


def render_todo_text(t: Todo) -> str:
    lines = ["Do:"]
    for d in t.do:
        lines.append(f"- {_line(d.message)}")
        if d.command:
            lines.append(f"  run: {_line(d.command)}")
    if not t.do:
        lines.append("- nothing")
    lines += ["", "Ask the user:"]
    for n, a in enumerate(t.ask, 1):
        lines.append(f"{n}. {_line(a.question_text)}")
        if a.evidence:
            lines.append(f"   evidence: {_evidence_text(a.evidence)}")
        lines.extend(f"   ({chr(ord('a') + k)}) {_line(o.label)}" for k, o in enumerate(a.options))
    if not t.ask:
        lines.append("- nothing")
    return "\n".join(lines) + "\n"


def render_todo_json(t: Todo) -> str:
    ask = [{k: v for k, v in asdict(a).items() if k != "question_text"} for a in t.ask]
    data = {"do": [asdict(d) for d in t.do], "ask": ask, "state": t.state}
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


def summary_line(t: Todo) -> str:
    return f"{len(t.do)} to do, {len(t.ask)} questions for the user"
