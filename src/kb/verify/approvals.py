"""The missing_owner_approval rule: who must approve a PR, and whether they did."""

from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import Path

from kb.config.codeowners import owners_for, parse_codeowners
from kb.config.load import CONFIG
from kb.config.schema import KbConfig
from kb.config.validate import modules_for, resolve_owner
from kb.gitfiles import file_at, tree_symlinks
from kb.init.scaffold import CI_WORKFLOWS, CODEOWNERS_LOCATIONS
from kb.issues import Issue
from kb.ports.forge import Forge, PullRequest, Review
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath, read_regular
from kb.verify.changes import ChangeSet
from kb.verify.engine import Verdict

# GitHub uses the first CODEOWNERS found in this order: .github/, then the root, then docs/.
_CODEOWNERS = tuple(p for p in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS") if p in CODEOWNERS_LOCATIONS)
_SHOWN_PATHS = 3
CodeownersRules = list[tuple[str, list[str]]]
_READ_NOW = object()  # required_owners default: read CODEOWNERS from the working tree when called
# required_owners' handle when the policy names no decision approvers: no login matches it, so it fails closed
NO_DECISION_APPROVERS = "(no decision approvers)"
_INTENT_DIRS = ("kb/requirements/", "kb/features/", "kb/glossary/")
_DECISION_DIRS = ("kb/decisions/", "kb/invariants/", "kb/components/")


def codeowners_rules(root: Path) -> CodeownersRules | None:
    """The first CODEOWNERS file's rules (GitHub's lookup order), or None when there is none. `kb verify` reads them
    before the project's tests run, so a test that rewrites CODEOWNERS cannot change who must approve."""
    for rel in _CODEOWNERS:
        try:
            data = read_regular(root, rel)
        except UnsafePath:
            continue
        if data is not None:
            return parse_codeowners(data.decode("utf-8", errors="replace"))
    return None


def codeowners_rules_at(root: Path, ref: str) -> CodeownersRules | None:
    """codeowners_rules as committed at `ref`: the first CODEOWNERS in GitHub's lookup order, skipping symlink
    entries; None when there is none."""
    for rel in _CODEOWNERS:
        if rel in tree_symlinks(root, ref, rel):
            continue
        data = file_at(root, ref, rel)
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
    *,
    rules: "CodeownersRules | None | object" = _READ_NOW,
) -> dict[str, list[str]]:
    """{owner handle: reasons}. `config` is the approval policy's config (kb.verify.policy: the target branch tip's), so its
    approvers, teams and modules decide, not the change's. `changes` None (no base): no changed files; records count
    as changed against `base_repo` (every record when that is None too). `rules`: the policy's CODEOWNERS rules taken
    earlier (None: no CODEOWNERS, module owners apply); left out, CODEOWNERS is read now. A change to kb.config.yaml,
    a CODEOWNERS file or the kb CI workflow needs the decision approvers; with none named, NO_DECISION_APPROVERS, which never approves."""
    reasons: dict[str, set[str]] = {}

    def need(handle: str, reason: str) -> None:
        reasons.setdefault(handle, set()).add(reason)

    if rules is _READ_NOW:
        rules = codeowners_rules(root)
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
    policy_files = (CONFIG, *CODEOWNERS_LOCATIONS, *CI_WORKFLOWS)  # the gate: who decides, and what runs it
    for path in sorted(p for p in (changes.files if changes else ()) if p in policy_files):
        for h in decisions or [NO_DECISION_APPROVERS]:
            need(h, f"{path} changed")
    for rid, path in _changed_records(repo, base_repo).items():
        if path.startswith("kb/requirements/"):
            for h in intent:
                need(h, f"requirement {rid} changed")
        elif path.startswith("kb/features/"):
            for h in intent:
                need(h, f"feature {rid} changed")
        elif path.startswith("kb/glossary/"):
            for h in intent:
                need(h, f"glossary term {rid} changed")
        elif path.startswith("kb/components/"):
            for h in decisions:
                need(h, f"component {rid} changed")
        elif path.startswith("kb/decisions/"):
            for h in decisions:
                need(h, f"decision {rid} changed")
        elif path.startswith("kb/invariants/"):
            for h in decisions:
                need(h, f"invariant {rid} changed")
    # a changed record file that does not parse is in no record map: route it by its path, so it cannot dodge approval
    parsed = set(_changed_records(repo, base_repo).values())
    for path in sorted(changes.files if changes and base_repo is not None else ()):
        if path in parsed:
            continue
        if path.startswith(_INTENT_DIRS):
            for h in intent:
                need(h, f"{path} changed")
        elif path.startswith(_DECISION_DIRS):
            for h in decisions:
                need(h, f"{path} changed")
    for v in verdicts:
        if v.kind == "requirement" and v.status == "stale":
            for h in intent:
                need(h, f"requirement {v.id} is stale")

    return {h: _ordered(rs) for h, rs in sorted(reasons.items())}


