"""kb verify's approval policy and CODEOWNERS come from the target branch's tip, not from the change under review nor
from the merge-base (which the change chooses by where it branches)."""

import json
import os

import pytest
from helpers import commit, git, make_git_repo

from kb.config.load import ConfigError, load_config
from kb.forge.local import LocalForge
from kb.gitfiles import GitError
from kb.verify.policy import load_policy
from kb.verify.run import run_verify

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


def sha(root, ref="HEAD"):
    return git(root, "rev-parse", ref).strip()


def head_cfg(root):
    return load_config(root / "kb.config.yaml")


def branch(root, name="feature", start=None):
    git(root, "checkout", "-q", "-b", name, *([start] if start else []))


def local_forge(tmp_path, root, **pr):
    path = tmp_path / "forge.json"
    data = {"pr": {"number": 7, "head_sha": sha(root), "author": "alice", **pr}, "reviews": [], "teams": {}}
    path.write_text(json.dumps(data))
    return LocalForge(path)


def test_policy_comes_from_the_base(tmp_path):
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": BASE_CONFIG})
    base_policy = head_cfg(root).policy
    branch(root)
    (root / "kb.config.yaml").write_text(HEAD_CONFIG)
    commit(root, "loosen the policy")
    head = head_cfg(root)
    assert head.policy != base_policy
    policy = load_policy(root, "main", head)
    assert policy.config.policy == base_policy
    assert policy.config.policy.approvers.intent == ["@acme/product"]
    assert policy.source == f"base main at {sha(root, 'main')[:7]}"


def test_policy_comes_from_the_base_tip_not_the_merge_base(tmp_path):
    loose = BASE_CONFIG.replace("intent: ['@acme/product']", "intent: []")
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": loose})
    branch(root)
    (root / "src.py").write_text("x = 1\n")
    commit(root, "work")
    git(root, "checkout", "-q", "main")  # meanwhile main names an intent approver
    (root / "kb.config.yaml").write_text(BASE_CONFIG)
    commit(root, "intent approvers")
    tip = sha(root)
    git(root, "checkout", "-q", "feature")
    policy = load_policy(root, "main", head_cfg(root))
    assert policy.config.policy.approvers.intent == ["@acme/product"]
    assert policy.source == f"base main at {tip[:7]}"


def test_branching_from_before_kb_does_not_hand_over_the_policy(tmp_path):
    root = make_git_repo(None, tmp_path, {"README.md": "x\n"})
    first = sha(root)
    (root / "kb.config.yaml").write_text(BASE_CONFIG)
    (root / "CODEOWNERS").write_text("/kb.config.yaml @acme/tech-leads\n")
    commit(root, "kb")
    branch(root, start=first)  # the merge-base has no kb.config.yaml...
    (root / "kb.config.yaml").write_text(HEAD_CONFIG.replace("['@acme/tech-leads']", "['@mallory']"))
    commit(root, "my own policy")
    policy = load_policy(root, "main", head_cfg(root))  # ...but main's tip does: it decides
    assert policy.config.policy.approvers.decisions == ["@acme/tech-leads"]
    assert policy.config.policy.mode == "enforce"
    assert policy.codeowners == [("/kb.config.yaml", ["@acme/tech-leads"])]
    assert policy.source == f"base main at {sha(root, 'main')[:7]}"


