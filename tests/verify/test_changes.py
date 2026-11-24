import json
from pathlib import Path

from helpers import git, make_git_repo

from kb.gitfiles import head_commit
from kb.verify.changes import compute_changes

KB_VALID = Path(__file__).parent.parent / "fixtures" / "kb_valid"
SEATS = "services/billing/seats.py"
SEG_FILE = "kb/sources/SRC-pricing-spec-v3/segments.jsonl"

BASE_SEATS = (
    "def add_seat(account, seat):\n    account.seats.append(seat)\n\n\n"
    "def remove_seat(account, seat):\n    account.pending_removals.append(seat)\n"
)


def repo(tmp_path, extra=None):
    return make_git_repo(KB_VALID, tmp_path, {SEATS: BASE_SEATS, **(extra or {})})


def changes(root):
    return compute_changes(root, head_commit(root))


def test_refactor_marks_symbol_changed(tmp_path):
    root = repo(tmp_path)
    (root / SEATS).write_text(BASE_SEATS.replace("pending_removals.append(seat)", "pending_removals.append(seat)\n    audit(seat)"))
    cs = changes(root)
    assert cs.symbols == {f"{SEATS}#remove_seat": "changed"}
    base_fp, head_fp = cs.fingerprints[f"{SEATS}#remove_seat"]
    assert base_fp and head_fp and base_fp != head_fp
    same = cs.fingerprints[f"{SEATS}#add_seat"]
    assert same[0] == same[1] is not None
    assert cs.files == (SEATS,)


def test_format_only_change_marks_nothing(tmp_path):
    root = repo(tmp_path, {SEATS: "def f(a):\n    return g(a, 'x')\n"})
    (root / SEATS).write_text('def f( a ):\n    # hi\n    return g(a,   "x",)\n')
    cs = changes(root)
    assert cs.files == (SEATS,)
    assert cs.symbols == {}


def test_rename_is_removed_plus_added(tmp_path):
    root = repo(tmp_path)
    (root / SEATS).write_text(BASE_SEATS.replace("remove_seat", "drop_seat"))
    cs = changes(root)
    assert cs.symbols == {f"{SEATS}#remove_seat": "removed", f"{SEATS}#drop_seat": "added"}
    assert cs.fingerprints[f"{SEATS}#remove_seat"][1] is None
    assert cs.fingerprints[f"{SEATS}#drop_seat"][0] is None


def test_segment_text_change_marks_segment_changed(tmp_path):
    root = repo(tmp_path)
    lines = (root / SEG_FILE).read_text().splitlines()
    seg = json.loads(lines[2])
    assert seg["id"] == "seg-03"
    seg["hash"] = "ffffffffffff"
    lines[2] = json.dumps(seg)
    (root / SEG_FILE).write_text("\n".join(lines) + "\n")
    assert changes(root).segments == {"SRC-pricing-spec-v3#seg-03": "changed"}


def test_new_source_segments_are_added(tmp_path):
    root = repo(tmp_path)
    src = root / "kb/sources/SRC-new"
    src.mkdir()
    for name in ("source.yaml", "text.md", "triage.yaml", "segments.jsonl"):
        (src / name).write_text((root / "kb/sources/SRC-pricing-spec-v3" / name).read_text())
    (src / "source.yaml").write_text((src / "source.yaml").read_text().replace("SRC-pricing-spec-v3", "SRC-new"))
    segs = changes(root).segments
    assert segs and set(segs.values()) == {"added"} and all(k.startswith("SRC-new#") for k in segs)


def test_lockfile_bump_lists_package(tmp_path):
    lock = 'version = 1\n\n[[package]]\nname = "stripe"\nversion = "7.0"\n\n[[package]]\nname = "idna"\nversion = "3.0"\n'
    root = repo(tmp_path, {"uv.lock": lock})
    (root / "uv.lock").write_text(lock.replace("7.0", "7.1"))
    assert changes(root).packages == frozenset({"stripe"})


def test_unparseable_lockfile_does_not_crash(tmp_path):
    lock = '[[package]]\nname = "stripe"\nversion = "7.0"\n'
    root = repo(tmp_path, {"uv.lock": lock})
    (root / "uv.lock").write_text("[[package\n")
    assert changes(root).packages == frozenset({"stripe"})


def test_untracked_file_counts_as_added(tmp_path):
    root = repo(tmp_path)
    (root / "services/billing/new.py").write_text("def fresh():\n    return 1\n")
    cs = changes(root)
    assert "services/billing/new.py" in cs.files
    assert cs.symbols == {"services/billing/new.py#fresh": "added"}


def test_deleted_file_marks_symbols_removed_and_tree_untouched(tmp_path):
    root = repo(tmp_path)
    (root / SEATS).unlink()
    before = git(root, "status", "--porcelain")
    cs = changes(root)
    assert cs.symbols == {f"{SEATS}#add_seat": "removed", f"{SEATS}#remove_seat": "removed"}
    assert git(root, "status", "--porcelain") == before


def test_file_without_adapter_only_in_files(tmp_path):
    root = repo(tmp_path, {"notes.txt": "a"})
    (root / "notes.txt").write_text("b")
    cs = changes(root)
    assert cs.files == ("notes.txt",) and cs.symbols == {} and cs.fingerprints == {}
