"""`kb baseline freeze`: record the agreed set of live requirements at HEAD."""

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from kb.check import run_check
from kb.gitfiles import (
    commit_time, create_tag, delete_tag, find_default_branch, head_commit, is_dirty, require_commit, rev_parse, tag_exists,
)
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


_SHOWN_ERRORS = 5


def requirement_hashes(repo: KbRepo) -> list[dict[str, str]]:
    """What a baseline records: the id and file hash of every live requirement, sorted by id."""
    return [
        {"id": rid, "hash": hashlib.sha256(repo.raw[repo.paths[rid]]).hexdigest()}
        for rid in sorted(live_ids(repo))
        if isinstance(repo.records[rid], Requirement)
    ]


def _on_default_branch_tip(root: Path) -> None:
    branch = find_default_branch(root)
    if branch is None or rev_parse(root, branch) != head_commit(root):
        where = f"the tip of the default branch ({branch})" if branch else "on a default branch (main, master or origin/HEAD)"
        raise BaselineError(
            f"HEAD is not {where}: a baseline records what was approved by merging (A-2). "
            "Freeze it on the default branch after the merge, or pass --allow-branch to freeze this commit anyway"
        )


def _gate_errors(root: Path) -> None:
    errors = [i for i in run_check(root).issues if i.severity == "error"]
    if errors:
        shown = [f"{i.code} {i.path or '-'} {i.record or '-'}: {' '.join(i.message.split())}" for i in errors[:_SHOWN_ERRORS]]
        more = [f"... and {len(errors) - _SHOWN_ERRORS} more"] if len(errors) > _SHOWN_ERRORS else []
        raise BaselineError(f"kb check reports {len(errors)} errors; fix them before freezing a baseline:\n" + "\n".join(shown + more))


def freeze_baseline(root: Path, version: str, *, allow_branch: bool = False) -> Path:
    """Write kb/baseline/<version>.yaml (uncommitted) and tag HEAD as kb-baseline-<version>.
    Refuses unless HEAD is the default branch's tip (or allow_branch) and `kb check` reports no error.
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

    require_commit(root)
    if not allow_branch:
        _on_default_branch_tip(root)
    repo = KbRepo.load(root)
    if problems := _load_problems(repo):
        raise BaselineError("KB files have problems (run `kb check`):\n" + "\n".join(problems))
    _gate_errors(root)
    reqs = requirement_hashes(repo)
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
