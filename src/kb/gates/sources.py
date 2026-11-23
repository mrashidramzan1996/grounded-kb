"""Source gate: the store under kb/sources/ is re-derived from the originals, never trusted (C1).

Every file under kb/ can be edited by an agent, so the stored text, segments, hashes and guard counts prove
nothing by themselves. `rederive` reads the original again (read → guard → segment → assign ids keeping the
stored ids) and compares bytes; `kb check` and `kb ingest --report` both use it.
"""

import hashlib
import re
from pathlib import Path

from kb.config.schema import KbConfig
from kb.gates.base import GateContext
from kb.gitfiles import file_at
from kb.issues import Issue
from kb.records.ids import valid_segment_id
from kb.records.repo import KbRepo
from kb.sources.readers import SourceError
from kb.sources.store import MAX_SOURCE_BYTES, derive_store, source_ids

STORE_NAMES = ("source.yaml", "text.md", "segments.jsonl")
# the files `kb source add` / `kb ingest` write; triage.yaml is the agent's
STORE_FILE = re.compile(r"kb/sources/(SRC-[^/]+)/(?:source\.yaml|text\.md|segments\.jsonl)")


def sha256_file(path: Path) -> str | None:
    """sha256 of the file, or None when it is missing; raises ValueError over MAX_SOURCE_BYTES."""
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"larger than {MAX_SOURCE_BYTES} bytes (MAX_SOURCE_BYTES)")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def stored_ids(repo: KbRepo) -> set[str]:
    """Source ids with at least one store file in `repo`, whether or not it loads."""
    return {m.group(1) for p in repo.raw if (m := STORE_FILE.fullmatch(p))}


def store_changed(src_id: str, head: KbRepo, base: KbRepo) -> bool:
    where = f"kb/sources/{src_id}"
    return any(head.raw.get(f"{where}/{n}") != base.raw.get(f"{where}/{n}") for n in STORE_NAMES)


def bad_segment_ids(repo: KbRepo) -> list[str]:
    """Sources whose segments.jsonl holds ids that are not `seg-NN`."""
    return sorted(s for s in stored_ids(repo) if not all(valid_segment_id(g.id) for g in repo.segments(s)))


def recover_hint(ref: str | None) -> str:
    if ref:
        return f"restore the store files from {ref} and run `kb source sync` once"
    return "run `kb source sync` to rebuild them from the original"


def _contained(root: Path, rel: str) -> bool:
    path = root / rel
    return not path.is_symlink() and path.resolve().is_relative_to(root.resolve())


def rederive(
    root: Path, config: KbConfig, src_id: str, head: KbRepo, base: KbRepo | None, fix: str
) -> list[Issue]:
    """Re-derive `src_id` from its original and compare every store file at head byte for byte.

    The original's path and the segment ids to keep come from `base` when it has the source, else from head
    (no base) or a fresh add (new since base). A store that moved since the base — or any store when there is
    no base to vouch for it — is source.tampered whenever its bytes are not exactly what the re-derivation
    gives, including when the re-derivation cannot run (the cause goes in the message); head's own
    source.yaml is never believed. source.outdated only when the store is the base's and the original moved
    on. source.secret_in_original is counted in the original just read, never taken from stored counts."""
    root = Path(root)
    where = f"kb/sources/{src_id}"
    yaml_path = f"{where}/source.yaml"
    old = base.sources.get(src_id) if base is not None else None
    new = head.sources.get(src_id)
    moved = old is None or store_changed(src_id, head, base)  # type: ignore[arg-type]

    def failed(code: str, cause: str) -> list[Issue]:
        if moved:
            return [Issue("source.tampered", f"{where} cannot be re-derived ({cause}); {fix}", path=yaml_path, record=src_id)]
        return [Issue(code, cause, path=yaml_path, record=src_id)]

    meta = old or new
    if meta is None:
        return [Issue("source.tampered", f"{where} holds no valid source.yaml; {fix}", path=yaml_path, record=src_id)]
    original = meta.original
    if not _contained(root, original):
        return failed("source.invalid_original", f"{original} is a symlink or lies outside the repository")
    if not (root / original).is_file():
        return failed("source.outdated", f"{original} is missing; restore it or remove {where}")
    if src_id not in source_ids(original):
        return [Issue("source.tampered", f"{where} does not belong to {original}; {fix}", path=yaml_path, record=src_id)]
    if old is not None:
        previous, next_seg = base.segments(src_id), old.next_seg  # type: ignore[union-attr]
    elif base is None and new is not None:
        previous, next_seg = head.segments(src_id), new.next_seg  # keeping the stored ids
    else:
        previous, next_seg = [], 1
    try:
        derived, expected = derive_store(root, original, config, previous, next_seg, src_id)
    except (SourceError, OSError, ValueError) as e:
        return failed("source.outdated", f"{original} cannot be stored as it is now: {e}")

    issues = []
    differ = [name for name, data in expected.items() if head.raw.get(f"{where}/{name}") != data]
    if differ and not moved:  # the store is the base's: the original moved on since
        issues.append(Issue("source.outdated", f"{original} changed since it was stored; run `kb source sync`", path=yaml_path, record=src_id))
    elif differ:
        cause = "" if base is not None else " (the store was edited, or the original changed without `kb source sync`)"
        issues += [
            Issue("source.tampered", f"{where}/{name} differs from what {original} gives{cause}; {fix}",
                  path=f"{where}/{name}", record=src_id)
            for name in differ
        ]
    if derived.guard.secrets:
        issues.append(Issue(
            "source.secret_in_original",
            f"{original} contains {derived.guard.secrets} secret(s); remove them from the original and run `kb source sync`",
            path=yaml_path, record=src_id,
        ))
    return issues


