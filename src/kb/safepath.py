"""Fail-closed access to files named by repo-relative paths: no escapes, no symlinks."""

import stat
from pathlib import Path, PurePosixPath

MAX_BYTES = 2 * 1024 * 1024


class UnsafePath(ValueError):
    pass


def check_relpath(rel: str) -> None:
    """Raise UnsafePath unless `rel` is a plain relative POSIX path without '.', '..' or empty segments."""
    parts = rel.split("/")
    if not rel or "\\" in rel or "\0" in rel or rel.startswith("/") or PurePosixPath(rel).is_absolute():
        raise UnsafePath(f"not a relative POSIX path: {rel!r}")
    if any(p in ("", ".", "..") for p in parts):
        raise UnsafePath(f"path must not contain empty, '.' or '..' segments: {rel!r}")


def read_regular(root: Path, rel: str, max_bytes: int = MAX_BYTES) -> bytes | None:
    """Bytes of the regular, non-symlink file `root/rel`, or None if absent, not regular or larger than max_bytes.
    Raises UnsafePath if `rel` is malformed or any component under root is a symlink."""
    check_relpath(rel)
    current = Path(root)
    for part in rel.split("/"):
        current = current / part
        try:
            st = current.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode):
            raise UnsafePath(f"symlinks are not allowed: {rel!r}")
    if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
        return None
    try:
        return current.read_bytes()
    except OSError:
        return None
