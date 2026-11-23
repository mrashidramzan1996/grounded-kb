"""Whitespace- and typography-tolerant quote matching (case-sensitive)."""

import re
import unicodedata

_CHAR_MAP = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."}


def fold(text: str) -> tuple[str, list[int]]:
    """Fold typography and whitespace; also return folded index -> original index.

    The map has one extra entry: len(folded) -> len(text).
    """
    out: list[str] = []
    mapping: list[int] = []
    in_space = False
    for i, ch in enumerate(text):
        piece = unicodedata.normalize("NFKC", ch)
        piece = "".join(_CHAR_MAP.get(c, c) for c in piece)
        for c in piece:
            if c.isspace():
                if in_space:
                    continue
                in_space, c = True, " "
            else:
                in_space = False
            out.append(c)
            mapping.append(i)
    mapping.append(len(text))
    return "".join(out), mapping


def find_quote(haystack: str, quote: str) -> tuple[int, int] | None:
    """Original [start, end) of `quote` inside `haystack`, or None."""
    needle = fold(quote)[0].strip()
    if not needle:
        return None
    folded, mapping = fold(haystack)
    at = folded.find(needle)
    if at < 0:
        return None
    return mapping[at], mapping[at + len(needle) - 1] + 1
