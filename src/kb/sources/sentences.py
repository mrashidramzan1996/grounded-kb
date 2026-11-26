"""Sentence units inside segments, and rule-like (normative) cues (0.4.0 evaluation layer)."""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from kb.quotes.match import fold
from kb.sources.segment import Segment

if TYPE_CHECKING:
    from kb.records.repo import KbRepo

_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "u.s.", "no.", "fig.", "approx.", "incl.", "resp.",
                  "dr.", "mr.", "mrs.", "ms.", "inc.", "ltd.", "co.", "st.")
_UNIT_START = re.compile(r"\s*(?:[-*+] |\d+[.)] |\| )")
_MARKER = re.compile(r"\s*(?:[-*+] |\d+[.)] |\| )")
_FENCE = re.compile(r"\s*(?:```|~~~)")
_SEPARATOR = re.compile(r"\|?[\s:|-]+\|?")
_HEADING_LINE = re.compile(r"#{1,6} ")
_BREAK = re.compile(r"(?m)^(?:#{1,6} |\s*(?:```|~~~))")

# "can" and "will" in descriptive prose ("You can find...", "This document will describe...") are not rules.
# Third-person uses ("The tool can parse YAML") are accepted false positives: a missed rule costs more than a dismissed item.
_NOT_RULE_SUBJECT = (r"(?<!\byou\s)(?<!\bwe\s)(?<!this\sdocument\s)(?<!this\ssection\s)(?<!this\sguide\s)(?<!this\spage\s)")
# lowercase "will", or a sentence-initial "Will" followed by a lowercase word (not the name "Will Smith")
_WILL = r"(?-i:will|Will(?=\s+[a-z]))"
_NUMBER_WORDS = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen"
                 r"|seventeen|eighteen|nineteen|twenty|hundred|thousand)")
_UNITS = (r"(?:seconds?|minutes?|mins?|hours?|hrs?|h|days?|weeks?|months?|years?|ms|s|KB|MB|GB|TB|seats?|users?"
          r"|requests?|times|per|rows?|items?|records?|characters?|invoices?|attempts?)")

NORMATIVE_CUES: dict[str, re.Pattern] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "must": r"\b(?:must(?!\s+(?:not|never)\b)|shall(?!\s+not\b)|required|requires?|is required to|has to|needs to"
                r"|limited\s+to|expected\s+to|entitled\s+to"
                r"|" + _NOT_RULE_SUBJECT + _WILL + r"(?!\s+(?:not|never)\b)|mandatory|ensures?|are\s+to)\b",  # not "is to": "The goal of this section is to explain"
        "must_not": r"\b(?:must\s+not|mustn['\u2019]t|shouldn['\u2019]t|(?:is|are)n['\u2019]t\s+allowed|shall\s+not|may\s+not|can\s*not|can['\u2019]t|won['\u2019]t|never|prohibited|forbidden"
                    r"|not\s+(?:be\s+)?permitted|not\s+allowed|no\s+longer|" + _NOT_RULE_SUBJECT + _WILL + r"\s+not)\b",
        "should": r"\b(?:should(?!n['\u2019]t)|recommended)\b",
        "may": r"\bmay\b(?!\s+not\b)|" + _NOT_RULE_SUBJECT + r"\bcan\b(?!\s*not\b|['\u2019]t)|\boptional\b"
               r"|(?:is|are)\s+allowed\s+to\b"
               r"|(?<!not\s)(?<!never\s)(?<!longer\s)(?<!not\sbe\s)(?<!never\sbe\s)(?<!longer\sbe\s)\bpermitted\b",
        "limit": r"\b(?:at least|at most|no more than|no less than|up to|within|maximum|minimum|only|limited\s+to)\b",
        # a quantity: a number with a currency, a percentage or a unit (a bare year, page or section number is not one)
        "number": r"(?:[$€£]\s?|\b(?:EUR|USD|GBP)\s?)\d+(?:[.,]\d+)*"
                  r"|\b\d+(?:[.,]\d+)*(?:\s?(?:%|[$€£])|\s?(?:EUR|USD|GBP|" + _UNITS[3:-1] + r")\b)"
                  r"|\b" + _NUMBER_WORDS + r"\s+" + _UNITS + r"\b"
                  r"|\b24/7\b|(?-i:\b[A-Z]+-\d+\b)",
    }.items()
}
# never quantities: masked placeholders ([EMAIL-1], [SECRET:aws]), ISO dates, times of day and kb record IDs
_NOT_QUANTITIES = re.compile(r"\[(?:EMAIL-\d+|SECRET:[^\]\n]*)\]|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b"
                             r"|\b(?:REQ-F|REQ-NF|FEAT|ADR|INV|EXCL|COMP|TERM|CLAIM|SRC)-\d+\b")
_MODAL = ("must", "must_not", "should", "may")
_CLAUSE_SEP = re.compile(r";\s+|,\s+(?:and|or|but)\s+")


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


@lru_cache(maxsize=1 << 16)  # pure; kb asks for the same sentence's key many times (decisions, findings, gaps)
def text_key(text: str) -> str:
    """The sentence key of a sentence's text: what sentence_key gives for a sentence of that text."""
    body = _MARKER.sub("", text, count=1)
    return hashlib.sha256(fold(body)[0].strip().encode()).hexdigest()[:12]


def sentence_key(s: Sentence) -> str:
    return text_key(s.text)


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


def _split_clauses(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split a unit at "; " and ", and/or/but" when the clause after the separator has a modal cue of its own."""
    if text[start:end].startswith("| "):
        return [(start, end)]
    out: list[tuple[int, int]] = []
    cur = start
    seps = list(_CLAUSE_SEP.finditer(text, start, end))
    for i, m in enumerate(seps):
        nxt = seps[i + 1].start() if i + 1 < len(seps) else end
        if cues(text[m.end() : nxt]).isdisjoint(_MODAL):
            continue
        if text[cur : m.start()].strip():
            out.append((cur, m.start()))
            cur = m.end()
    out.append((cur, end))
    return out


def segment_sentences(text: str, seg: Segment, src_id: str) -> list[Sentence]:
    result: list[Sentence] = []
    inherited: frozenset[str] = frozenset()
    prev_end = seg.start
    for bs, be in _blocks(text, seg.start, seg.end):
        is_item = bool(_UNIT_START.match(text[bs:be]))
        if not is_item or _BREAK.search(text, prev_end, bs):
            inherited = frozenset()  # a lead-in reaches only the items right after it, not past a heading or code
        prev_end = be
        for ss, se in _split_block(text, bs, be):
            for s, e in _split_clauses(text, ss, se):
                piece = text[s:e].rstrip()
                if not piece:
                    continue
                e = s + len(piece)
                c = cues(piece)
                if is_item and c.isdisjoint(_MODAL):
                    c = c | inherited  # an item with a modal of its own keeps only that one
                result.append(Sentence(f"{src_id}#{seg.id}", len(result), s, e, piece, c))
        if not is_item and text[bs:be].rstrip().endswith(":"):
            inherited = frozenset(result[-1].cues & set(_MODAL))
    return result


def sentences(repo: "KbRepo", src_id: str) -> list[Sentence]:
    """Every sentence of the stored source, in document order; computed once per source and repository (a fresh list
    each call, so a caller may change it)."""
    cache = repo.memo.setdefault("sentences", {})
    if src_id not in cache:
        text = repo.source_text(src_id)
        out: list[Sentence] = []
        for seg in repo.segments(src_id):
            out.extend(segment_sentences(text, seg, src_id))
        cache[src_id] = tuple(out)
    return list(cache[src_id])
