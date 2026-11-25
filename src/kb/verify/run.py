"""`kb verify`: the schema gates, then the derived verification states, stale context and owner approval."""

from dataclasses import replace
from pathlib import Path

from kb.cache import write_cache
from kb.check import run_check
from kb.config.load import load_config
from kb.gitfiles import (
    GitError,
    branch_tip,
    default_branch_tip,
    head_commit,
    merge_base_of,
)
from kb.issues import Issue
from kb.ports.forge import Forge, PullRequest
from kb.records.repo import KbRepo
from kb.testrun import run_tests
from kb.verify.affected import affected
from kb.verify.approvals import owner_approval, required_owners
from kb.verify.changes import compute_changes
from kb.verify.engine import EngineInput, Verdict, derive, snapshot, tests_needed
from kb.verify.index import INDEX_PATH, build_index
from kb.verify.policy import load_policy
from kb.verify.report import VerifyReport, blocking_issues
from kb.verify.stale_context import stale_context

Branch = tuple[str, str]  # (the branch as shown, its tip commit): gitfiles.branch_tip


def _branch(root: Path, name: str) -> Branch:
    """branch_tip, or the GitError (with the fetch-depth hint) when no branch has that name."""
    found = branch_tip(root, name)
    if found is None:
        raise GitError(f"cannot find the base branch '{name}'; check the base name, fetch the base branch, "
                       "or use fetch-depth: 0 in CI")
    return found


def _bases(root: Path, base: str | None, all_: bool) -> tuple[Branch | None, str | None]:
    """(base branch, merge-base commit). No base given: the default branch, unless --all; with no default
    branch at all, (None, None) and every claim is checked. A default branch without a merge-base (a shallow
    clone) is a GitError with the fetch-depth hint, not a silent full run. The branch is looked up among branches
    only (a tag of the same name is a GitError) and everything after uses its commit, never the name."""
    if base:
        branch = _branch(root, base)
    elif not all_ and (default := default_branch_tip(root)) is not None:
        branch = default
    else:
        return None, None
    return branch, merge_base_of(root, branch[1], branch[0])


def _policy_branch(root: Path, base_branch: Branch | None, pr: PullRequest | None) -> Branch | None:
    """The branch whose tip holds the approval policy: the base branch; with none (--all, or no default branch) and a
    pull request, the branch it targets (as the forge names it, else the default branch). None: no PR and no base,
    the working tree's policy. A PR whose target cannot be found is a GitError, never the working tree's policy."""
    if base_branch is not None or pr is None:
        return base_branch
    if pr.base is not None:
        found = branch_tip(root, pr.base)
        if found is None:
            raise GitError(f"cannot find the pull request's base branch {pr.base!r} to read the approval policy; fetch it")
        return found
    branch = default_branch_tip(root)
    if branch is None:
        raise GitError("a pull request is checked but there is no base branch to read the approval policy from; "
                       "fetch the default branch or pass --base")
    return branch


def verdict_issues(verdicts: list[Verdict], repo: KbRepo) -> list[Issue]:
    """broken: an error; stale and unverified: warnings. The path is the record's file (a claim's feature)."""
    feature_of = {c.id: f.id for f, c in repo.claims()}
    out = []
    for v in verdicts:
        if v.status == "broken":
            code, severity = f"{v.kind}.broken", "error"
        elif v.status == "stale":
            code, severity = "requirement.stale", "warning"
        elif v.status == "unverified":
            code, severity = f"{v.kind}.unverified", "warning"
        else:
            continue
        rid = feature_of.get(v.id, v.id) if v.kind == "claim" else v.id
        message = f"{v.status}: {'; '.join(v.reasons)}" if v.reasons else v.status
        out.append(Issue(code, message, severity, path=repo.paths.get(rid), record=v.id))
    return out


def run_verify(root: Path, *, base: str | None, all_: bool, forge: Forge | None) -> VerifyReport:
    """Raises ConfigError, GitError, TestRunError, ForgeError; the caller maps them to exit 2."""
    root = Path(root)
    config = load_config(root / "kb.config.yaml")
    base_branch, base_ref = _bases(root, base, all_)
    pr = forge.pull_request() if forge else None
    # Who must approve, what blocks and the mode come from the target branch's tip (ConfigError/GitError when it
    # cannot be read), so the change under review cannot loosen them, not even by branching from an old commit; the
    # working tree's config drives everything else. The merge-base stays the base of the diff.
    policy = load_policy(root, _policy_branch(root, base_branch, pr), config)

    check = run_check(root, base=base_branch[1] if base_branch else None)  # the schema gates, and decision_guard when there is a base
    repo = KbRepo.load(root)
    base_repo = KbRepo.at_ref(root, base_ref) if base_ref else None
    changes = compute_changes(root, base_ref, head_repo=repo) if base_ref else None
    index = build_index(root, config)
    write_cache(root, INDEX_PATH, index.to_json())
    hit = affected(None if all_ else changes, config, repo, index, base_repo)
    stale = (
        stale_context(root, config, repo, base_branch[0], base_ref, list(changes.files), tip=base_branch[1])
        if base_branch and base_ref and changes is not None else []
    )
    # The project's tests are untrusted code: everything read from the tree or the forge's event is read first
    # (the PR, policy, config, repos, changes and index above; HEAD and the parsed head files here).
    head = head_commit(root)
    inp = snapshot(EngineInput(root, config, repo, base_repo, changes, index, {}), hit)
    inp = replace(inp, outcomes=run_tests(root, config, tests_needed(inp, hit)))
    verdicts = derive(inp, hit)

    issues = check.issues + verdict_issues(verdicts, repo) + stale
    required = None
    if forge and pr:
        required = required_owners(root, policy.config, repo, base_repo, changes, verdicts, rules=policy.codeowners)
        issues += owner_approval(forge, pr, policy.config, required, bootstrap=policy.bootstrap)

    issues = sorted(issues, key=lambda i: (*i.sort_key, i.message))
    return VerifyReport(
        base_ref=base_ref,
        head=head,
        verdicts=verdicts,
        issues=issues,
        blocking=blocking_issues(issues, list(policy.config.policy.fail_on)),
        mode=policy.config.policy.mode,
        scope="all" if all_ or changes is None else "affected",
        pr=pr,
        required=required,
        max_items=config.review.max_items_per_pr,
        policy_source=policy.source,
    )
