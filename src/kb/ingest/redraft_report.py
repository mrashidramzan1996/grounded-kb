"""`kb redraft --report`: gate what the agent redrafted since the brief was written (A-17)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from kb.config.load import load_config
from kb.gates.base import GateContext, run_gates
from kb.gitfiles import GitError, require_commit, rev_parse
from kb.ingest.brief import recorded_base
from kb.ingest.redraft import REDRAFT_BASE, REDRAFT_IDS, RedraftError
from kb.gates.sources import recover_hint, stored_ids
from kb.ingest.report import CONFIG, GATES, base_config, drafted_paths, issue_lines, section, tainted
from kb.issues import Issue
from kb.records.ids import ADR_ID, INV_ID, REQ_ID
from kb.records.models import Decision, Invariant, Requirement
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath, read_regular

_SELECTABLE = re.compile(rf"{REQ_ID}|{ADR_ID}|{INV_ID}")
_DIGIT = re.compile(r"\d")
NO_REDRAFT = f"no redraft selection in {REDRAFT_IDS}; run `kb redraft` first"


@dataclass
class RedraftReport:
    base: str
    selected: list[str]
    redrafted: list[str]  # selected ids whose file changed and that have no rejection
    superseding: list[str]  # new ADR/INV ids whose `supersedes` names a selected id, with no rejection
    rejected: list[Issue]
    flagged: list[Issue]  # warnings on redrafted/superseding records, plus "flag.cleared" notices
    unchanged: list[str]  # selected ids not touched (and not superseded by an accepted record)
    still_flagged: list[str]  # selected requirements that still carry flags
    sources: list[Issue] = field(default_factory=list)  # every source issue; its errors are also in `rejected`


def _selection(root: Path) -> list[str]:
    """The ids `kb redraft` wrote; RedraftError when the file is missing or is not a list of record ids."""
    try:
        data = read_regular(root, REDRAFT_IDS, max_bytes=1_000_000)
    except UnsafePath:
        data = None
    try:
        ids = (data or b"").decode("ascii").split()
    except UnicodeDecodeError:
        ids = []
    if not ids or not all(_SELECTABLE.fullmatch(i) for i in ids):
        raise RedraftError(NO_REDRAFT)
    return sorted(set(ids))


def _superseding(head: KbRepo, before: KbRepo, selected: set[str]) -> list[str]:
    """New ADRs/INVs at head whose `supersedes` names a selected record of their own type."""
    return sorted(
        rid for rid, rec in head.records.items()
        if isinstance(rec, (Decision, Invariant)) and rid not in before.records and rec.supersedes in selected
        and type(head.records.get(rec.supersedes)) is type(rec)
    )


def _scope_issues(head: KbRepo, before: KbRepo, drafted: set[str], allowed: set[str], selected: set[str]) -> list[Issue]:
    """redraft.out_of_scope for drafted record files that are not the selection's; redraft.id_changed for
    selected records whose file is gone or now holds another id."""
    at_head, at_base = {p: r for r, p in head.id_paths}, {p: r for r, p in before.id_paths}
    issues = []
    for path in sorted(drafted):
        rid = at_head.get(path) or at_base.get(path)
        if rid is None or at_base.get(path) in selected or rid in allowed:
            continue
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


def _source_issues(head: KbRepo, before: KbRepo, issues: list[Issue], ref: str) -> list[Issue]:
    """The source gate's issues, plus source.tampered for a stored source deleted since base. A store edited
    by hand fails the source gate (it is re-derived from the original); a `kb source sync` does not."""
    deleted = [
        Issue("source.tampered", f"kb/sources/{s} was deleted; {recover_hint(ref)}", path=f"kb/sources/{s}", record=s)
        for s in sorted(stored_ids(before) - stored_ids(head))
    ]
    return [i for i in issues if i.code.startswith("source.")] + deleted


def redraft_report(root: Path, base: str | None) -> RedraftReport:
    """Gate the redraft of the ids in REDRAFT_IDS against `base` (default: the commit in REDRAFT_BASE when it is
    still an ancestor of HEAD, else HEAD). Only issues on drafted paths are reported; store files are not drafted."""
    root = Path(root)
    require_commit(root)
    selected = _selection(root)
    ref = base or recorded_base(root, REDRAFT_BASE) or "HEAD"
    commit = rev_parse(root, ref)
    if commit is None:
        raise GitError(f"unknown base {ref!r}")
    config = load_config(root / CONFIG)
    head, before = KbRepo.load(root), KbRepo.at_ref(root, ref)
    issues = run_gates(GateContext(root, config, head, before, ref), GATES)

    drafted = drafted_paths(head, before)
    chosen = set(selected)
    superseding = _superseding(head, before, chosen)
    ids = {rid for rid, path in head.id_paths if path in drafted}
    mine = [i for i in issues if (i.record in ids or i.path in drafted) and not i.code.startswith("source.")]
    sources = _source_issues(head, before, issues, ref)
    flags = _flag_issues(head, before)
    old_config = base_config(root, ref)
    config_issues = [] if old_config is not None and old_config.model_dump() == config.model_dump() else [
        Issue("ingest.config_changed", f"{CONFIG} changed; a redraft does not change the config", path=CONFIG)
    ]
    rejected = (
        [i for i in mine if i.severity == "error"]
        + _scope_issues(head, before, drafted, chosen | set(superseding), chosen)
        + [i for i in flags if i.severity == "error"]
        + config_issues
        + [i for i in sources if i.severity == "error" and i.code != "source.secret_in_original"]
        + tainted(head, sources)
    )

    bad_paths = {i.path for i in rejected}
    bad = {i.record for i in rejected} | {rid for rid, path in [*head.id_paths, *before.id_paths] if path in bad_paths}
    touched = {rid for rid in selected if before.paths.get(rid) in drafted or head.paths.get(rid) in drafted}
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
        flagged=[i for i in mine if i.severity == "warning" and i.record in good]
        + [i for i in flags if i.code == "flag.cleared"],
        unchanged=[rid for rid in selected if rid not in touched and rid not in replaced],
        still_flagged=[
            rid for rid in selected
            if isinstance(rec := head.records.get(rid), Requirement) and rec.flags and rec.status != "rejected"
        ],
        sources=sources,
    )


def render_redraft_report(r: RedraftReport) -> str:
    """Markdown; every message and path is one-line inline code."""
    out = ["# Redraft report", ""]
    out += section("Redrafted", [f"- {rid}" for rid in r.redrafted]
                   + [f"- {rid} (supersedes a selected record)" for rid in r.superseding])
    out += section("Rejected", issue_lines(r.rejected))
    out += section("Flagged", issue_lines(r.flagged))
    out += section("Sources", issue_lines(r.sources))
    out += section("Still flagged", [f"- {rid}" for rid in r.still_flagged])
    out += section("Not redrafted", [f"- {rid}" for rid in r.unchanged])
    return "\n".join(out)
