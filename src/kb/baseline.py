"""`kb baseline freeze`: record the agreed set of live requirements at HEAD."""

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from kb.gitfiles import commit_time, create_tag, delete_tag, head_commit, is_dirty, tag_exists
from kb.records.lifecycle import live_ids
from kb.records.models import Requirement
from kb.records.repo import KbRepo
from kb.yamlio import dump_yaml

_VERSION = re.compile(r"v\d+")


class BaselineError(Exception):
    pass


def _load_problems(repo: KbRepo) -> list[str]:
    problems = [f"{i.path}: {i.message}" for i in repo.errors]
    by_id: dict[str, list[str]] = {}
    for rid, path in repo.id_paths:
        by_id.setdefault(rid, []).append(path)
    problems += [f"{rid} is defined in {', '.join(paths)}" for rid, paths in sorted(by_id.items()) if len(paths) > 1]
    return problems


def freeze_baseline(root: Path, version: str) -> Path:
    """Write kb/baseline/<version>.yaml (uncommitted) and tag HEAD as kb-baseline-<version>.
    Nothing is left behind if any step fails."""
    root = Path(root)
    if not _VERSION.fullmatch(version):
        raise BaselineError(f"version must look like v1, v2, ...: got {version!r}")
    for rel in ("kb", "kb/baseline"):
        if (root / rel).is_symlink():
            raise BaselineError(f"{rel} must not be a symlink")
    folder = root / "kb/baseline"
    if folder.exists() and not folder.is_dir():
        raise BaselineError("kb/baseline exists and is not a directory")
    final = folder / f"{version}.yaml"
    tag = f"kb-baseline-{version}"
    if tag_exists(root, tag):
        raise BaselineError(f"tag {tag} already exists")
    if final.exists():
        raise BaselineError(f"kb/baseline/{version}.yaml already exists")
    if is_dirty(root, "kb/"):
        raise BaselineError("kb/ has uncommitted changes; commit them before freezing a baseline")

    repo = KbRepo.load(root)
    if problems := _load_problems(repo):
        raise BaselineError("KB files have problems (run `kb check`):\n" + "\n".join(problems))
    live = live_ids(repo)
    reqs = [
        {"id": rid, "hash": hashlib.sha256(repo.raw[repo.paths[rid]]).hexdigest()}
        for rid in sorted(live)
        if isinstance(repo.records[rid], Requirement)
    ]
    frozen_at = datetime.fromtimestamp(commit_time(root, "HEAD"), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = dump_yaml({"version": version, "commit": head_commit(root), "frozen_at": frozen_at, "requirements": reqs})

    made_dir = not folder.exists()
    made_tag = False
    tmp = folder / f".{version}.yaml.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        create_tag(root, tag)
        made_tag = True
        os.link(tmp, final)  # fails if final exists
        tmp.unlink()
    except BaseException as e:
        tmp.unlink(missing_ok=True)
        if made_tag:
            delete_tag(root, tag)
        if made_dir:
            try:
                folder.rmdir()
            except OSError:
                pass
        if isinstance(e, FileExistsError):
            raise BaselineError(f"kb/baseline/{version}.yaml already exists") from e
        raise
    return final
