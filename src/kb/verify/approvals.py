"""The missing_owner_approval rule: who must approve a PR, and whether they did."""

from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import Path

from kb.config.codeowners import owners_for, parse_codeowners
from kb.config.schema import KbConfig
from kb.config.validate import modules_for, resolve_owner
from kb.init.scaffold import CODEOWNERS_LOCATIONS
from kb.issues import Issue
from kb.ports.forge import Forge, PullRequest, Review
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath, read_regular
from kb.verify.changes import ChangeSet
from kb.verify.engine import Verdict

# GitHub uses the first CODEOWNERS found in this order: .github/, then the root, then docs/.
_CODEOWNERS = tuple(p for p in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS") if p in CODEOWNERS_LOCATIONS)
_SHOWN_PATHS = 3


def _codeowners_rules(root: Path):
    for rel in _CODEOWNERS:
        try:
            data = read_regular(root, rel)
        except UnsafePath:
            continue
        if data is not None:
            return parse_codeowners(data.decode("utf-8", errors="replace"))
    return None


def _file_owners(path: str, rules, config: KbConfig) -> set[str]:
    if rules is not None:
        raw = owners_for(path, rules)
    else:
        owners = {m.id: m.owner for m in config.modules}
        raw = [owners[mid] for mid in modules_for(path, config)]
    return {h for o in raw if (h := resolve_owner(o, config))}


def _changed_records(repo: KbRepo, base_repo: KbRepo | None) -> dict[str, str]:
    """{record id: path} of records new, changed or deleted relative to the base."""
    out: dict[str, str] = {}
    base_raw = base_repo.raw if base_repo else {}
    for rid, path in repo.paths.items():
        if base_raw.get(path) != repo.raw.get(path):
            out[rid] = path
    if base_repo:
        for rid, path in base_repo.paths.items():
            if rid not in repo.paths and path not in repo.raw:
                out[rid] = path
    return out


def _handles(entries: Sequence[str], config: KbConfig) -> list[str]:
    return [h for e in entries if (h := resolve_owner(e, config))]


def required_owners(
    root: Path,
    config: KbConfig,
    repo: KbRepo,
    base_repo: KbRepo | None,
    changes: ChangeSet | None,
    verdicts: Sequence[Verdict],
) -> dict[str, list[str]]:
    """{owner handle: reasons}. `changes` None (no base): no changed files; records count as changed against
    `base_repo` (every record when that is None too)."""
    reasons: dict[str, set[str]] = {}

    def need(handle: str, reason: str) -> None:
        reasons.setdefault(handle, set()).add(reason)

    rules = _codeowners_rules(root)
    by_owner: dict[str, list[str]] = {}
    for path in sorted(changes.files if changes else ()):
        for h in _file_owners(path, rules, config):
            by_owner.setdefault(h, []).append(path)
    for h, paths in by_owner.items():
        for p in paths[:_SHOWN_PATHS]:
            need(h, f"changes {p}")
        if len(paths) > _SHOWN_PATHS:
            need(h, f"… and {len(paths) - _SHOWN_PATHS} more")

    intent = _handles(config.policy.approvers.intent, config)
    decisions = _handles(config.policy.approvers.decisions, config)
    for rid, path in _changed_records(repo, base_repo).items():
        if path.startswith("kb/requirements/"):
            for h in intent:
                need(h, f"requirement {rid} changed")
        elif path.startswith("kb/decisions/"):
            for h in decisions:
                need(h, f"decision {rid} changed")
        elif path.startswith("kb/invariants/"):
            for h in decisions:
                need(h, f"invariant {rid} changed")
    for v in verdicts:
        if v.kind == "requirement" and v.status == "stale":
            for h in intent:
                need(h, f"requirement {v.id} is stale")

    return {h: _ordered(rs) for h, rs in sorted(reasons.items())}


def _ordered(reasons: set[str]) -> list[str]:
    """Sorted, with the "… and N more" collapse marker last."""
    return sorted(reasons, key=lambda r: (r.startswith("… and"), r))


def _literal_brackets(glob: str) -> str:
    """Bot globs like "*[bot]" mean the literal text; only * and ? are wildcards."""
    return glob.replace("[", "[[]")


def _approvers(reviews: list[Review], pr: PullRequest, config: KbConfig) -> set[str]:
    latest: dict[str, Review] = {}
    for r in reviews:
        if r.state == "COMMENTED":
            continue
        if r.login not in latest or r.submitted_at >= latest[r.login].submitted_at:
            latest[r.login] = r
    return {
        login.lower()
        for login, r in latest.items()
        if r.state == "APPROVED"
        and r.commit_id == pr.head_sha
        and login.lower() != pr.author.lower()
        and not any(fnmatchcase(login, _literal_brackets(g)) for g in config.bots)
    }


def owner_approval(forge: Forge, pr: PullRequest, config: KbConfig, required: dict[str, list[str]]) -> list[Issue]:
    if not required:
        return []
    approved = _approvers(forge.reviews(pr), pr, config)
    issues: list[Issue] = []
    unreadable: list[str] = []
    for handle in sorted(required):
        if "/" in handle:
            members = forge.team_members(handle)
            if members is None:
                if approved:  # some approval counts, but its team cannot be checked: defer to branch protection
                    unreadable.append(handle)
                    continue
                members = set()
            satisfied = bool({m.lower() for m in members} & approved)
        else:
            satisfied = handle.lstrip("@").lower() in approved
        if not satisfied:
            why = ", ".join(required[handle])
            issues.append(Issue("missing_owner_approval", f"{handle} has not approved {pr.head_sha[:7]} ({why})"))
    if unreadable:
        issues.append(
            Issue(
                "owner_approval.deferred",
                f"cannot read team membership for {', '.join(unreadable)}; "
                "relying on branch protection's required code-owner review",
                "warning",
            )
        )
    return issues
