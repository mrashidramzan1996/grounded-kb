"""Document readers: turn a product document into normalised text (spec §6.2).

Output conventions shared by every reader: `#` heading lines, one blank line between
blocks, `- ` list items, one line per table row (`| Header: value | … |`), LF line
endings and a single final newline.
"""

from dataclasses import dataclass
from pathlib import PurePath


MAX_TEXT_CHARS = 5_000_000  # extracted characters per document (docx and pdf readers)


class SourceError(Exception):
    pass


@dataclass(frozen=True)
class NormalisedText:
    text: str
    pages: list[tuple[int, int]]  # (page number, start character)


def row_line(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def table_rows(rows: list[list[str]]) -> list[str]:
    """One line per data row, each cell prefixed by its column header (the first row)."""
    if len(rows) == 1:
        return [row_line(rows[0])]
    header, *body = rows
    return [row_line([f"{h}: {v}" if h else v for h, v in zip(header, row)]) for row in body]


def join_blocks(blocks: list[str]) -> str:
    return "\n\n".join(blocks) + "\n" if blocks else ""


_READERS = {".md": "text", ".txt": "text", ".pdf": "pdf", ".docx": "docx"}


def read_document(path: str, data: bytes) -> tuple[NormalisedText, str]:
    """Read `data` (the file at `path`) with the reader for its suffix; returns (text, normaliser id)."""
    suffix = PurePath(path).suffix.lower()
    name = _READERS.get(suffix)
    if name is None:
        raise SourceError(f"no reader for {suffix or path} (available: md, txt, pdf, docx)")
    if name == "text":
        from kb.sources.readers import text as reader
    elif name == "pdf":
        from kb.sources.readers import pdf as reader
    else:
        from kb.sources.readers import docx as reader
    return reader.read(data), f"{name}@1"
