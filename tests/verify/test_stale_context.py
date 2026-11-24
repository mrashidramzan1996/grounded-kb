from pathlib import Path

import pytest
from helpers import adr_bytes, git, make_git_repo

from kb.config.load import load_config
from kb.gitfiles import GitError, merge_base, rev_parse
from kb.records.repo import KbRepo
from kb.verify.stale_context import stale_context

KB_VALID = Path(__file__).parent.parent / "fixtures" / "kb_valid"
SEATS = "services/billing/seats.py"


def commit(root, rel, data, msg):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    git(root, "add", "-A")
    git(root, "commit", "-m", msg)


def land_adr(root, id, module):
    git(root, "checkout", "main")
    commit(root, f"kb/decisions/{id}.md", adr_bytes(id, governs=[module]), f"add {id}")


def run(root, changed=(SEATS,), base="main"):
    config = load_config(root / "kb.config.yaml")
    return stale_context(root, config, KbRepo.load(root), base, merge_base(root, base), list(changed))


@pytest.fixture
def forked(tmp_path):
    root = make_git_repo(KB_VALID, tmp_path, {SEATS: "x = 1\n"})
    git(root, "checkout", "-b", "feature")
    commit(root, SEATS, "x = 2\n", "edit seats")
    return root


def test_pr_branched_before_adr_merged_fails(forked):
    land_adr(forked, "ADR-0041", "MOD-billing")
    git(forked, "checkout", "feature")
    issues = run(forked)
    assert [(i.code, i.record, i.path, i.severity) for i in issues] == [
        ("stale_context", "ADR-0041", "kb/decisions/ADR-0041.md", "error")
    ]
    assert issues[0].message == (
        "ADR-0041 changed on main after this branch forked; rebase on main and re-run `kb context`"
    )


def test_passes_after_rebase(forked):
    land_adr(forked, "ADR-0041", "MOD-billing")
    git(forked, "checkout", "feature")
    git(forked, "rebase", "main")
    assert run(forked) == []


def test_unrelated_record_change_does_not_fail(forked):
    land_adr(forked, "ADR-0042", "MOD-sessions")
    git(forked, "checkout", "feature")
    assert run(forked) == []


def test_no_default_branch_progress_passes(forked):
    assert rev_parse(forked, "main") == merge_base(forked, "main")
    assert run(forked) == []


def test_edit_of_existing_governing_adr_fails(forked):
    land_adr(forked, "ADR-0041", "MOD-billing")
    git(forked, "checkout", "feature")
    git(forked, "rebase", "main")
    git(forked, "checkout", "main")
    commit(forked, "kb/decisions/ADR-0041.md", adr_bytes("ADR-0041", governs=["MOD-billing"]) + b"more\n", "edit")
    git(forked, "checkout", "feature")
    assert [i.record for i in run(forked)] == ["ADR-0041"]


def test_deleted_record_on_default_fails(forked):
    land_adr(forked, "ADR-0041", "MOD-billing")
    git(forked, "checkout", "feature")
    git(forked, "rebase", "main")
    git(forked, "checkout", "main")
    git(forked, "rm", "kb/decisions/ADR-0041.md")
    git(forked, "commit", "-m", "drop")
    git(forked, "checkout", "feature")
    issues = run(forked)
    assert [(i.record, i.path) for i in issues] == [("ADR-0041", "kb/decisions/ADR-0041.md")]


def test_unresolvable_base_raises(forked):
    with pytest.raises(GitError, match="nope"):
        stale_context(forked, load_config(forked / "kb.config.yaml"), KbRepo.load(forked), "nope", "HEAD", [SEATS])


def test_test_merge_checkout_hides_stale_context(forked):
    """Why the GitHub template checks out the PR head: from GitHub's test-merge commit (base tip + branch) the
    merge-base with the base is the base tip itself, so nothing can look stale."""
    land_adr(forked, "ADR-0041", "MOD-billing")
    git(forked, "checkout", "--detach", "main")
    git(forked, "merge", "--no-ff", "-m", "test merge", "feature")
    assert run(forked) == []
    git(forked, "checkout", "feature")  # the PR head, as the template checks out
    assert [i.record for i in run(forked)] == ["ADR-0041"]
