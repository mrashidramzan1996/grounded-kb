"""What `kb verify` found, and its three renderings: text, JSON (status.json) and the PR comment.

Everything rendered here is deterministic: no times, durations, absolute paths or log tails.
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kb.cache import write_cache
from kb.issues import Issue
from kb.ports.forge import COMMENT_MARKER, PullRequest
from kb.verify.engine import Verdict

STATUS_PATH = ".kb/cache/status.json"
COMMENT_PATH = ".kb/cache/verify.md"

VERDICT_CODES = frozenset({
    "claim.broken", "requirement.broken", "invariant.broken",
    "requirement.stale",
    "claim.unverified", "requirement.unverified", "invariant.unverified",
})
APPROVAL_CODES = frozenset({"missing_owner_approval", "owner_approval.deferred"})
_OWN_CATEGORY = frozenset({"decision_guard", "missing_owner_approval", "stale_context"})
Scope = Literal["all", "affected"]
COMMENT_LIMIT = 60_000  # characters; GitHub refuses comment bodies over 65,536
TRUNCATED = "… comment truncated; see the status.json artifact"
_STATUS_ORDER = {"broken": 0, "stale": 1, "unverified": 2}


@dataclass
class VerifyReport:
    base_ref: str | None  # merge-base commit, None with --all and no base
    head: str  # HEAD commit
    verdicts: list[Verdict]
    issues: list[Issue]  # run_check issues + verdict issues + stale_context + approval issues, sorted
    blocking: list[Issue]
    mode: str
    scope: Scope = "affected"  # "all": every live claim was checked (--all, or no base resolved)
    pr: PullRequest | None = None  # the forge's pull request, when there is one
    required: dict[str, list[str]] | None = None  # owners who must approve; None when not checked (no PR)
    max_items: int = 40  # config.review.max_items_per_pr: rows in each of the comment's lists


def category(issue: Issue) -> str | None:
    """The `policy.fail_on` category of an error issue; None for warnings."""
    if issue.severity != "error":
        return None
    if issue.code.endswith(".broken"):
        return "broken"
    if issue.code in _OWN_CATEGORY:
        return issue.code
    return "schema"


def blocking_issues(issues: list[Issue], fail_on: list[str]) -> list[Issue]:
    return [i for i in issues if category(i) in fail_on]


def _counts(r: VerifyReport) -> dict[str, int]:
    statuses = Counter(v.status for v in r.verdicts)
    errors = sum(i.severity == "error" for i in r.issues)
    return {
        "broken": statuses["broken"],
        "stale": statuses["stale"],
        "unverified": statuses["unverified"],
        "verified": statuses["verified"],
        "built": statuses["built"],
        "not_built": statuses["not_built"],
        "errors": errors,
        "warnings": len(r.issues) - errors,
    }


def _is_verdict_issue(r: VerifyReport, i: Issue) -> bool:
    return i.code in VERDICT_CODES and i.record in {v.id for v in r.verdicts}


def summary_line(r: VerifyReport) -> str:
    c = _counts(r)
    return (
        f"{c['broken']} broken, {c['stale']} stale, {c['unverified']} unverified, {c['verified']} verified, "
        f"{c['built']} built, {c['not_built']} not_built; {len(r.blocking)} blocking (mode: {r.mode})"
    )


def render_text(r: VerifyReport) -> str:
    """One line per verdict that is not verified, then the other issues as `kb check` prints them."""
    lines = []
    for v in r.verdicts:
        if v.status != "verified":
            reasons = " ".join("; ".join(v.reasons).split())
            lines.append(f"{v.status} {v.id} {v.level}" + (f": {reasons}" if reasons else ""))
    for i in r.issues:
        if not _is_verdict_issue(r, i):
            lines.append(f"{i.severity} {i.code} {i.path or '-'} {i.record or '-'}: {' '.join(i.message.split())}")
    return "".join(f"{ln}\n" for ln in lines)


def _issue_json(i: Issue) -> dict:
    return {"code": i.code, "severity": i.severity, "path": i.path, "record": i.record, "message": i.message}


def render_json(r: VerifyReport) -> str:
    data = {
        "base": r.base_ref,
        "head": r.head,
        "mode": r.mode,
        "scope": r.scope,
        "verdicts": [
            {
                "id": v.id, "kind": v.kind, "status": v.status, "level": v.level,
                "notes": list(v.notes), "reasons": list(v.reasons), "tests": list(v.tests),
            }
            for v in r.verdicts
        ],
        "issues": [_issue_json(i) for i in r.issues],
        "blocking": [{"code": i.code, "record": i.record} for i in r.blocking],
        "summary": _counts(r),
    }
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


_MD_ESCAPES = str.maketrans({c: "\\" + c for c in "\\|[]!"} | {"<": "&lt;"})


def _md(text: str) -> str:
    """One line of text, safe inside a Markdown table cell: it cannot fake the comment marker or, from PR-controlled
    text, make a link or an image."""
    return " ".join(text.split()).translate(_MD_ESCAPES)


def _issue_md(i: Issue) -> str:
    where = " ".join(f"`{_md(x)}`" for x in (i.path, i.record) if x)
    return f"- {i.severity} **{_md(i.code)}**" + (f" {where}" if where else "") + f": {_md(i.message)}"


def _capped(lines: list[str], cap: int) -> list[str]:
    return lines[:cap] + (["", f"… and {len(lines) - cap} more"] if len(lines) > cap else [])


def _fit(lines: list[str]) -> str:
    """The comment body, cut at a line boundary to stay within COMMENT_LIMIT characters."""
    body = "\n".join(lines) + "\n"
    if len(body) <= COMMENT_LIMIT:
        return body
    budget, kept, size = COMMENT_LIMIT - len(TRUNCATED) - 2, [], 0
    for line in lines:
        if size + len(line) + 1 > budget:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept) + "\n\n" + TRUNCATED + "\n"


def render_markdown(r: VerifyReport) -> str:
    c = _counts(r)
    out = [
        COMMENT_MARKER,
        (
            f"**kb verify**: {c['broken']} broken · {c['stale']} stale · {c['unverified']} unverified · "
            f"{c['verified']} verified · {c['built']} built · {c['not_built']} not_built (mode: {r.mode})"
        ),
        "",
        "### Blocking",
        "",
    ]
    out += _capped([_issue_md(i) for i in r.blocking], r.max_items) or ["None."]

    out += ["", "### Claims", ""]
    ordered = sorted(r.verdicts, key=lambda v: _STATUS_ORDER.get(v.status, len(_STATUS_ORDER)))  # stable
    if ordered:
        out += ["| ID | status | level | notes | reason |", "|---|---|---|---|---|"]
        out += _capped(
            [f"| {_md(v.id)} | {v.status} | {v.level} | {', '.join(v.notes)} | {_md('; '.join(v.reasons))} |"
             for v in ordered],
            r.max_items,
        )
    else:
        out.append("No affected claims.")

    out += ["", "### Approvals", ""]
    approval_issues = [i for i in r.issues if i.code in APPROVAL_CODES]
    if r.required is None:
        out.append("Not checked: no pull request.")
    elif not r.required:
        out.append("No owner approval required.")
    else:
        out += [f"- `{_md(h)}`: {_md(', '.join(reasons))}" for h, reasons in r.required.items()]
        out += ["", *(_issue_md(i) for i in approval_issues)] if approval_issues else ["", "All required owners approved."]

    out += ["", "### Other issues", ""]
    blocking = set(r.blocking)
    other = [
        i for i in r.issues
        if i not in blocking and i.code not in APPROVAL_CODES and not _is_verdict_issue(r, i)
    ]
    out += _capped([_issue_md(i) for i in other], r.max_items) or ["None."]
    return _fit(out)


def write_outputs(root: Path, r: VerifyReport) -> None:
    """status.json (as render_json) and the comment body, under .kb/cache."""
    write_cache(root, STATUS_PATH, render_json(r))
    write_cache(root, COMMENT_PATH, render_markdown(r))
