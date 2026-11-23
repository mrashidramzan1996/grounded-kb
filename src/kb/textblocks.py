"""Marker-delimited blocks inside text the user owns."""

from typing import Literal


def _markers(name: str, style: Literal["html", "hash"]) -> tuple[str, str]:
    if style == "html":
        return f"<!-- kb:begin {name} -->", f"<!-- kb:end {name} -->"
    return f"# kb:begin {name}", f"# kb:end {name}"


def upsert_block(text: str, name: str, content: str, style: Literal["html", "hash"]) -> str:
    """Return `text` with the block `name` set to `content`: replaced in place if present, else appended.
    Everything outside the markers is untouched; repeating the call with the same content is a no-op.
    Raises ValueError if the markers are unbalanced or repeated."""
    begin, end = _markers(name, style)
    block = f"{begin}\n{content.rstrip(chr(10))}\n{end}\n"
    lines = text.splitlines(keepends=True)
    starts = [i for i, l in enumerate(lines) if l.strip() == begin]
    ends = [i for i, l in enumerate(lines) if l.strip() == end]
    if not starts and not ends:
        if text and not text.endswith("\n"):
            text += "\n"
        return text + ("\n" if text.strip() and not text.endswith("\n\n") else "") + block
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise ValueError(f"unbalanced or repeated '{begin}' / '{end}' markers")
    tail = lines[ends[0] + 1 :]
    return "".join(lines[: starts[0]]) + block + "".join(tail)
