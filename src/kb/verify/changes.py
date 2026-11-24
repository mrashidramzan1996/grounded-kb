"""What changed between the merge-base and the working tree: files, symbols, source segments, packages.

Both sides are computed from content (A-1): nothing is read from a stored fingerprint.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kb.code.registry import adapter_for
from kb.deps import changed_packages, is_lockfile, lockfile_packages
from kb.gitfiles import changed_since, file_at
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath, read_regular

SymbolChange = Literal["added", "removed", "changed"]


@dataclass(frozen=True)
class ChangeSet:
    base_ref: str
    files: tuple[str, ...]
    symbols: dict[str, SymbolChange]
    fingerprints: dict[str, tuple[str | None, str | None]]
    segments: dict[str, SymbolChange]
    packages: frozenset[str]


def _head_bytes(root: Path, path: str) -> bytes | None:
    try:
        return read_regular(root, path)
    except UnsafePath:
        return None  # the gates report unsafe paths; here it counts as absent


def _fingerprints(path: str, data: bytes | None) -> dict[str, str]:
    adapter = adapter_for(path)
    if adapter is None or data is None:
        return {}
    return {f"{path}#{s.name}": adapter.fingerprint(data, s) for s in adapter.symbols(path, data)}


def _packages(path: str, data: bytes | None) -> dict[str, str]:
    if data is None:
        return {}
    try:
        return lockfile_packages(path, data)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}


def _segment_hashes(repo: KbRepo) -> dict[str, str]:
    return {f"{src}#{seg.id}": seg.hash for src in repo.sources for seg in repo.segments(src)}


def _diff(base: dict[str, str], head: dict[str, str]) -> dict[str, SymbolChange]:
    out: dict[str, SymbolChange] = {}
    for key in sorted(base.keys() | head.keys()):
        b, h = base.get(key), head.get(key)
        if b is None:
            out[key] = "added"
        elif h is None:
            out[key] = "removed"
        elif b != h:
            out[key] = "changed"
    return out


def compute_changes(root: Path, base_ref: str, head_repo: KbRepo | None = None) -> ChangeSet:
    root = Path(root)
    files = tuple(changed_since(root, base_ref))
    symbols: dict[str, SymbolChange] = {}
    fingerprints: dict[str, tuple[str | None, str | None]] = {}
    base_pkgs: dict[str, str] = {}
    head_pkgs: dict[str, str] = {}
    for path in files:
        base_data, head_data = file_at(root, base_ref, path), _head_bytes(root, path)
        if adapter_for(path) is not None:
            base_fps, head_fps = _fingerprints(path, base_data), _fingerprints(path, head_data)
            for anchor in sorted(base_fps.keys() | head_fps.keys()):
                fingerprints[anchor] = (base_fps.get(anchor), head_fps.get(anchor))
            symbols |= _diff(base_fps, head_fps)
        elif is_lockfile(path):
            # key by (file, package) so a bump in one lockfile is not hidden by another file's version
            base_pkgs |= {f"{path}\0{k}": v for k, v in _packages(path, base_data).items()}
            head_pkgs |= {f"{path}\0{k}": v for k, v in _packages(path, head_data).items()}
    packages = frozenset(k.split("\0", 1)[1] for k in changed_packages(base_pkgs, head_pkgs))

    base_segs = _segment_hashes(KbRepo.at_ref(root, base_ref))
    head_segs = _segment_hashes(head_repo if head_repo is not None else KbRepo.load(root))
    return ChangeSet(base_ref, files, symbols, fingerprints, _diff(base_segs, head_segs), packages)
