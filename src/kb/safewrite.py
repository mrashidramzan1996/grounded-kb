"""Symlink-safe reads and atomic writes under a repository root, shared by `kb init` and `kb agents install`."""

import os
import stat
import tempfile
from pathlib import Path
from typing import Literal


class WriteRefused(Exception):
    """A target is a symlink, the wrong type or unreadable; nothing was written."""


def check_target(root: Path, rel: str, kind: Literal["file", "dir"], error: type[Exception] = WriteRefused) -> bool:
    """Whether `rel` exists. Raises error if it, or a parent below root, is a symlink or the wrong type."""
    parts = rel.split("/")
    for i in range(len(parts)):
        sub = "/".join(parts[: i + 1])
        try:
            mode = os.lstat(root / sub).st_mode
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(mode):
            raise error(f"{sub} is a symlink; refusing to write through it")
        want_dir = i < len(parts) - 1 or kind == "dir"
        if want_dir and not stat.S_ISDIR(mode):
            raise error(f"{sub} exists and is not a directory")
        if not want_dir and not stat.S_ISREG(mode):
            raise error(f"{sub} exists and is not a regular file")
    return True


def read_text(root: Path, rel: str, error: type[Exception] = WriteRefused) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise error(f"cannot read {rel}: {e}") from e


def write_atomic(root: Path, rel: str, text: str, mode: int | None = None) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        try:
            mode = stat.S_IMODE(os.lstat(path).st_mode)
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
