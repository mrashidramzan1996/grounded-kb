"""`kb verify`: the schema gates, then the derived verification states, stale context and owner approval."""

from dataclasses import replace
from pathlib import Path

from kb.cache import write_cache
from kb.check import run_check
from kb.config.load import load_config
from kb.gitfiles import (
    find_default_branch,
    head_commit,
    merge_base,
    rev_parse,
)
from kb.issues import Issue
from kb.ports.forge import Forge
from kb.records.repo import KbRepo
from kb.testrun import run_tests
from kb.verify.affected import affected
from kb.verify.approvals import codeowners_rules, owner_approval, required_owners
from kb.verify.changes import compute_changes
from kb.verify.engine import EngineInput, Verdict, derive, snapshot, tests_needed
from kb.verify.index import INDEX_PATH, build_index
from kb.verify.report import VerifyReport, blocking_issues
from kb.verify.stale_context import stale_context


def _bases(root: Path, base: str | None, all_: bool) -> tuple[str | None, str | None]:
    """(base branch name, merge-base commit). No base given: the default branch, unless --all; with no default
    branch at all, (None, None) and every claim is checked. A default branch without a merge-base (a shallow
    clone) is a GitError with the fetch-depth hint, not a silent full run."""
    if base:
        ref = merge_base(root, base)  # GitError, with the fetch-depth hint, when there is none
        # merge_base falls back to origin/<base> (a CI checkout has no local base branch): so do we
        return (base if rev_parse(root, base) is not None else f"origin/{base}"), ref
    if not all_:
        branch = find_default_branch(root)
        if branch is not None:
            return branch, merge_base(root, branch)
    return None, None


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

    check = run_check(root, base=base_branch)  # the schema gates, and decision_guard when there is a base
    repo = KbRepo.load(root)
    base_repo = KbRepo.at_ref(root, base_ref) if base_ref else None
    changes = compute_changes(root, base_ref, head_repo=repo) if base_ref else None
    index = build_index(root, config)
    write_cache(root, INDEX_PATH, index.to_json())
    hit = affected(None if all_ else changes, config, repo, index, base_repo)
    stale = (
        stale_context(root, config, repo, base_branch, base_ref, list(changes.files))
        if base_branch and base_ref and changes is not None else []
    )
    # The project's tests are untrusted code: everything read from the tree or the forge's event is read first
    # (config, repos, changes and index above; the PR, HEAD, CODEOWNERS and the parsed head files here).
    pr = forge.pull_request() if forge else None
    head = head_commit(root)
    owners_rules = codeowners_rules(root) if forge and pr else None
    inp = snapshot(EngineInput(root, config, repo, base_repo, changes, index, {}), hit)
    inp = replace(inp, outcomes=run_tests(root, config, tests_needed(inp, hit)))
    verdicts = derive(inp, hit)

    issues = check.issues + verdict_issues(verdicts, repo) + stale
    required = None
    if forge and pr:
        required = required_owners(root, config, repo, base_repo, changes, verdicts, rules=owners_rules)
        issues += owner_approval(forge, pr, config, required)

    issues = sorted(issues, key=lambda i: (*i.sort_key, i.message))
    return VerifyReport(
        base_ref=base_ref,
        head=head,
        verdicts=verdicts,
        issues=issues,
        blocking=blocking_issues(issues, list(config.policy.fail_on)),
        mode=config.policy.mode,
        scope="all" if all_ or changes is None else "affected",
        pr=pr,
        required=required,
        max_items=config.review.max_items_per_pr,
    )
