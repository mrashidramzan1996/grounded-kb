"""Fail-closed access to files named by repo-relative paths: no escapes, no symlinks."""

import errno
import os
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
    Raises UnsafePath if `rel` is malformed or any component under root is a symlink.
    Components are opened one at a time with O_NOFOLLOW relative to the parent fd, so no symlink can be
    swapped in between the check and the read."""
    check_relpath(rel)
    parts = rel.split("/")
    fds: list[int] = []
    try:
        try:
            fds.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY))
        except OSError:
            return None
        for i, part in enumerate(parts):
            leaf = i == len(parts) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_NONBLOCK if leaf else os.O_DIRECTORY)
            try:
                fds.append(os.open(part, flags, dir_fd=fds[-1]))
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
                    try:
                        if stat.S_ISLNK(os.lstat(part, dir_fd=fds[-1]).st_mode):
                            raise UnsafePath(f"symlinks are not allowed: {rel!r}") from None
                    except OSError:
                        pass
                return None
        st = os.fstat(fds[-1])
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            return None
        with os.fdopen(os.dup(fds[-1]), "rb") as f:
            data = f.read(max_bytes + 1)
        return data if len(data) <= max_bytes else None
    finally:
        for fd in fds:
            os.close(fd)
