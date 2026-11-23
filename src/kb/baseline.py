"""`kb baseline freeze`: record the agreed set of live requirements at HEAD."""

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from kb.gitfiles import GitError, _git
from kb.records.lifecycle import live_ids
from kb.records.models import Requirement
from kb.records.repo import KbRepo
from kb.yamlio import dump_yaml

_VERSION = re.compile(r"v\d+")


class BaselineError(Exception):
    pass


def freeze_baseline(root: Path, version: str) -> Path:
    """Write kb/baseline/<version>.yaml (uncommitted) and tag HEAD as kb-baseline-<version>."""
    root = Path(root)
    if not _VERSION.fullmatch(version):
        raise BaselineError(f"version must look like v1, v2, ...: got {version!r}")
    for rel in ("kb", "kb/baseline"):
        if (root / rel).is_symlink():
            raise BaselineError(f"{rel} must not be a symlink")
    tag = f"kb-baseline-{version}"
    if _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False).returncode == 0:
        raise BaselineError(f"tag {tag} already exists")
    if _git(root, "status", "--porcelain", "--", "kb/").stdout.strip():
        raise BaselineError("kb/ has uncommitted changes; commit them before freezing a baseline")
    commit = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    stamp = int(_git(root, "show", "-s", "--format=%ct", "HEAD").stdout.decode().strip())
    frozen_at = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    repo = KbRepo.load(root)
    live = live_ids(repo)
    reqs = [
        {"id": rid, "hash": hashlib.sha256(repo.raw[repo.paths[rid]]).hexdigest()}
        for rid in sorted(live)
        if isinstance(repo.records[rid], Requirement)
    ]
    text = dump_yaml({"version": version, "commit": commit, "frozen_at": frozen_at, "requirements": reqs})

    out = root / "kb/baseline" / f"{version}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError as e:
        raise BaselineError(f"{out.relative_to(root).as_posix()} already exists") from e
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        _git(root, "tag", tag, "HEAD")
    except GitError:
        out.unlink()
        raise
    return out
