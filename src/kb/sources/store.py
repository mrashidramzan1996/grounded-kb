"""Read, guard, segment and store a source under kb/sources/<id>/ (spec §6)."""

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

from kb.config.schema import KbConfig, SourceCfg
from kb.records.models import Source
from kb.records.repo import KbRepo
from kb.sources.guard import guard
from kb.sources.readers import NormalisedText, SourceError, read_document
from kb.sources.segment import assign_segment_ids, segment
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


def add_source(root: Path, path: str, config: KbConfig) -> Source:
    """Store `path` (relative to `root`) as a source; an existing source is re-synced, keeping segment ids.

    Nothing is written until the document is read and guarded; the source directory is replaced atomically.
    """
    root = Path(root)
    rel = _relative(root, path)
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
    src_id = source_id(rel)
    target = root / "kb/sources" / src_id
    previous, next_seg = [], 1
    if target.exists():
        existing = KbRepo.load(root)
        old = existing.sources.get(src_id)
        if old is None:
            raise SourceError(f"{target.relative_to(root)} exists but holds no valid source")
        if old.original != rel:
            raise SourceError(f"{src_id} already belongs to {old.original}; {rel} has the same name")
        previous, next_seg = existing.segments(src_id), old.next_seg
    segments, next_seg = assign_segment_ids(previous, segment(masked), next_seg)
    spans = guarded.injection_spans
    counts = guarded.counts.model_copy(update={
        "injection_segments": [s.id for s in segments if any(s.start < e and a < s.end for a, e in spans)]
    })
    source = Source(
        id=src_id, original=rel, original_sha256=hashlib.sha256(data).hexdigest(), normaliser=normaliser,
        trust=cfg.trust, visibility=cfg.visibility, guard=counts, next_seg=next_seg,
    )

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{src_id}.", dir=parent))
    try:
        (tmp / "source.yaml").write_text(dump_yaml(source.model_dump()), encoding="utf-8")
        (tmp / "text.md").write_text(masked.text, encoding="utf-8")
        (tmp / "segments.jsonl").write_text(_segments_jsonl(segments), encoding="utf-8")
        if (target / "triage.yaml").exists():
            shutil.copy2(target / "triage.yaml", tmp / "triage.yaml")
        if target.exists():
            old_dir = tmp.with_name(tmp.name + ".old")
            target.rename(old_dir)
            tmp.rename(target)
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
