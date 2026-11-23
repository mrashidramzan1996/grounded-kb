"""PDF documents (pdfplumber): lines → headings, paragraphs and table rows."""

import io
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass

import pdfplumber

from kb.sources.readers import NormalisedText, SourceError, join_blocks, table_rows

EDGE_LINES = 2  # header/footer zone: top and bottom lines of a page
MIN_REPEAT_PAGES = 2
# Lines of one paragraph are joined when the white space between them is at most this many
# line heights (top of the next line minus bottom of the previous one).
MAX_PARAGRAPH_GAP = 1.0

_PAGE_N_OF_M = re.compile(r"^Page \d+ of \d+$")
_NUMBERING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
_HYPHENATED = re.compile(r"[a-z]-$")


@dataclass
class _Line:
    text: str
    top: float
    bottom: float
    size: float
    bold: bool
    heading: int = 0  # heading level, 0 for body text


@dataclass
class _Block:
    top: float
    text: str
    line: _Line | None = None  # last line of a paragraph/heading; None for a table


def _line(raw: dict) -> _Line:
    chars = [c for c in raw["chars"] if not c["text"].isspace()]
    return _Line(
        text=raw["text"].strip(),
        top=raw["top"],
        bottom=raw["bottom"],
        size=round(max(c["size"] for c in chars), 1),
        bold=all("bold" in c["fontname"].lower() for c in chars),
    )


def _cell(value: str | None) -> str:
    return " ".join((value or "").split())


def _repeated_edges(pages: list[list[_Line]]) -> set[str]:
    seen: dict[str, set[int]] = defaultdict(set)
    for n, lines in enumerate(pages):
        for line in lines[:EDGE_LINES] + lines[-EDGE_LINES:]:
            seen[re.sub(r"\d", "#", line.text)].add(n)
    return {key for key, on in seen.items() if len(on) >= MIN_REPEAT_PAGES}


def _drop_headers_footers(pages: list[list[_Line]]) -> list[list[_Line]]:
    repeated = _repeated_edges(pages)
    kept = []
    for lines in pages:
        edge = set(range(min(EDGE_LINES, len(lines)))) | set(range(max(0, len(lines) - EDGE_LINES), len(lines)))
        kept.append([
            line for i, line in enumerate(lines)
            if not _PAGE_N_OF_M.match(line.text)
            and not (i in edge and re.sub(r"\d", "#", line.text) in repeated)
        ])
    return kept


def _assign_headings(pages: list[list[_Line]]) -> None:
    lines = [line for page in pages for line in page]
    if not lines:
        return
    body = statistics.median(line.size for line in lines for _ in line.text)  # per character
    headings = [line for line in lines if line.bold or line.size > body]
    sizes = sorted({line.size for line in headings}, reverse=True)
    for line in headings:
        m = _NUMBERING.match(line.text)
        depth = len(m.group(1).split(".")) + 1 if m else sizes.index(line.size) + 1
        line.heading = min(depth, 6)


def _continues(prev: _Line, line: _Line) -> bool:
    """Does `line` continue the paragraph (or wrapped heading) that `prev` ends?"""
    if (prev.heading, prev.size, prev.bold) != (line.heading, line.size, line.bold):
        return False
    if line.heading and _NUMBERING.match(line.text):
        return False
    return line.top - prev.bottom <= MAX_PARAGRAPH_GAP * (prev.bottom - prev.top)


def _join(text: str, more: str) -> str:
    if _HYPHENATED.search(text) and more[:1].islower():
        return text[:-1] + more
    return f"{text} {more}"


def _page_blocks(lines: list[_Line], tables: list[tuple[float, str]]) -> list[str]:
    items: list[_Block] = [_Block(top, text) for top, text in tables]
    items += [_Block(line.top, line.text, line) for line in lines]
    items.sort(key=lambda b: b.top)
    blocks: list[_Block] = []
    for item in items:
        prev = blocks[-1] if blocks else None
        if item.line and prev and prev.line and _continues(prev.line, item.line):
            prev.text, prev.line = _join(prev.text, item.text), item.line
        else:
            blocks.append(item)
    return [
        "#" * b.line.heading + " " + b.text if b.line and b.line.heading else b.text
        for b in blocks
    ]


def read(data: bytes) -> NormalisedText:
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:  # pdfminer raises a variety of parse errors
        raise SourceError(f"cannot read PDF: {e}") from e
    with pdf:
        line_pages: list[list[_Line]] = []
        table_pages: list[list[tuple[float, str]]] = []
        for page in pdf.pages:
            found = page.find_tables()
            text_area = page
            for t in found:
                text_area = text_area.outside_bbox(t.bbox)
            rows = [[[_cell(c) for c in row] for row in t.extract()] for t in found]
            table_pages.append([
                (t.bbox[1], "\n".join(table_rows([r for r in rs if any(r)])))
                for t, rs in zip(found, rows) if any(any(r) for r in rs)
            ])
            line_pages.append([_line(raw) for raw in text_area.extract_text_lines(return_chars=True)
                               if raw["text"].strip()])
    line_pages = _drop_headers_footers(line_pages)
    _assign_headings(line_pages)

    blocks: list[str] = []
    pages: list[tuple[int, int]] = []
    offset = 0
    for number, (lines, tables) in enumerate(zip(line_pages, table_pages), start=1):
        pages.append((number, offset))
        for block in _page_blocks(lines, tables):
            blocks.append(block)
            offset += len(block) + 2  # the blank line that follows
    return NormalisedText(join_blocks(blocks), pages)
