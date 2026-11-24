"""Stale-context check (A-5): a branch that touches code governed by a record that changed on the default
branch after the fork was written against out-of-date context."""

from pathlib import Path

from kb.config.schema import KbConfig
from kb.context import ContextPack, governing
from kb.gitfiles import GitError, diff_names, rev_parse
from kb.issues import Issue
from kb.records.repo import KbRepo


def _governing_ids(pack: ContextPack) -> set[str]:
    ids = {r.id for r in (*pack.decisions, *pack.invariants, *pack.requirements)}
    return ids | set(pack.claim_features.values())


def stale_context(
    root: Path, config: KbConfig, repo: KbRepo, base_branch: str, merge_base_ref: str, changed: list[str]
) -> list[Issue]:
    tip = rev_parse(root, base_branch)
    if tip is None:
        raise GitError(f"base branch '{base_branch}' does not resolve to a commit")
    if tip == rev_parse(root, merge_base_ref):
        return []
    tip_repo, base_repo = KbRepo.at_ref(root, tip), KbRepo.at_ref(root, merge_base_ref)
    moved = set(diff_names(root, merge_base_ref, tip, "kb/"))
    paths = {**base_repo.paths, **tip_repo.paths}  # a record's path at tip wins over its path at the fork
    changed_ids = {rid for rid, path in paths.items() if path in moved}
    used = set()
    for r in (repo, tip_repo):
        used |= _governing_ids(governing(root, config, r, paths=changed))
    return [
        Issue(
            "stale_context",
            f"{rid} changed on {base_branch} after this branch forked; rebase on {base_branch} and re-run `kb context`",
            path=paths[rid],
            record=rid,
        )
        for rid in sorted(changed_ids & used)
    ]
