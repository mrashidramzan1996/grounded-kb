import pytest

from kb.markers import scan_markers


@pytest.mark.parametrize("line", ["# r[verify REQ-F-001]", "// r[impl REQ-F-001]", "/* r[verify INV-001] */"])
def test_markers_any_comment_style(tmp_path, line):
    (tmp_path / "f.x").write_text(f"code\n{line}\n")
    [m] = scan_markers(tmp_path, ["f.x"])
    assert m.line == 2


def test_marker_fields_and_claim_ids(tmp_path):
    (tmp_path / "f.py").write_text("x = 1  # r[impl FEAT-001.C1] r[verify REQ-F-001]\n")
    assert [(m.kind, m.id, m.path, m.line) for m in scan_markers(tmp_path, ["f.py"])] == [
        ("impl", "FEAT-001.C1", "f.py", 1),
        ("verify", "REQ-F-001", "f.py", 1),
    ]


def test_non_markers_ignored(tmp_path):
    (tmp_path / "f.py").write_text("r[impl lower]\nr[other REQ-F-001]\nr[impl REQ-]\n")
    assert scan_markers(tmp_path, ["f.py"]) == []


def test_skips_binary_large_symlink_and_unsafe(tmp_path):
    (tmp_path / "bin").write_bytes(b"\xff\xfe r[impl REQ-F-001]")
    (tmp_path / "big.txt").write_text("r[impl REQ-F-001]\n" + "x" * (2 * 1024 * 1024))
    (tmp_path / "real.txt").write_text("r[impl REQ-F-001]\n")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("r[impl REQ-F-001]\n")
    files = ["bin", "big.txt", "link.txt", "../" + outside.name, "missing.txt", "/abs.txt"]
    assert scan_markers(tmp_path, files) == []


def test_skips_file_under_symlinked_dir(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_text("r[impl REQ-F-001]\n")
    (tmp_path / "dl").symlink_to(tmp_path / "d")
    assert [m.path for m in scan_markers(tmp_path, ["dl/f.txt", "d/f.txt"])] == ["d/f.txt"]


def test_marker_eligible_excludes_kb_and_dot_paths():
    from kb.markers import marker_eligible

    assert marker_eligible("services/billing/seats.py") and marker_eligible("kbtools/x.py")
    assert not marker_eligible("kb/requirements/REQ-F-001.yaml")
    assert not marker_eligible(".github/x.py") and not marker_eligible("src/.hidden/x.py")


def test_agent_rules_contain_no_parseable_marker(tmp_path):
    from kb.markers import scan_markers
    from kb.rules import AGENT_RULES

    (tmp_path / "A.md").write_text("\n".join(AGENT_RULES))
    assert scan_markers(tmp_path, ["A.md"]) == []
    assert "r[impl <ID>]" in AGENT_RULES[3] and "r[verify <ID>]" in AGENT_RULES[3]
