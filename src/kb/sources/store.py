"""Read, guard, segment and store a source under kb/sources/<id>/ (spec §6)."""

import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
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


def _slug_id(text: str, path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        raise SourceError(f"cannot derive a source id from {path}")
    return f"SRC-{slug}"


def source_id(path: str) -> str:
    """The usual id: SRC- and the file stem."""
    return _slug_id(PurePosixPath(path).stem, path)


def path_source_id(path: str) -> str:
    """The id for documents whose stems collide: SRC- and the repo-relative path without its extension."""
    return _slug_id(PurePosixPath(path).with_suffix("").as_posix(), path)


def source_ids(path: str) -> set[str]:
    """The ids a source of `path` may have."""
    return {source_id(path), path_source_id(path)}


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
    try:
        normalised, normaliser = read_document(rel, data)
    except SourceError as e:
        raise SourceError(e.args[0] if str(e).startswith(rel) else f"{rel}: {e}") from e
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


def assign_source_ids(repo: KbRepo, rels: list[str]) -> dict[str, str]:
    """The id each document gets. A stored document keeps its id. A new one gets `SRC-<file stem>`, unless
    another new document or a stored source with a different original has that stem: then every colliding
    new document gets the id of its whole path (SRC-docs-api-readme for docs/api/README.md)."""
    by_original = {s.original: sid for sid, s in repo.sources.items()}
    taken = set(by_original.values()) | {p.split("/")[2] for p in repo.raw if p.startswith("kb/sources/")}
    new = [r for r in dict.fromkeys(rels) if r not in by_original]
    stems = Counter(source_id(r) for r in new)
    out = {r: by_original[r] for r in rels if r in by_original}
    for rel in new:
        sid = source_id(rel)
        if stems[sid] > 1 or sid in taken:
            sid = path_source_id(rel)
        if sid in taken or sid in out.values():
            raise SourceError(f"{rel}: its source id {sid} is already taken; rename the document")
        out[rel] = sid
    return out


@dataclass(frozen=True)
class _Prepared:
    target: Path
    source: Source
    files: dict[str, bytes]  # including the kept triage.yaml


def _prepare(root: Path, read: _Read, src_id: str, repo: KbRepo) -> _Prepared:
    """Everything add_source writes for `read`, computed and checked without writing."""
    target = root / "kb/sources" / src_id
    _check_store_dirs(root, target)
    triage = target / "triage.yaml"
    if triage.is_symlink() or (triage.exists() and not triage.is_file()):
        raise SourceError(f"{triage.relative_to(root)} is not a regular file; refusing to sync")
    previous, next_seg = [], 1
    if target.exists():
        old = repo.sources.get(src_id)
        if old is None:
            raise SourceError(f"{target.relative_to(root)} exists but holds no valid source")
        if old.original != read.rel:
            raise SourceError(f"{src_id} already belongs to {old.original}; {read.rel} has the same name")
        previous, next_seg = repo.segments(src_id), old.next_seg
    source, files = _store_files(read, previous, next_seg, src_id)
    if triage.exists():
        files = {**files, "triage.yaml": triage.read_bytes()}
    return _Prepared(target, source, files)


def _write(prepared: _Prepared) -> None:
    """Replace the source directory atomically."""
    target, parent = prepared.target, prepared.target.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
    try:
        for name, data in prepared.files.items():
            (tmp / name).write_bytes(data)
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


def add_sources(root: Path, paths: list[str], config: KbConfig) -> list[Source]:
    """Store each of `paths` (relative to `root`) as a source; an existing source is re-synced, keeping its id
    and segment ids. Every document is read, guarded and given its id before anything is written, so one
    bad document leaves the store untouched; each source directory is then replaced atomically."""
    root = Path(root)
    reads = [_read(root, p, config) for p in paths]
    repo = KbRepo.load(root)
    ids = assign_source_ids(repo, [r.rel for r in reads])
    prepared = [_prepare(root, r, ids[r.rel], repo) for r in {r.rel: r for r in reads}.values()]
    for p in prepared:
        _write(p)
    return [p.source for p in prepared]


def add_source(root: Path, path: str, config: KbConfig) -> Source:
    """add_sources for one document."""
    return add_sources(root, [path], config)[0]


def sync_sources(root: Path, config: KbConfig) -> list[Source]:
    """Re-read every stored source from its original."""
    repo = KbRepo.load(Path(root))
    return add_sources(root, [repo.sources[i].original for i in sorted(repo.sources)], config)