_GROUP_RECORDS = {"intent": "requirements, features and glossary terms", "decisions": "decisions, invariants and components"}


def no_approver_warnings(
    config: KbConfig, repo: KbRepo, base_repo: KbRepo | None, changes: ChangeSet | None, verdicts: Sequence[Verdict]
) -> list[Issue]:
    """One owner_approval.no_approvers warning per approver group (policy.approvers.intent / decisions) that names
    nobody while the change has records it would have to approve (as required_owners routes them): those changes
    need no approval, which should not pass silently. A config or CODEOWNERS change with no decision approvers is
    not one of them: it fails closed (NO_DECISION_APPROVERS)."""
    paths = set(_changed_records(repo, base_repo).values())
    if changes and base_repo is not None:  # record files that do not parse, routed by path
        paths |= {p for p in changes.files if p.startswith((*_INTENT_DIRS, *_DECISION_DIRS))}
    changed = {
        "intent": any(p.startswith(_INTENT_DIRS) for p in paths)
        or any(v.kind == "requirement" and v.status == "stale" for v in verdicts),
        "decisions": any(p.startswith(_DECISION_DIRS) for p in paths),
    }
    return [
        Issue("owner_approval.no_approvers", f"{group} approvers are empty in the approval policy; changes to "
              f"{what} need no approval", "warning")
        for group, what in _GROUP_RECORDS.items()
        if changed[group] and not _handles(getattr(config.policy.approvers, group), config)
    ]


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


def owner_approval(
    forge: Forge, pr: PullRequest, config: KbConfig, required: dict[str, list[str]], *, bootstrap: bool = False
) -> list[Issue]:
    """`bootstrap`: the policy is the change's own (the target branch has no kb.config.yaml yet), so a missing
    approver is set in this change, not on the default branch first."""
    if not required:
        return []
    approved = _approvers(forge.reviews(pr), pr, config)
    issues: list[Issue] = []
    unreadable: list[str] = []
    for handle in sorted(required):
        if handle == NO_DECISION_APPROVERS:  # no approval can satisfy it
            where = "in this change" if bootstrap else "on the default branch first"
            issues.append(Issue("missing_owner_approval", f"{', '.join(required[handle])}, but the approval policy names "
                                f"no decision approvers; set policy.approvers.decisions {where}"))
            continue
        if "/" in handle:
            members = forge.team_members(handle)
            if members is None:
                members = {
                    m
                    for t in config.teams.values()
                    if t.git_team.lower() == handle.lower()
                    for m in t.members
                } or None
            if members is None:
                if config.policy.unreadable_teams == "defer" and approved:
                    unreadable.append(handle)  # defer to branch protection's required code-owner review
                    continue
                why = ", ".join(required[handle])
                issues.append(Issue("missing_owner_approval", f"{handle} has not approved {pr.head_sha[:7]}: cannot read "
                                    f"the members of {handle}; list them under teams.<name>.members in kb.config.yaml "
                                    "on the base branch, or give the CI token read access to the organisation's "
                                    f"teams ({why})"))
                continue
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
