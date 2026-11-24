"""`kb redraft --report`: gate what the agent redrafted since the brief was written (A-17)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from kb.config.load import load_config
from kb.gitfiles import GitError, file_at, require_commit, rev_parse
from kb.ingest.brief import recorded_base
from kb.ingest.redraft import (
    REDRAFT_BASE,
    REDRAFT_BRIEF,
    REDRAFT_IDS,
    RedraftError,
    check_named,
)
from kb.ingest.report import (
    CONFIG,
    base_config,
    deleted_sources,
    gate_drafts,
    issue_lines,
    section,
    tainted,
)
from kb.issues import Issue
from kb.records.ids import ADR_ID, INV_ID, REQ_ID
from kb.records.models import Decision, Invariant, Requirement
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath, read_regular
from kb.textfmt import inline_code

_SELECTABLE = re.compile(rf"{REQ_ID}|{ADR_ID}|{INV_ID}")
_DIGIT = re.compile(r"\d")
_TRIAGE = re.compile(r"kb/sources/(SRC-[^/]+)/triage\.yaml")
_BRIEF_BASE = re.compile(r"this brief was written at commit ([0-9a-f]+)\.")
NO_REDRAFT = f"no redraft selection in {REDRAFT_IDS}; run `kb redraft` first"
MISMATCH = "the redraft files don't match; run `kb redraft` again"


@dataclass
class RedraftReport:
    base: str
    selected: list[str]
    redrafted: list[str]  # selected ids whose file changed and that have no rejection
    superseding: list[str]  # new ADR/INV ids whose `supersedes` names a selected id, with no rejection
    rejected: list[Issue]  # every error that fails --strict, including `caused`
    flagged: list[Issue]  # warnings on redrafted/superseding records, plus "flag.cleared" notices
    unchanged: list[str]  # selected ids not touched (and not superseded by an accepted record)
    still_flagged: list[str]  # selected requirements that still carry flags
    sources: list[Issue] = field(default_factory=list)  # every source issue; its errors are also in `rejected`
    caused: list[Issue] = field(default_factory=list)  # new errors on records and files the redraft did not touch
    documents_changed: list[str] = field(default_factory=list)  # originals whose sha256 differs from base


def _read(root: Path, rel: str) -> bytes | None:
    try:
        return read_regular(root, rel)
    except UnsafePath:
        return None


def _selection(root: Path) -> list[str]:
    """The ids `kb redraft` wrote; RedraftError when the file is missing or is not a list of record ids."""
    try:
        ids = (_read(root, REDRAFT_IDS) or b"").decode("ascii").split()
    except UnicodeDecodeError:
        ids = []
    if not ids or not all(_SELECTABLE.fullmatch(i) for i in ids):
        raise RedraftError(NO_REDRAFT)
    return sorted(set(ids))


def _check_files(root: Path, before: KbRepo, selected: list[str], commit: str, base_given: bool) -> None:
    """.kb/cache is the agent's to write, so the selection is checked against the base and the brief: every id
    is live at base, has a section in the brief, and (unless --base is given) the brief names the base used."""
    for rid in selected:
        try:
            check_named(before, rid)
        except RedraftError as e:
            raise RedraftError(f"{e} at the base {commit}; run `kb redraft` again") from None
    brief = (_read(root, REDRAFT_BRIEF) or b"").decode("utf-8", errors="replace")
    headings = set(brief.splitlines())
    m = _BRIEF_BASE.search(brief)
    if any(f"## {rid}" not in headings for rid in selected) or (not base_given and (m is None or m.group(1) != commit)):
        raise RedraftError(MISMATCH)


def _superseding(head: KbRepo, before: KbRepo, selected: set[str]) -> list[str]:
    """New ADRs/INVs at head whose `supersedes` names a selected record of their own type."""
    return sorted(
        rid for rid, rec in head.records.items()
        if isinstance(rec, (Decision, Invariant)) and rid not in before.records and rec.supersedes in selected
        and type(head.records.get(rec.supersedes)) is type(rec)
    )


def _cited(repos: list[KbRepo], ids: set[str]) -> set[str]:
    """'SRC-x#seg-NN' refs quoted by `ids` in any of `repos`."""
    return {
        q.source for repo in repos for rid in ids
        if (rec := repo.records.get(rid)) is not None and getattr(rec, "evidence", None) for q in rec.evidence.quotes
    }


def _triage_changes(head: KbRepo, before: KbRepo, src: str) -> list[str]:
    """Segment ids whose triage entry was added, changed or removed since base."""
    now, then = head.triage(src), before.triage(src)
    a, b = (now.segments if now else {}), (then.segments if then else {})
    return sorted(seg for seg in set(a) | set(b) if a.get(seg) != b.get(seg))


def _scope_issues(head: KbRepo, before: KbRepo, drafted: set[str], allowed: set[str], selected: set[str]) -> list[Issue]:
    """redraft.out_of_scope for drafted files that are not the selection's (a triage file only for entries of
    segments the selection does not cite); redraft.id_changed for selected records whose file is gone or now
    holds another id."""
    at_head, at_base = {p: r for r, p in head.id_paths}, {p: r for r, p in before.id_paths}
    cited = _cited([head, before], allowed)
    issues = []
    for path in sorted(drafted):
        rid = at_head.get(path) or at_base.get(path)
        if rid is None and (m := _TRIAGE.fullmatch(path)):
            issues.extend(
                Issue("redraft.out_of_scope", f"{m.group(1)}#{seg}: triage changed for a segment outside this redraft", path=path)
                for seg in _triage_changes(head, before, m.group(1)) if f"{m.group(1)}#{seg}" not in cited
            )
        elif rid is None:
            issues.append(Issue("redraft.out_of_scope", f"{path} is not in this redraft; redraft only the listed records", path=path))
        elif at_base.get(path) not in selected and rid not in allowed:
            issues.append(Issue("redraft.out_of_scope", f"{rid} is not in this redraft; redraft only the listed records",
                                path=path, record=rid))
    for rid in sorted(selected):
        path = before.paths.get(rid)
        if path is not None and at_head.get(path) != rid:
            issues.append(Issue("redraft.id_changed",
                                f"{rid} was deleted or renamed; a redraft keeps ids (set status: rejected instead)",
                                path=path, record=rid))
    return issues


def _flag_issues(head: KbRepo, before: KbRepo) -> list[Issue]:
    """vague.cleared_without_target, and flag.cleared for any other flag removed since base."""
    issues = []
    for rid, rec in sorted(head.records.items()):
        old = before.records.get(rid)
        if not isinstance(rec, Requirement) or not isinstance(old, Requirement):
            continue
        path = head.paths[rid]
        for flag in [f for f in old.flags if f not in rec.flags]:
            if flag != "vague":
                issues.append(Issue("flag.cleared", f"{rid}: {flag} was removed; reviewer to confirm", "warning", path, rid))
                continue
            quotes = rec.evidence.quotes if rec.evidence else []
            if rec.status != "rejected" and not any(_DIGIT.search(q.text) for q in quotes):
                issues.append(Issue("vague.cleared_without_target",
                                    f"{rid}: vague was removed but no quote states a measurable target", path=path, record=rid))
    return issues


def _config_issues(root: Path, ref: str) -> list[Issue]:
    if file_at(root, ref, CONFIG) == _read(root, CONFIG):
        return []
    return [Issue("ingest.config_changed", f"{CONFIG} changed; a redraft does not change the config", path=CONFIG)]


def _documents_changed(head: KbRepo, before: KbRepo) -> list[str]:
    return sorted(
        head.sources[s].original for s in set(head.sources) & set(before.sources)
        if head.sources[s].original_sha256 != before.sources[s].original_sha256
    )


def redraft_report(root: Path, base: str | None) -> RedraftReport:
    """Gate the redraft of the ids in REDRAFT_IDS against `base` (default: the commit in REDRAFT_BASE when it is
    still an ancestor of HEAD, else HEAD), and the errors it causes elsewhere. Store files are not drafted; a
    hand-edited store fails the source gate, which rejects the source and the records citing it."""
    root = Path(root)
    require_commit(root)
    selected = _selection(root)
    ref = base or recorded_base(root, REDRAFT_BASE) or "HEAD"
    commit = rev_parse(root, ref)
    if commit is None:
        raise GitError(f"unknown base {ref!r}")
    config = load_config(root / CONFIG)
    head, before = KbRepo.load(root), KbRepo.at_ref(root, ref)
    _check_files(root, before, selected, commit, base is not None)
    g = gate_drafts(root, config, base_config(root, ref), head, before, ref)

    chosen = set(selected)
    superseding = _superseding(head, before, chosen)
    sources = [i for i in g.issues if i.code.startswith("source.")] + deleted_sources(head, before, ref)
    flags = _flag_issues(head, before)
    rejected = (
        [i for i in g.mine if i.severity == "error"]
        + _scope_issues(head, before, g.drafted, chosen | set(superseding), chosen)
        + [i for i in flags if i.severity == "error"]
        + _config_issues(root, ref)
        + [i for i in sources if i.severity == "error" and i.code != "source.secret_in_original"]
        + tainted(head, sources)
        + g.caused
    )

    bad_paths = {i.path for i in rejected}
    bad = {i.record for i in rejected} | {rid for rid, path in [*head.id_paths, *before.id_paths] if path in bad_paths}
    touched = {rid for rid in selected if before.paths.get(rid) in g.drafted or head.paths.get(rid) in g.drafted}
    redrafted = sorted(touched - bad)
    accepted_superseding = [rid for rid in superseding if rid not in bad]
    replaced = {head.records[rid].supersedes for rid in accepted_superseding}  # type: ignore[union-attr]
    good = set(redrafted) | set(accepted_superseding)
    return RedraftReport(
        base=commit,
        selected=selected,
        redrafted=redrafted,
        superseding=accepted_superseding,
        rejected=rejected,
        flagged=[i for i in g.mine if i.severity == "warning" and i.record in good]
        + [i for i in flags if i.code == "flag.cleared"],
        unchanged=[rid for rid in selected if rid not in touched and rid not in replaced],
        still_flagged=[
            rid for rid in selected
            if isinstance(rec := head.records.get(rid), Requirement) and rec.flags and rec.status != "rejected"
        ],
        sources=sources,
        caused=g.caused,
        documents_changed=_documents_changed(head, before),
    )


def render_redraft_report(r: RedraftReport) -> str:
    """Markdown; every message and path is one-line inline code."""
    direct = [i for i in r.rejected if i not in r.caused]
    out = ["# Redraft report", "", f"Base: {r.base}", f"Selection: {', '.join(r.selected)}", ""]
    out += section("Redrafted", [f"- {rid}" for rid in r.redrafted]
                   + [f"- {rid} (supersedes a selected record)" for rid in r.superseding])
    out += [f"## Rejected ({len(r.rejected)})", "", *(issue_lines(direct) or ["- none"]), ""]
    if r.caused:
        out += section("Caused by this change", issue_lines(r.caused), "###")
    out += section("Flagged", issue_lines(r.flagged))
    out += section("Sources", issue_lines(r.sources))
    if r.documents_changed:
        out += ["Documents changed since base: " + ", ".join(inline_code(p) for p in r.documents_changed), ""]
    out += section("Still flagged", [f"- {rid}" for rid in r.still_flagged])
    out += section("Not redrafted", [f"- {rid}" for rid in r.unchanged])
    return "\n".join(out)
