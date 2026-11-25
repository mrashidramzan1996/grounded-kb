"""Whose rules decide a pull request's approvals: the merge-base's kb.config.yaml and CODEOWNERS, so a change cannot
loosen the policy it is checked against."""

from dataclasses import dataclass
from pathlib import Path

from kb.config.load import config_at
from kb.config.schema import KbConfig
from kb.verify.approvals import CodeownersRules, codeowners_rules, codeowners_rules_at


@dataclass(frozen=True)
class Policy:
    config: KbConfig  # approvers, teams, bots, modules (for owners), fail_on and mode
    codeowners: CodeownersRules | None  # None: no CODEOWNERS, module owners apply
    source: str  # where it came from, for the report: "base <sha7>" or "working tree…"


def load_policy(root: Path, base_ref: str | None, head_config: KbConfig) -> Policy:
    """The policy at `base_ref` (the merge-base). No base: the working tree's, as before. A base without
    kb.config.yaml (kb is new in this change): the working tree's config, with the base's CODEOWNERS. A base config
    that does not parse is a ConfigError, and git failing a GitError: never a fallback to the working tree."""
    root = Path(root)
    if base_ref is None:
        return Policy(head_config, codeowners_rules(root), "working tree")
    config = config_at(root, base_ref)
    rules = codeowners_rules_at(root, base_ref)
    if config is None:
        return Policy(head_config, rules, "working tree (kb.config.yaml is new in this change)")
    return Policy(config, rules, f"base {base_ref[:7]}")
