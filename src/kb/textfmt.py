"""Rendering untrusted text as Markdown data that cannot break out of its span."""

import re

_BACKTICKS = re.compile(r"`+")
# every character str.splitlines() treats as a line boundary
_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c\x1d\x1e\x85  ]")


def longest_backticks(text: str) -> int:
    return max((len(m.group()) for m in _BACKTICKS.finditer(text)), default=0)


def inline_code(text: str) -> str:
    """`text` on one line as inline code it cannot break out of (line breaks become spaces)."""
    if not text:
        return "(empty)"
    flat = _LINE_BREAK.sub(" ", text)
    ticks = "`" * (longest_backticks(flat) + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return f"{ticks}{pad}{flat}{pad}{ticks}"
