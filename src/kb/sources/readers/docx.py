"""Word documents (python-docx), walked in document order. No text is dropped silently:
content controls and nested tables are read, and any other element holding text is an error."""

import io
import re
import zipfile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from kb.sources.readers import NormalisedText, SourceError, join_blocks, table_rows

_HEADING = re.compile(r"^Heading (\d+)$")
W_P, W_TBL, W_TR, W_T = qn("w:p"), qn("w:tbl"), qn("w:tr"), qn("w:t")
W_SDT, W_SDT_CONTENT = qn("w:sdt"), qn("w:sdtContent")
_SPACES = {qn("w:tab"), qn("w:br"), qn("w:cr")}
# Alternative or moved-away copies of text that is also present elsewhere in the paragraph.
_DUPLICATES = {"{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback", qn("w:moveFrom")}
# Table structure that holds no text of its own.
_TABLE_PARTS = {qn("w:tblPr"), qn("w:tblGrid"), qn("w:tblPrEx"), qn("w:trPr"), qn("w:tc")}


def _flat(text: str) -> str:
    return " ".join(text.split())


def _text(el) -> str:
    """Visible text of a paragraph, including runs inside inline content controls."""
    parts: list[str] = []

    def walk(node) -> None:
        for child in node:
            if not isinstance(child.tag, str) or child.tag in _DUPLICATES:
                continue
            if child.tag == W_T:
                parts.append(child.text or "")
            elif child.tag in _SPACES:
                parts.append(" ")
            else:
                walk(child)

    walk(el)
    return _flat("".join(parts))


def _unsupported(el) -> SourceError | None:
    """An error for an element we do not read, unless it holds no text."""
    if any((t.text or "").strip() for t in el.iter(W_T)):
        return SourceError(f"unsupported Word element {el.prefix}:{el.tag.rsplit('}', 1)[-1]}")
    return None


def _children(container):
    """Body-level children in document order, with content controls opened up."""
    for el in container.iterchildren():
        if not isinstance(el.tag, str):
            continue
        if el.tag == W_SDT:
            content = el.find(W_SDT_CONTENT)
            if content is not None:
                yield from _children(content)
        elif el.tag in (W_P, W_TBL):
            yield el
        elif err := _unsupported(el):
            raise err


def _paragraph(el, doc) -> str | None:
    text = _text(el)
    if not text:
        return None
    p = Paragraph(el, doc)
    style = p.style.name if p.style is not None else ""
    if style == "Title":
        return f"# {text}"
    if m := _HEADING.match(style):
        return "#" * (int(m.group(1)) + 1) + f" {text}"
    if style.startswith("List"):
        return f"- {text}"
    return text


def _check_table(el) -> None:
    for part in [*el.iterchildren(), *(c for tr in el.iterchildren(W_TR) for c in tr.iterchildren())]:
        if isinstance(part.tag, str) and part.tag not in _TABLE_PARTS | {W_TR} and (err := _unsupported(part)):
            raise err


def _cell(tc, doc) -> str:
    """A cell's text; a nested table adds its rows' `Header: value` pairs, joined by '; '."""
    pieces: list[str] = []
    words: list[str] = []
    for el in _children(tc):
        if el.tag == W_P:
            if text := _text(el):
                words.append(text)
            continue
        if words:
            pieces.append(" ".join(words))
            words = []
        pieces += _pairs(_rows(el, doc))
    if words:
        pieces.append(" ".join(words))
    return "; ".join(pieces)


def _rows(el, doc) -> list[list[str]]:
    _check_table(el)
    rows = [[_cell(c._tc, doc) for c in row.cells] for row in Table(el, doc).rows]
    return [r for r in rows if any(r)]


def _pairs(rows: list[list[str]]) -> list[str]:
    if len(rows) <= 1:
        return [v for row in rows for v in row if v]
    header, *body = rows
    return [f"{h}: {v}" if h else v for row in body for h, v in zip(header, row) if v]


def read(data: bytes) -> NormalisedText:
    try:
        doc = Document(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as e:
        raise SourceError(f"cannot read Word document: {e}") from e
    blocks: list[str] = []
    lines: list[str] = []  # consecutive list items stay in one block

    def flush() -> None:
        if lines:
            blocks.append("\n".join(lines))
            lines.clear()

    for el in _children(doc.element.body):
        if el.tag == W_TBL:
            rows = _rows(el, doc)
            out = "\n".join(table_rows(rows)) if rows else None
        else:
            out = _paragraph(el, doc)
        if out is None:
            continue
        if out.startswith("- "):
            lines.append(out)
            continue
        flush()
        blocks.append(out)
    flush()
    return NormalisedText(join_blocks(blocks), [(1, 0)])
