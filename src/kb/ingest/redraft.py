"""Which records `kb redraft` should look at: those citing changed text, flagged, or whose quote vanished."""

from kb.quotes.match import find_quote
from kb.records.lifecycle import live_ids
from kb.records.models import Decision, Invariant, Requirement
from kb.records.repo import KbRepo

_Cited = Requirement | Decision | Invariant


def segment_hashes(repo: KbRepo) -> dict[str, str]:
    """'SRC-x#seg-NN' -> hash, for every stored source."""
    return {f"{src}#{seg.id}": seg.hash for src in repo.sources for seg in repo.segments(src)}


def changed_segments(before: KbRepo, after: KbRepo) -> set[str]:
    """Refs of `before` whose hash differs at `after`, or that are gone there."""
    now = segment_hashes(after)
    return {ref for ref, h in segment_hashes(before).items() if now.get(ref) != h}


def _live_cited(repo: KbRepo) -> list[tuple[str, _Cited]]:
    live = live_ids(repo)
    return [(rid, rec) for rid, rec in sorted(repo.records.items()) if rid in live and isinstance(rec, (Requirement, Decision, Invariant))]


def citing(repo: KbRepo, refs: set[str]) -> list[str]:
    """Live REQ/ADR/INV ids with a quote citing any of `refs`."""
    return [rid for rid, rec in _live_cited(repo) if rec.evidence and any(q.source in refs for q in rec.evidence.quotes)]


def flagged(repo: KbRepo) -> list[str]:
    """Live requirement ids with a non-empty `flags`."""
    return [rid for rid, rec in _live_cited(repo) if isinstance(rec, Requirement) and rec.flags]


def _quote_found(repo: KbRepo, ref: str, text: str) -> bool:
    src, _, seg_id = ref.partition("#")
    seg = next((s for s in repo.segments(src) if s.id == seg_id), None) if src in repo.sources else None
    return seg is not None and find_quote(repo.source_text(src)[seg.start : seg.end], text) is not None


def unfound(repo: KbRepo) -> list[str]:
    """Live REQ/ADR/INV ids with a quote not found in its segment (or whose segment is missing)."""
    return [rid for rid, rec in _live_cited(repo) if rec.evidence and not all(_quote_found(repo, q.source, q.text) for q in rec.evidence.quotes)]
