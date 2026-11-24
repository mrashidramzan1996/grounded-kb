"""Symlink-safe writes under .kb/cache."""

import os
import tempfile
from pathlib import Path

from kb.safepath import UnsafePath


def write_cache(root: Path, rel: str, text: str) -> Path:
    """Write `rel` under .kb/cache atomically, refusing to write through symlinks."""
    root = Path(root)
    target = root / rel
    for p in (root / ".kb", root / ".kb/cache", target.parent, target):
        if p.is_symlink():
            raise UnsafePath(f"{p.relative_to(root)} is a symlink; refusing to write through it")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
