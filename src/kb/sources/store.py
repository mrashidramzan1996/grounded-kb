"""Read, guard, segment and store a source under kb/sources/<id>/ (spec §6)."""

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

from kb.config.schema import KbConfig, SourceCfg
from kb.records.models import Source
from kb.records.repo import KbRepo
from kb.sources.guard import GuardResult, guard
from kb.sources.readers import NormalisedText, SourceError, read_document
from kb.sources.segment import Segment, assign_segment_ids, segment
from kb.yamlio import dump_yaml

MAX_SOURCE_BYTES = 50_000_000


def source_id(path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", PurePosixPath(path).stem.lower()).strip("-")
    if not slug:
        raise SourceError(f"cannot derive a source id from {path}")
    return f"SRC-{slug}"


def _relative(root: Path, path: str) -> str:
    full = (root / path).resolve()
    try:
        return full.relative_to(root.resolve()).as_posix()
    except ValueError:
        raise SourceError(f"{path} is outside the repository") from None


def _check_store_dirs(root: Path, target: Path) -> None:
    """Refuse to write through symlinks: kb/, kb/sources and the source directory must be real directories."""
    for d in (root / "kb", root / "kb/sources", target):
        if d.is_symlink():
            raise SourceError(f"{d.relative_to(root)} is a symlink; refusing to write through it")
    if (root / "kb/sources").exists() and not (root / "kb/sources").resolve().is_relative_to(root.resolve()):
        raise SourceError("kb/sources resolves outside the repository")


def _config_for(rel: str, config: KbConfig) -> SourceCfg:
    for restricted in config.visibility.restricted:
        if GitIgnoreSpec.from_lines(restricted.paths).match_file(rel):
            raise SourceError(f"{rel} is under visibility.restricted and cannot be a source")
    for cfg in config.sources:
        if GitIgnoreSpec.from_lines(cfg.paths).match_file(rel):
            return cfg
    raise SourceError(f"{rel} is not in config.sources")


def _segments_jsonl(segments) -> str:
    keys = ("id", "heading", "page", "start", "end", "hash")
    return "".join(
        json.dumps({k: getattr(s, k) for k in keys}, ensure_ascii=False) + "\n" for s in segments
    )


@dataclass(frozen=True)
class _Read:
    rel: str
    cfg: SourceCfg
    data: bytes
    normaliser: str
    guarded: GuardResult
    masked: NormalisedText


def _read(root: Path, path: str, config: KbConfig) -> _Read:
    """Read, normalise and guard the original; nothing is written."""
    rel = _relative(root, path)
    if rel == "kb" or rel.startswith("kb/"):
        raise SourceError(f"{rel} is under kb/, which holds the knowledge base itself and cannot be a source")
    cfg = _config_for(rel, config)
    file = root / rel
    if not file.is_file():
        raise SourceError(f"{rel}: no such file")
    if file.stat().st_size > MAX_SOURCE_BYTES:
        raise SourceError(f"{rel} is larger than {MAX_SOURCE_BYTES} bytes (MAX_SOURCE_BYTES)")
    data = file.read_bytes()
    normalised, normaliser = read_document(rel, data)
    guarded = guard(normalised.text)
    masked = NormalisedText(
        guarded.text, [(page, guarded.map_offset(start)) for page, start in normalised.pages]
    )
    return _Read(rel, cfg, data, normaliser, guarded, masked)


def _store_files(read: _Read, previous: list[Segment], next_seg: int, src_id: str) -> tuple[Source, dict[str, bytes]]:
    """The source `src_id` and the bytes of its source.yaml, text.md and segments.jsonl."""
    segments, next_seg = assign_segment_ids(previous, segment(read.masked), next_seg)
    spans = read.guarded.injection_spans
    counts = read.guarded.counts.model_copy(update={
        "injection_segments": [s.id for s in segments if any(s.start < e and a < s.end for a, e in spans)]
    })
    source = Source(
        id=src_id, original=read.rel, original_sha256=hashlib.sha256(read.data).hexdigest(),
        normaliser=read.normaliser, trust=read.cfg.trust, visibility=read.cfg.visibility, guard=counts,
        next_seg=next_seg,
    )
    return source, {
        "source.yaml": dump_yaml(source.model_dump()).encode("utf-8"),
        "text.md": read.masked.text.encode("utf-8"),
        "segments.jsonl": _segments_jsonl(segments).encode("utf-8"),
    }


def derive_store(
    root: Path, path: str, config: KbConfig, previous: list[Segment], next_seg: int, src_id: str | None = None
) -> tuple[Source, dict[str, bytes]]:
    """What add_source would store for `path` (as `src_id`, default its own id) given the earlier segments and
    next_seg, computed in memory."""
    return _store_files(_read(Path(root), path, config), previous, next_seg, src_id or source_id(path))


def add_source(root: Path, path: str, config: KbConfig) -> Source:
    """Store `path` (relative to `root`) as a source; an existing source is re-synced, keeping segment ids.

    Nothing is written until the document is read and guarded; the source directory is replaced atomically.
    """
    root = Path(root)
    read = _read(root, path, config)
    rel = read.rel
    src_id = source_id(rel)
    target = root / "kb/sources" / src_id
    _check_store_dirs(root, target)
    triage = target / "triage.yaml"
    triage_data = None
    if triage.is_symlink() or (triage.exists() and not triage.is_file()):
        raise SourceError(f"{triage.relative_to(root)} is not a regular file; refusing to sync")
    if triage.exists():
        triage_data = triage.read_bytes()
    previous, next_seg = [], 1
    if target.exists():
        existing = KbRepo.load(root)
        old = existing.sources.get(src_id)
        if old is None:
            raise SourceError(f"{target.relative_to(root)} exists but holds no valid source")
        if old.original != rel:
            raise SourceError(f"{src_id} already belongs to {old.original}; {rel} has the same name")
        previous, next_seg = existing.segments(src_id), old.next_seg
    source, store = _store_files(read, previous, next_seg, src_id)

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{src_id}.", dir=parent))
    try:
        for name, data in store.items():
            (tmp / name).write_bytes(data)
        if triage_data is not None:
            (tmp / "triage.yaml").write_bytes(triage_data)
        if target.exists():
            old_dir = tmp.with_name(tmp.name + ".old")
            target.rename(old_dir)
            try:
                tmp.rename(target)
            except BaseException:
                old_dir.rename(target)  # put the previous source back
                raise
            shutil.rmtree(old_dir)
        else:
            tmp.rename(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return source


def sync_sources(root: Path, config: KbConfig) -> list[Source]:
    """Re-read every stored source from its original."""
    repo = KbRepo.load(Path(root))
    return [add_source(root, repo.sources[i].original, config) for i in sorted(repo.sources)]
