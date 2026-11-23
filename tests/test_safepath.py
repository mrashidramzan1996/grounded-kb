import os

import pytest

from kb.safepath import UnsafePath, read_regular


def test_reads_regular_file(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_bytes(b"hi")
    assert read_regular(tmp_path, "d/f.txt") == b"hi"
    assert read_regular(tmp_path, "d/none.txt") is None
    assert read_regular(tmp_path, "none/f.txt") is None
    assert read_regular(tmp_path, "d") is None


def test_too_large_is_none(tmp_path):
    (tmp_path / "f").write_bytes(b"x" * 11)
    assert read_regular(tmp_path, "f", max_bytes=10) is None
    assert read_regular(tmp_path, "f", max_bytes=11) == b"x" * 11


def test_fifo_leaf_is_refused_without_hanging(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    assert read_regular(tmp_path, "pipe") is None


def test_symlink_leaf_and_parent_rejected(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_text("x")
    (tmp_path / "l").symlink_to(tmp_path / "d" / "f")
    (tmp_path / "dl").symlink_to(tmp_path / "d")
    for rel in ("l", "dl/f"):
        with pytest.raises(UnsafePath):
            read_regular(tmp_path, rel)