def test_no_base_uses_the_working_tree(tmp_path):
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": BASE_CONFIG, "CODEOWNERS": "src/** @first\n"})
    head = head_cfg(root)
    policy = load_policy(root, None, head)
    assert policy.config is head
    assert policy.codeowners == [("src/**", ["@first"])]
    assert policy.source == "working tree"


def test_missing_config_at_tip_is_bootstrap(tmp_path):
    root = make_git_repo(None, tmp_path, {"README.md": "x\n", "CODEOWNERS": "* @acme/tech-leads\n"})
    branch(root)
    (root / "kb.config.yaml").write_text(HEAD_CONFIG)
    (root / "CODEOWNERS").write_text("* @mallory\n")
    commit(root, "add kb")
    head = head_cfg(root)
    policy = load_policy(root, "main", head)
    assert policy.config is head
    assert policy.codeowners == [("*", ["@acme/tech-leads"])]  # CODEOWNERS still comes from the tip
    assert policy.source == "working tree (kb.config.yaml is new in this change)"


def test_invalid_base_config_is_an_error(tmp_path):
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": "version: [\n"})
    tip = sha(root)
    branch(root)
    (root / "kb.config.yaml").write_text(HEAD_CONFIG)
    commit(root, "fix the config")
    with pytest.raises(ConfigError) as e:
        load_policy(root, "main", head_cfg(root))
    assert tip in str(e.value)


def test_unresolvable_tip_is_an_error(tmp_path):
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": BASE_CONFIG})
    with pytest.raises(GitError, match="nope"):
        load_policy(root, "nope", head_cfg(root))


def test_codeowners_from_the_base(tmp_path):
    root = make_git_repo(
        None, tmp_path, {"kb.config.yaml": BASE_CONFIG, ".github/CODEOWNERS": "/kb/requirements/ @acme/product\n"}
    )
    branch(root)
    (root / ".github/CODEOWNERS").unlink()
    commit(root, "drop CODEOWNERS")
    policy = load_policy(root, "main", head_cfg(root))
    assert ("/kb/requirements/", ["@acme/product"]) in policy.codeowners


def test_codeowners_symlink_at_base_ignored(tmp_path):
    """GitHub looks in .github/ first: a symlink there is skipped, so the root CODEOWNERS applies."""
    root = make_git_repo(
        None, tmp_path, {"kb.config.yaml": BASE_CONFIG, "docs/x": "* @mallory\n", "CODEOWNERS": "src/** @first\n"}
    )
    (root / ".github").mkdir()
    os.symlink("../docs/x", root / ".github/CODEOWNERS")
    commit(root, "symlinked CODEOWNERS")
    policy = load_policy(root, "main", head_cfg(root))
    assert policy.codeowners == [("src/**", ["@first"])]


def _loosened_feature(tmp_path):
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": BASE_CONFIG})
    branch(root)
    (root / "kb.config.yaml").write_text(HEAD_CONFIG)
    commit(root, "loosen")
    return root


def test_all_with_a_pull_request_still_uses_the_base_policy(tmp_path):
    root = _loosened_feature(tmp_path)
    report = run_verify(root, base=None, all_=True, forge=local_forge(tmp_path, root))  # no PR base: default branch
    assert report.mode == "enforce"
    assert report.policy_source == f"base main at {sha(root, 'main')[:7]}"
    # the PR's own base branch, when the forge names one
    git(root, "branch", "release", "main")
    report = run_verify(root, base=None, all_=True, forge=local_forge(tmp_path, root, base="release"))
    assert report.policy_source == f"base release at {sha(root, 'release')[:7]}"
    # without a pull request, --all keeps the working tree's policy
    report = run_verify(root, base=None, all_=True, forge=None)
    assert (report.mode, report.policy_source) == ("warn-only", "working tree")


def test_all_with_a_pull_request_and_no_base_branch_is_an_error(tmp_path):
    root = _loosened_feature(tmp_path)
    git(root, "branch", "-q", "-D", "main")  # no default branch
    with pytest.raises(GitError, match="base branch"):
        run_verify(root, base=None, all_=True, forge=local_forge(tmp_path, root))
    with pytest.raises(GitError, match="gone"):  # a PR base that does not resolve is an error too, never a fallback
        run_verify(root, base=None, all_=True, forge=local_forge(tmp_path, root, base="gone"))


def test_verify_takes_the_policy_from_the_tip_when_branched_from_before_kb(tmp_path):
    root = make_git_repo(None, tmp_path, {"README.md": "x\n"})
    first = sha(root)
    (root / "kb.config.yaml").write_text(BASE_CONFIG)
    commit(root, "kb")
    branch(root, start=first)
    (root / "kb.config.yaml").write_text(HEAD_CONFIG.replace("['@acme/tech-leads']", "['@mallory']"))
    commit(root, "my own policy")
    report = run_verify(root, base="main", all_=False, forge=local_forge(tmp_path, root))
    assert report.mode == "enforce"
    assert report.policy_source == f"base main at {sha(root, 'main')[:7]}"
    assert report.required == {"@acme/tech-leads": ["kb.config.yaml changed"]}
    assert "missing_owner_approval" in {i.code for i in report.blocking}


def test_a_tag_shadowing_the_base_branch_is_an_error(tmp_path):
    root = _loosened_feature(tmp_path)
    git(root, "tag", "main", "HEAD")  # the PR head, named like the target branch
    with pytest.raises(GitError, match="a tag named 'main' shadows the branch"):
        load_policy(root, "main", head_cfg(root))
    policy = load_policy(root, "refs/heads/main", head_cfg(root))  # a full ref is not ambiguous
    assert policy.config.policy.mode == "enforce"
    assert policy.source == f"base refs/heads/main at {sha(root, 'refs/heads/main')[:7]}"
