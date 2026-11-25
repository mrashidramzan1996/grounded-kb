"""kb verify's approval policy and CODEOWNERS come from the merge-base, not from the change under review."""

import os

import pytest
from helpers import commit, git, make_git_repo

from kb.config.load import ConfigError, load_config
from kb.verify.policy import load_policy

BASE_CONFIG = (
    "version: 1\n"
    "policy:\n"
    "  mode: enforce\n"
    "  approvers:\n"
    "    intent: ['@acme/product']\n"
    "    decisions: ['@acme/tech-leads']\n"
)
HEAD_CONFIG = (
    "version: 1\n"
    "policy:\n"
    "  mode: warn-only\n"
    "  fail_on: []\n"
    "  approvers:\n"
    "    intent: []\n"
    "    decisions: ['@acme/tech-leads']\n"
)


def base_sha(root):
    return git(root, "rev-parse", "HEAD").strip()


def head_cfg(root):
    return load_config(root / "kb.config.yaml")


def test_policy_comes_from_the_base(tmp_path):
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": BASE_CONFIG})
    sha = base_sha(root)
    base_policy = head_cfg(root).policy
    git(root, "checkout", "-q", "-b", "feature")
    (root / "kb.config.yaml").write_text(HEAD_CONFIG)
    commit(root, "loosen the policy")
    head = head_cfg(root)
    assert head.policy != base_policy
    policy = load_policy(root, sha, head)
    assert policy.config.policy == base_policy
    assert policy.config.policy.approvers.intent == ["@acme/product"]
    assert policy.source == f"base {sha[:7]}"


def test_no_base_uses_the_working_tree(tmp_path):
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": BASE_CONFIG, "CODEOWNERS": "src/** @first\n"})
    head = head_cfg(root)
    policy = load_policy(root, None, head)
    assert policy.config is head
    assert policy.codeowners == [("src/**", ["@first"])]
    assert policy.source == "working tree"


def test_config_new_in_this_change_uses_working_tree_and_says_so(tmp_path):
    root = make_git_repo(None, tmp_path, {"README.md": "x\n"})
    sha = base_sha(root)
    (root / "kb.config.yaml").write_text(HEAD_CONFIG)
    commit(root, "add kb")
    head = head_cfg(root)
    policy = load_policy(root, sha, head)
    assert policy.config is head
    assert policy.source == "working tree (kb.config.yaml is new in this change)"


def test_invalid_base_config_is_an_error(tmp_path):
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": "version: [\n"})
    sha = base_sha(root)
    (root / "kb.config.yaml").write_text(HEAD_CONFIG)
    commit(root, "fix the config")
    with pytest.raises(ConfigError) as e:
        load_policy(root, sha, head_cfg(root))
    assert sha in str(e.value)


def test_codeowners_from_the_base(tmp_path):
    root = make_git_repo(
        None, tmp_path, {"kb.config.yaml": BASE_CONFIG, ".github/CODEOWNERS": "/kb/requirements/ @acme/product\n"}
    )
    sha = base_sha(root)
    (root / ".github/CODEOWNERS").unlink()
    commit(root, "drop CODEOWNERS")
    policy = load_policy(root, sha, head_cfg(root))
    assert ("/kb/requirements/", ["@acme/product"]) in policy.codeowners


def test_codeowners_symlink_at_base_ignored(tmp_path):
    """GitHub looks in .github/ first: a symlink there is skipped, so the root CODEOWNERS applies."""
    root = make_git_repo(
        None, tmp_path, {"kb.config.yaml": BASE_CONFIG, "docs/x": "* @mallory\n", "CODEOWNERS": "src/** @first\n"}
    )
    (root / ".github").mkdir()
    os.symlink("../docs/x", root / ".github/CODEOWNERS")
    commit(root, "symlinked CODEOWNERS")
    sha = base_sha(root)
    policy = load_policy(root, sha, head_cfg(root))
    assert policy.codeowners == [("src/**", ["@first"])]
