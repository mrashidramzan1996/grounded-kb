"""Stable, citable segments of normalised text (spec §6.4)."""

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass, replace

from kb.sources.readers import NormalisedText

MAX_SEGMENT_CHARS = 2000
_HEADING = re.compile(r"(#{1,6}) (.*)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Segment:
    id: str
    heading: str
    page: int
    start: int
    end: int
    hash: str


def _split(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split [start, end) at the sentence boundary nearest its middle while a piece is too long."""
    if end - start <= MAX_SEGMENT_CHARS:
        return [(start, end)]
    middle = (start + end) / 2
    cuts = [(m.start(), m.end()) for m in _SENTENCE_END.finditer(text, start, end) if m.start() > start]
    cuts = [c for c in cuts if c[1] < end]
    if not cuts:
        return [(start, end)]
    cut_end, cut_start = min(cuts, key=lambda c: abs(c[0] - middle))
    return _split(text, start, cut_end) + _split(text, cut_start, end)


def segment(n: NormalisedText) -> list[Segment]:
    """A heading with its paragraphs and lists is one segment; each table row is its own."""
    text = n.text
    spans: list[tuple[str, int, int]] = []  # (heading path, start, end)
    path: list[tuple[int, str]] = []
    heading = ""
    open_start: int | None = None
    open_end = 0

    def close() -> None:
        nonlocal open_start
        if open_start is not None:
            spans.append((heading, open_start, open_end))
            open_start = None

    pos = 0
    for line in text.split("\n"):
        start, end = pos, pos + len(line)
        pos = end + 1
        if not line.strip():
            continue
        if m := _HEADING.fullmatch(line):
            close()
            level = len(m.group(1))
            path = [p for p in path if p[0] < level] + [(level, m.group(2).strip())]
            heading = " > ".join(t for _, t in path)
            open_start = start
        elif line.startswith("| ") and line.endswith(" |"):
            close()
            spans.append((heading, start, end))
            continue
        elif open_start is None:
            open_start = start
        open_end = end
    close()

    page_starts = [s for _, s in n.pages]
    segments = []
    for head, s, e in spans:
        for a, b in _split(text, s, e):
            page = n.pages[max(bisect_right(page_starts, a) - 1, 0)][0] if n.pages else 1
            segments.append(Segment("", head, page, a, b, hashlib.sha256(text[a:b].encode()).hexdigest()[:12]))
    return segments


def assign_segment_ids(
    previous: list[Segment], new: list[Segment], next_seg: int
) -> tuple[list[Segment], int]:
    """Keep ids by exact hash (duplicates in order), then by (heading, occurrence); the rest get fresh numbers."""

    def keyed(segs: list[Segment]) -> list[tuple[str, int]]:
        seen: dict[str, int] = {}
        keys = []
        for s in segs:
            keys.append((s.heading, seen.get(s.heading, 0)))
            seen[s.heading] = seen.get(s.heading, 0) + 1
        return keys

    ids: list[str | None] = [None] * len(new)
    used: set[int] = set()
    for i, s in enumerate(new):
        j = next((j for j, p in enumerate(previous) if j not in used and p.hash == s.hash), None)
        if j is not None:
            ids[i] = previous[j].id
            used.add(j)
    old_by_key = {k: j for j, k in enumerate(keyed(previous))}
    for i, key in enumerate(keyed(new)):
        j = old_by_key.get(key)
        if ids[i] is None and j is not None and j not in used:
            ids[i] = previous[j].id
            used.add(j)
    out = []
    for s, seg_id in zip(new, ids):
        if seg_id is None:
            seg_id = f"seg-{next_seg:02d}"
            next_seg += 1
        out.append(replace(s, id=seg_id))
    return out, next_seg