def _unchanged_original(ctx: GateContext, src_id: str) -> bytes | None:
    """The original's bytes when they and the store files are identical to the base, else None. The base's
    (identical) store then stands for the re-derivation, which would give what it gave there."""
    assert ctx.base is not None and ctx.base_ref is not None
    src = ctx.repo.sources.get(src_id)
    if src is None or store_changed(src_id, ctx.repo, ctx.base) or not _contained(ctx.root, src.original):
        return None
    path = ctx.root / src.original
    try:
        if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return data if file_at(ctx.root, ctx.base_ref, src.original) == data else None


def _as_at_base(ctx: GateContext, src_id: str, original: bytes) -> list[Issue]:
    src, where = ctx.repo.sources[src_id], f"kb/sources/{src_id}/source.yaml"
    issues = []
    if hashlib.sha256(original).hexdigest() != src.original_sha256:
        issues.append(Issue("source.outdated", f"{src.original} changed since it was stored; run `kb source sync`", path=where, record=src_id))
    if src.guard.secrets > 0:
        issues.append(Issue(
            "source.secret_in_original", f"{src.original} contains {src.guard.secrets} secret(s); remove them from the original and run `kb source sync`",
            path=where, record=src_id,
        ))
    return issues


def source_gate(ctx: GateContext) -> list[Issue]:
    """With a base (ctx.base / ctx.base_ref), sources whose store files or original changed since it are
    re-derived; without one, every source is."""
    fix = recover_hint(ctx.base_ref)
    issues = [
        Issue("source.tampered", f"kb/sources/{s}/segments.jsonl holds invalid segment ids; {fix}",
              path=f"kb/sources/{s}/segments.jsonl", record=s)
        for s in bad_segment_ids(ctx.repo)
    ]
    tampered_ids = {i.record for i in issues}
    issues += [  # M8: a scanned or image-only document gives no text
        Issue("source.empty", f"{src.original} gave no text (scanned or image-only?); nothing in it can be quoted",
              "warning", f"kb/sources/{sid}/source.yaml", sid)
        for sid, src in sorted(ctx.repo.sources.items()) if not ctx.repo.segments(sid)
    ]
    for src_id in sorted(stored_ids(ctx.repo) - tampered_ids):
        original = _unchanged_original(ctx, src_id) if ctx.base is not None else None
        if original is not None:
            issues.extend(_as_at_base(ctx, src_id, original))
        else:
            issues.extend(rederive(ctx.root, ctx.config, src_id, ctx.repo, ctx.base, fix))
    return issues
