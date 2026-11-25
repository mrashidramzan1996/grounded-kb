"""`kb trace`: record -> quote -> segment (heading, page) -> original file, with every quote re-checked.

`SRC-…#seg-NN` lists every requirement, exclusion, decision, invariant and term whose quotes (a term's evidence and
relation quotes) cite that segment.
"""

from pathlib import Path

from kb.gates.quotes import quote_results, too_short
from kb.quotes.match import find_quote
from kb.records.lifecycle import superseded_by
from kb.records.models import (
    QUOTED_TYPES,
    Component,
    Decision,
    Exclusion,
    Feature,
    Invariant,
    Quote,
    Requirement,
    Term,
)
from kb.records.repo import KbRepo
from kb.sources.segment import Segment
from kb.textfmt import inline_code

NOT_FOUND = "QUOTE NOT FOUND"


class TraceError(ValueError):
    pass


def _summary(rec) -> tuple[str, str]:
    """(kind, main text) of a record."""
    if isinstance(rec, Requirement):
        return "requirement", rec.statement
    if isinstance(rec, Decision):
        return "decision", rec.title
    if isinstance(rec, Invariant):
        return "invariant", rec.statement
    if isinstance(rec, Feature):
        return "feature", rec.title
    if isinstance(rec, Component):
        return "component", rec.title
    if isinstance(rec, Term):
        return "term", rec.name
    if isinstance(rec, Exclusion):
        return "exclusion", rec.statement
    raise TraceError(f"no trace summary for record type {type(rec).__name__}")


def _state(repo: KbRepo, gone: dict[str, str], rid: str) -> str:
    if repo.records[rid].status == "rejected":
        return "rejected"
    return f"superseded by {gone[rid]}" if rid in gone else "live"


def _segment(repo: KbRepo, ref: str) -> Segment | None:
    src, _, seg_id = ref.partition("#")
    return next((s for s in repo.segments(src) if s.id == seg_id), None) if src in repo.sources else None


def _segment_lines(repo: KbRepo, ref: str, seg: Segment) -> list[str]:
    src = repo.sources[ref.partition("#")[0]]
    return [
        f"[{ref}] segment {inline_code(seg.heading)}, page {seg.page}, chars {seg.start}-{seg.end}",
        f"[{src.id}] original {inline_code(src.original)} (sha256 {src.original_sha256})",
    ]


def _quote_line(repo: KbRepo, rid: str, q: Quote) -> tuple[str, bool]:
    seg = _segment(repo, q.source)
    if seg is None:
        return f"[{rid}] {NOT_FOUND}: {q.source} does not exist: {inline_code(q.text)}", False
    at = find_quote(repo.source_text(q.source.partition("#")[0])[seg.start : seg.end], q.text)
    if at is None or too_short(q.text):
        return f"[{rid}] {NOT_FOUND} in {q.source}: {inline_code(q.text)}", False
    return f"[{rid}] quote {inline_code(q.text)} found in {q.source} at chars {seg.start + at[0]}-{seg.start + at[1]}", True


def _quote_chain(repo: KbRepo, rid: str, q: Quote) -> tuple[list[str], bool]:
    line, found = _quote_line(repo, rid, q)
    seg = _segment(repo, q.source)
    return [line, *(_segment_lines(repo, q.source, seg) if seg is not None else [])], found


def _record_chain(repo: KbRepo, rid: str) -> tuple[list[str], bool]:
    rec = repo.records[rid]
    kind, text = _summary(rec)
    lines = [f"[{rid}] {kind} ({_state(repo, superseded_by(repo), rid)}): {inline_code(text)}"]
    evidence = getattr(rec, "evidence", None)
    relations = rec.relations if isinstance(rec, Term) else []
    ok = True
    if evidence is None and not relations:
        return lines + [f"[{rid}] no quotes"], True
    if evidence is not None:
        found_quotes, _ = quote_results(repo, evidence)
        ok = bool(found_quotes) if evidence.mode == "any" else len(found_quotes) == len(evidence.quotes)
        for q in evidence.quotes:
            lines += _quote_chain(repo, rid, q)[0]
    for r in relations:
        lines.append(f"[{rid}] relation {r.type} {r.target}")
        more, found = _quote_chain(repo, rid, r.quote)
        lines += more
        ok &= found
    return lines, ok


def _segment_chain(repo: KbRepo, ref: str) -> tuple[list[str], bool]:
    seg = _segment(repo, ref)
    if seg is None:
        raise TraceError(f"unknown segment {ref}")
    lines, ok, gone = _segment_lines(repo, ref, seg), True, superseded_by(repo)
    for rid, rec in sorted(repo.records.items()):
        if not isinstance(rec, (*QUOTED_TYPES, Term)):
            continue
        quotes = [*(rec.evidence.quotes if rec.evidence else []), *(r.quote for r in getattr(rec, "relations", []))]
        for q in quotes:
            if q.source == ref:
                line, found = _quote_line(repo, rid, q)
                ok &= found
                kind, _ = _summary(rec)
                lines.append(line.replace(f"[{rid}] ", f"[{rid}] {kind} ({_state(repo, gone, rid)}) ", 1))
    if len(lines) == 2:
        lines.append(f"[{ref}] no record cites this segment")
    return lines, ok


def trace_chain(root: Path, id: str) -> tuple[list[str], bool]:
    """(lines, every quote found). Raises TraceError for an unknown id or segment."""
    repo = KbRepo.load(Path(root))
    if "#" in id:
        return _segment_chain(repo, id)
    if id not in repo.records:
        hint = "; give a segment (SRC-…#seg-NN)" if id in repo.sources else ""
        raise TraceError(f"unknown id {id}{hint}")
    return _record_chain(repo, id)


def trace(root: Path, id: str) -> list[str]:
    return trace_chain(root, id)[0]
