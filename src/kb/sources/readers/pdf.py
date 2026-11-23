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
HEADER_BAND = 3.0  # a repeated header/footer line sits at the same height (in points) on each page
MAX_HEADING_CHARS = 120
SENTENCE_END = (".", "!", "?", ":")  # text ending like this is never a heading
# Lines of one paragraph are joined when the white space between them is at most this many
# line heights (top of the next line minus bottom of the previous one).
MAX_PARAGRAPH_GAP = 1.0

_PAGE_N_OF_M = re.compile(r"^Page \d+ of \d+$")
_NUMBERING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S)")
_HYPHENATED = re.compile(r"[a-z]-$")


@dataclass
class _Line:
    text: str
    top: float
    bottom: float
    size: float
    bold: bool


@dataclass
class _Block:
    top: float
    text: str
    line: _Line | None = None  # last line of a paragraph or heading; None for a table


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


def _key(line: _Line) -> str:
    return re.sub(r"\d", "#", line.text)


def _edges(lines: list[_Line]) -> list[_Line]:
    return [line for i, line in enumerate(lines) if i < EDGE_LINES or i >= len(lines) - EDGE_LINES]


def _drop_headers_footers(pages: list[list[_Line]]) -> list[list[_Line]]:
    """Drop `Page N of M` lines, and edge lines repeated (digits aside) at the same height on ≥ 2 pages."""
    by_key: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for n, lines in enumerate(pages):
        for line in _edges(lines):
            by_key[_key(line)].append((n, line.top))

    def repeated(line: _Line) -> bool:
        on = {n for n, top in by_key[_key(line)] if abs(top - line.top) <= HEADER_BAND}
        return len(on) >= MIN_REPEAT_PAGES

    kept = []
    for lines in pages:
        edge = {id(line) for line in _edges(lines)}
        kept.append([
            line for line in lines
            if not _PAGE_N_OF_M.match(line.text) and not (id(line) in edge and repeated(line))
        ])
    return kept


def _numbering_depth(text: str) -> int:
    """`3. Title` → 1, `3.2 Title` → 2; 0 unless the numbering is followed by a capitalised title."""
    m = _NUMBERING.match(text)
    return len(m.group(1).split(".")) if m and m.group(2).isupper() else 0


def _continues(prev: _Line, line: _Line, styled: bool) -> bool:
    """Does `line` continue the paragraph (or wrapped heading) that `prev` ends?"""
    if (prev.size, prev.bold) != (line.size, line.bold):
        return False
    if styled and _numbering_depth(line.text):
        return False
    return line.top - prev.bottom <= MAX_PARAGRAPH_GAP * (prev.bottom - prev.top)


def _join(text: str, more: str) -> str:
    if _HYPHENATED.search(text) and more[:1].islower():
        return text[:-1] + more
    return f"{text} {more}"


def _page_blocks(lines: list[_Line], tables: list[tuple[float, str]], body: float) -> list[_Block]:
    items: list[_Block] = [_Block(top, text) for top, text in tables]
    items += [_Block(line.top, line.text, line) for line in lines]
    items.sort(key=lambda b: b.top)
    blocks: list[_Block] = []
    for item in items:
        prev = blocks[-1] if blocks else None
        if item.line and prev and prev.line and _continues(prev.line, item.line, _styled(item.line, body)):
            prev.text, prev.line = _join(prev.text, item.text), item.line
        else:
            blocks.append(item)
    return blocks


def _styled(line: _Line, body: float) -> bool:
    return line.bold or line.size > body


def _is_heading(block: _Block, body: float) -> bool:
    return (
        block.line is not None
        and _styled(block.line, body)
        and len(block.text) <= MAX_HEADING_CHARS
        and not block.text.endswith(SENTENCE_END)
    )


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
    sizes = [line.size for page in line_pages for line in page for _ in line.text]  # per character
    body = statistics.median(sizes) if sizes else 0.0
    page_blocks = [_page_blocks(lines, tables, body) for lines, tables in zip(line_pages, table_pages)]
    heading_sizes = sorted(
        {b.line.size for blocks in page_blocks for b in blocks if _is_heading(b, body)}, reverse=True
    )

    def render(block: _Block) -> str:
        if not _is_heading(block, body):
            return block.text
        depth = _numbering_depth(block.text)
        level = depth + 1 if depth else heading_sizes.index(block.line.size) + 1
        return "#" * min(level, 6) + " " + block.text

    out: list[str] = []
    pages: list[tuple[int, int]] = []
    offset = 0
    for number, blocks in enumerate(page_blocks, start=1):
        pages.append((number, offset))
        for block in blocks:
            out.append(render(block))
            offset += len(out[-1]) + 2  # the blank line that follows
    return NormalisedText(join_blocks(out), pages)
