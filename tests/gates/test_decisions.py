from helpers import add_adr_file, edit, git

from kb.gates.base import run_gates
from kb.gates.decisions import decision_guard


def test_decision_guard_blocks_edit_allows_new(valid_git_ctx):
    edit(valid_git_ctx, "kb/decisions/ADR-0002.md", "typo")
    add_adr_file(valid_git_ctx, "ADR-0050")
    issues = run_gates(valid_git_ctx.reload(base="HEAD"), [decision_guard])
    assert [(i.code, i.record) for i in issues] == [("decision_guard", "ADR-0002")]


def test_decision_guard_blocks_delete(valid_git_ctx):
    (valid_git_ctx.root / "kb/decisions/ADR-0001.md").unlink()
    issues = run_gates(valid_git_ctx.reload(base="HEAD"), [decision_guard])
    assert [(i.code, i.record) for i in issues] == [("decision_guard", "ADR-0001")]


def test_decision_guard_ignores_other_records_and_no_base(valid_git_ctx):
    edit(valid_git_ctx, "kb/features/FEAT-001.md", "more\n")
    assert run_gates(valid_git_ctx.reload(base="HEAD"), [decision_guard]) == []
    edit(valid_git_ctx, "kb/decisions/ADR-0002.md", "typo")
    assert run_gates(valid_git_ctx.reload(), [decision_guard]) == []
