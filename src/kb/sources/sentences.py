"""Sentence units inside segments, and rule-like (normative) cues (0.4.0 evaluation layer)."""

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kb.quotes.match import fold
from kb.sources.segment import Segment

if TYPE_CHECKING:
    from kb.records.repo import KbRepo

_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "u.s.", "no.", "fig.", "approx.", "incl.", "resp.")
_UNIT_START = re.compile(r"(?:[-*+] |\d+[.)] |\| )")
_HEADING_LINE = re.compile(r"#{1,6} ")

NORMATIVE_CUES: dict[str, re.Pattern] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "must": r"\b(?:must|shall|required|requires|is required to|has to|needs to)\b",
        "must_not": r"\b(?:must not|shall not|may not|cannot|can not|never|prohibited|not allowed|no longer)\b",
        "should": r"\b(?:should|recommended)\b",
        "may": r"\bmay\b(?!\s+not\b)",
        "limit": r"\b(?:at least|at most|no more than|no less than|up to|within|maximum|minimum|only)\b",
        "number": r"\b\d+(?:[.,]\d+)*(?:\s?%|\b)",
    }.items()
}


@dataclass(frozen=True)
class Sentence:
    ref: str
    index: int
    start: int
    end: int
    text: str
    cues: frozenset[str]


def cues(text: str) -> frozenset[str]:
    return frozenset(name for name, pat in NORMATIVE_CUES.items() if pat.search(text))


def is_normative(s: Sentence) -> bool:
    return bool(s.cues)


def sentence_key(s: Sentence) -> str:
    return hashlib.sha256(fold(s.text)[0].encode()).hexdigest()[:12]


def _blocks(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Units of [start, end): a paragraph, a list item or a table row; headings and blanks are skipped."""
    blocks: list[list[int]] = []
    open_block: list[int] | None = None
    pos = start
    for line in text[start:end].split("\n"):
        ls, le = pos, pos + len(line)
        pos = le + 1
        if not line.strip() or _HEADING_LINE.match(line):
            open_block = None
        elif open_block is not None and not _UNIT_START.match(line):
            open_block[1] = le  # a wrapped continuation line
        else:
            open_block = [ls, le]
            blocks.append(open_block)
            if line.startswith("| "):
                open_block = None  # a table row is always exactly one line
    return [(b[0], b[1]) for b in blocks]


def _split_block(text: str, start: int, end: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    cur = start
    for m in _BOUNDARY.finditer(text, start, end):
        before = text[cur : m.start()].split()
        if before and before[-1].lower() in _ABBREVIATIONS:
            continue
        out.append((cur, m.start()))
        cur = m.end()
    out.append((cur, end))
    return out


def segment_sentences(text: str, seg: Segment, src_id: str) -> list[Sentence]:
    result: list[Sentence] = []
    for bs, be in _blocks(text, seg.start, seg.end):
        for s, e in _split_block(text, bs, be):
            piece = text[s:e]
            if not piece.strip():
                continue
            result.append(Sentence(f"{src_id}#{seg.id}", len(result), s, e, piece, cues(piece)))
    return result


def sentences(repo: "KbRepo", src_id: str) -> list[Sentence]:
    text = repo.source_text(src_id)
    out: list[Sentence] = []
    for seg in repo.segments(src_id):
        out.extend(segment_sentences(text, seg, src_id))
    return out
