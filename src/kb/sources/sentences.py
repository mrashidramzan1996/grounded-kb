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
_UNIT_START = re.compile(r"\s*(?:[-*+] |\d+[.)] |\| )")
_MARKER = re.compile(r"\s*(?:[-*+] |\d+[.)] |\| )")
_FENCE = re.compile(r"\s*(?:```|~~~)")
_SEPARATOR = re.compile(r"\|?[\s:|-]+\|?")
_HEADING_LINE = re.compile(r"#{1,6} ")

NORMATIVE_CUES: dict[str, re.Pattern] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "must": r"\b(?:must(?!\s+not\b)|shall(?!\s+not\b)|required|requires|is required to|has to|needs to)\b",
        "must_not": r"\b(?:must not|shall not|may not|cannot|can not|never|prohibited|not allowed|no longer)\b",
        "should": r"\b(?:should|recommended)\b",
        "may": r"\bmay\b(?!\s+not\b)",
        "limit": r"\b(?:at least|at most|no more than|no less than|up to|within|maximum|minimum|only)\b",
        # a quantity: a number with a currency, a percentage or a unit (a bare year, page or section number is not one)
        "number": r"(?:[$€£]\s?|\b(?:EUR|USD|GBP)\s?)\d+(?:[.,]\d+)*"
                  r"|\b\d+(?:[.,]\d+)*(?:\s?(?:%|[$€£])|\s?(?:EUR|USD|GBP|seconds?|minutes?|hours?|days?|weeks?|months?"
                  r"|years?|ms|s|KB|MB|GB|TB|seats?|users?|requests?|times|per)\b)",
    }.items()
}
# never quantities: masked placeholders ([EMAIL-1], [SECRET:aws]), ISO dates and times of day
_NOT_QUANTITIES = re.compile(r"\[(?:EMAIL-\d+|SECRET:[^\]\n]*)\]|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")


@dataclass(frozen=True)
class Sentence:
    ref: str
    index: int
    start: int
    end: int
    text: str
    cues: frozenset[str]


def cues(text: str) -> frozenset[str]:
    plain = _NOT_QUANTITIES.sub(" ", text)
    return frozenset(name for name, pat in NORMATIVE_CUES.items() if pat.search(plain if name == "number" else text))


def is_normative(s: Sentence) -> bool:
    return bool(s.cues)


def sentence_key(s: Sentence) -> str:
    body = _MARKER.sub("", s.text, count=1)
    return hashlib.sha256(fold(body)[0].strip().encode()).hexdigest()[:12]


def _blocks(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Units of [start, end): a paragraph, a list item or a table row; headings, blanks, table
    separators and fenced code are skipped."""
    blocks: list[list[int]] = []
    open_block: list[int] | None = None
    in_fence = False
    pos = start
    for raw in text[start:end].split("\n"):
        ls, le = pos, pos + len(raw)
        pos = le + 1
        line = raw.rstrip("\r")
        le = ls + len(line)
        if _FENCE.match(line):
            in_fence = not in_fence
            open_block = None
        elif in_fence or not line.strip() or _HEADING_LINE.match(line) or "-" in line and line.lstrip().startswith("|") and _SEPARATOR.fullmatch(line.strip()):
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
        if cur == start and re.fullmatch(r"\s*\d+[.)]", text[cur : m.start()]):
            continue  # the number of a numbered list item is not a sentence
        before = text[cur : m.start()].split()
        # Known trade-off: "No." is protected, so it never ends a sentence.
        if before and before[-1].lower().lstrip("([{\"'") in _ABBREVIATIONS:
            continue
        out.append((cur, m.start()))
        cur = m.end()
    out.append((cur, end))
    return out


def segment_sentences(text: str, seg: Segment, src_id: str) -> list[Sentence]:
    result: list[Sentence] = []
    for bs, be in _blocks(text, seg.start, seg.end):
        for s, e in _split_block(text, bs, be):
            piece = text[s:e].rstrip()
            if not piece:
                continue
            e = s + len(piece)
            result.append(Sentence(f"{src_id}#{seg.id}", len(result), s, e, piece, cues(piece)))
    return result


def sentences(repo: "KbRepo", src_id: str) -> list[Sentence]:
    text = repo.source_text(src_id)
    out: list[Sentence] = []
    for seg in repo.segments(src_id):
        out.extend(segment_sentences(text, seg, src_id))
    return out
