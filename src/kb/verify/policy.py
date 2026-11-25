"""Whose rules decide a pull request's approvals: kb.config.yaml and CODEOWNERS at the tip of the branch it targets.
Not the change's own, and not the merge-base's either: the change picks its merge-base by where it branches, so an
old commit with looser approvers (or none, before kb existed) must not judge it."""

from dataclasses import dataclass
from pathlib import Path

from kb.config.load import config_at
from kb.config.schema import KbConfig
from kb.gitfiles import GitError, rev_parse
from kb.verify.approvals import CodeownersRules, codeowners_rules, codeowners_rules_at


@dataclass(frozen=True)
class Policy:
    config: KbConfig  # approvers, teams, bots, modules (for owners), fail_on and mode
    codeowners: CodeownersRules | None  # None: no CODEOWNERS, module owners apply
    source: str  # where it came from, for the report: "base <branch> at <sha7>" or "working tree…"


def load_policy(root: Path, branch: str | None, head_config: KbConfig) -> Policy:
    """The policy at the tip of `branch` (the target branch: "main" or "origin/main"). No branch: the working tree's,
    as before. A tip without kb.config.yaml (kb is new in this change): the working tree's config, with the tip's
    CODEOWNERS. A branch that does not resolve is a GitError, and a tip config that does not parse a ConfigError:
    never a fallback to the working tree."""
    root = Path(root)
    if branch is None:
        return Policy(head_config, codeowners_rules(root), "working tree")
    if branch.startswith("-") or (tip := rev_parse(root, branch)) is None:
        raise GitError(f"cannot resolve the base branch {branch!r} to read the approval policy; fetch it first")
    config = config_at(root, tip)
    rules = codeowners_rules_at(root, tip)
    if config is None:
        return Policy(head_config, rules, "working tree (kb.config.yaml is new in this change)")
    return Policy(config, rules, f"base {branch} at {tip[:7]}")
