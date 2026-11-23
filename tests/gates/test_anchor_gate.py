from helpers import add_file, codes, set_claim, set_field

from kb.gates.anchors import anchor_gate
from kb.gates.base import run_gates


def test_valid_fixture_is_clean(valid_ctx):
    assert run_gates(valid_ctx, [anchor_gate]) == []


def test_anchor_gate(valid_ctx_with):
    ctx = valid_ctx_with(
        set_claim("FEAT-001", 0, code="services/billing/seats.py#gone", test="tests/test_seats.py::nope"),
        add_file("tests/test_x.py", "# r[verify REQ-F-999]\n"),
    )
    assert set(codes(run_gates(ctx, [anchor_gate]))) == {"anchor.unresolved", "test.missing", "marker.unknown_id"}


def test_typescript_file_is_unverified_not_broken(valid_ctx_with):
    ctx = valid_ctx_with(set_claim("FEAT-001", 0, code="web/app.ts#main"))
    assert [(i.code, i.severity) for i in run_gates(ctx, [anchor_gate])] == [("anchor.no_adapter", "warning")]


def test_unsafe_anchor_and_test_paths(valid_ctx_with):
    ctx = valid_ctx_with(set_claim("FEAT-001", 0, code="../x.py#f", test="/etc/x.py::t"))
    assert codes(run_gates(ctx, [anchor_gate])) == ["anchor.invalid_path", "anchor.invalid_path"]


def test_malformed_refs(valid_ctx_with):
    ctx = valid_ctx_with(set_claim("FEAT-001", 0, code="services/billing/seats.py", test="tests/test_seats.py"))
    assert sorted(codes(run_gates(ctx, [anchor_gate]))) == ["anchor.unresolved", "test.missing"]


def test_claim_without_test_or_verify_marker_warns(valid_ctx_with):
    ctx = valid_ctx_with(
        set_claim("FEAT-001", 0, test=None),
        add_file("tests/test_seats.py", "def test_no_refund_on_removal(): ...\n"),
    )
    issues = run_gates(ctx, [anchor_gate])
    assert [(i.code, i.severity) for i in issues] == [("claim.no_test", "warning")]


def test_verify_marker_satisfies_claim_without_test(valid_ctx_with):
    ctx = valid_ctx_with(set_claim("FEAT-001", 0, test=None))  # tests/test_seats.py keeps its r[verify FEAT-001.C1]
    assert run_gates(ctx, [anchor_gate]) == []


def test_invariant_without_tests_warns_and_missing_test_errors(valid_ctx_with):
    ctx = valid_ctx_with(set_field("INV-001", tests=[]))
    assert [(i.code, i.severity) for i in run_gates(ctx, [anchor_gate])] == [("invariant.no_test", "warning")]
    ctx = valid_ctx_with(set_field("INV-001", tests=["tests/test_sessions.py::nope"]))
    assert codes(run_gates(ctx, [anchor_gate])) == ["test.missing"]


def test_markers_under_kb_are_not_scanned(valid_ctx_with):
    ctx = valid_ctx_with(add_file("kb/notes.txt", "r[impl REQ-F-999]\n"))
    assert "marker.unknown_id" not in codes(run_gates(ctx, [anchor_gate]))


def test_marker_ids_may_be_records_or_claims(valid_ctx_with):
    ctx = valid_ctx_with(add_file("lib.js", "// r[impl REQ-F-001]\n// r[verify INV-001]\n// r[impl FEAT-001.C1]\n"))
    assert run_gates(ctx, [anchor_gate]) == []


def test_unresolved_anchor_in_broken_file_hints_syntax_errors(valid_ctx_with):
    ctx = valid_ctx_with(
        add_file("lib/broken.py", "def good(): ...\ndef broken(:\n    pass\n"),
        set_claim("FEAT-001", 0, code="lib/broken.py#lost"),
    )
    [issue] = [i for i in run_gates(ctx, [anchor_gate]) if i.code == "anchor.unresolved"]
    assert "file has syntax errors; symbols may be missing" in issue.message


def test_valid_ctx_with_calls_do_not_share_files(valid_ctx_with):
    a = valid_ctx_with(add_file("only_a.txt", "x"))
    b = valid_ctx_with()
    assert (a.root / "only_a.txt").exists() and not (b.root / "only_a.txt").exists()


def test_markers_in_agent_instruction_files_are_documentation(valid_ctx_with):
    ctx = valid_ctx_with(add_file("AGENTS.md", "put r[impl ID] in code\n"), add_file("CLAUDE.md", "r[verify ID]\n"))
    assert "marker.unknown_id" not in codes(run_gates(ctx, [anchor_gate]))
