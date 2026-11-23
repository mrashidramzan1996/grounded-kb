"""Word documents (python-docx), walked in document order. No text is dropped silently:
content controls and nested tables are read, and any other element holding text is an error.

Source documents are untrusted, so the work is bounded: every table cell is read once (merged
cells are not repeated per grid column), the archive is checked for zip bombs before it is
parsed, tables nest at most MAX_TABLE_DEPTH deep, a document has at most MAX_TABLE_CELLS table
cells and at most MAX_TEXT_CHARS characters are extracted."""

import io
import re
import zipfile
from bisect import bisect_right

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from kb.sources.readers import NormalisedText, SourceError, join_blocks, row_line

MAX_TABLE_DEPTH = 5
MAX_TEXT_CHARS = 5_000_000
MAX_TABLE_CELLS = 100_000
MAX_DOCX_UNCOMPRESSED = 100_000_000  # bytes, all archive members together
MAX_DOCX_COMPRESSION_RATIO = 200

_HEADING = re.compile(r"^Heading (\d+)$")
W_P, W_TBL, W_TR, W_TC, W_T = qn("w:p"), qn("w:tbl"), qn("w:tr"), qn("w:tc"), qn("w:t")
W_SDT, W_SDT_CONTENT = qn("w:sdt"), qn("w:sdtContent")
W_VAL = qn("w:val")
# Run content that stands for a character.
_CHARS = {
    qn("w:tab"): " ", qn("w:ptab"): " ", qn("w:br"): " ", qn("w:cr"): " ",
    qn("w:noBreakHyphen"): "-", qn("w:softHyphen"): "",
}
# Not text: properties (tab stop definitions live in w:pPr), and alternative or moved-away
# copies of text that is also present elsewhere in the paragraph.
_SKIP = {
    qn("w:pPr"), qn("w:rPr"), qn("w:moveFrom"),
    "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback",
}
# Table structure that holds no text of its own.
_TABLE_PARTS = {qn("w:tblPr"), qn("w:tblGrid"), qn("w:tblPrEx"), qn("w:trPr"), W_TC, W_TR}

_Cell = tuple[int, int, str]  # (first grid column, columns spanned, value)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _unsupported(el) -> SourceError | None:
    """An error for an element we do not read, unless it holds no text."""
    if any((t.text or "").strip() for t in el.iter(W_T)):
        return SourceError(f"unsupported Word element {el.prefix}:{el.tag.rsplit('}', 1)[-1]}")
    return None


def _children(container):
    """Children in document order, with content controls opened up."""
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


def _prop(el, path: str, default: int, minimum: int) -> int:
    found = el.find(path)
    if found is None:
        return default
    try:
        value = int(found.get(W_VAL, default))
    except ValueError:
        value = minimum - 1
    if value < minimum:
        raise SourceError(f"invalid Word table property {path}: {found.get(W_VAL)!r}")
    return value


def _headers(header: list[_Cell], rows: list[list[_Cell]]) -> dict[int, str]:
    """Grid column → header value, for the columns where `rows` have cells. Built once per table;
    header cells' start columns increase strictly, so each lookup is a binary search."""
    starts = [start for start, _, _ in header]
    out: dict[int, str] = {}
    for col in {col for row in rows for col, _, _ in row}:
        i = bisect_right(starts, col) - 1
        out[col] = header[i][2] if i >= 0 and col < header[i][0] + header[i][1] else ""
    return out


def _check_archive(data: bytes) -> None:
    """Refuse zip bombs before python-docx decompresses anything. Python's zipfile never
    yields more than a member's declared size, so the declared sizes bound the work."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = z.infolist()
    except (zipfile.BadZipFile, ValueError, EOFError) as e:
        raise SourceError(f"not a valid .docx archive: {e}") from e
    total = 0
    for m in members:
        total += m.file_size
        if total > MAX_DOCX_UNCOMPRESSED:
            raise SourceError(
                f"Word document has more than {MAX_DOCX_UNCOMPRESSED} bytes uncompressed (MAX_DOCX_UNCOMPRESSED)"
            )
        if m.file_size > MAX_DOCX_COMPRESSION_RATIO * max(m.compress_size, 1):
            raise SourceError(
                f"Word archive member {m.filename} has compression ratio above "
                f"{MAX_DOCX_COMPRESSION_RATIO} (MAX_DOCX_COMPRESSION_RATIO)"
            )


class _Reader:
    def __init__(self, doc):
        self.doc = doc
        self.chars = 0
        self.cells = 0

    def count(self, text: str) -> str:
        self.chars += len(text)
        if self.chars > MAX_TEXT_CHARS:
            raise SourceError(f"Word document text has more than {MAX_TEXT_CHARS} characters (MAX_TEXT_CHARS)")
        return text

    def text(self, el) -> str:
        """Visible text of a paragraph, including inline content controls and text boxes."""
        parts: list[str] = []

        def walk(node) -> None:
            for child in node:
                if not isinstance(child.tag, str) or child.tag in _SKIP:
                    continue
                if child.tag == W_T:
                    parts.append(child.text or "")
                elif child.tag in _CHARS:
                    parts.append(_CHARS[child.tag])
                else:
                    walk(child)

        walk(el)
        return self.count(_flat("".join(parts)))

    def paragraph(self, el) -> str | None:
        text = self.text(el)
        if not text:
            return None
        p = Paragraph(el, self.doc)
        style = p.style.name if p.style is not None else ""
        if style == "Title":
            return f"# {text}"
        if m := _HEADING.match(style):
            return "#" * (int(m.group(1)) + 1) + f" {text}"
        if style.startswith("List"):
            return f"- {text}"
        return text

    def rows(self, tbl, depth: int) -> list[list[_Cell]]:
        """Non-empty rows of a table; each `w:tc` is read once, wherever it spans."""
        if depth > MAX_TABLE_DEPTH:
            raise SourceError(f"Word tables nested deeper than {MAX_TABLE_DEPTH} levels (MAX_TABLE_DEPTH)")
        for part in [*tbl.iterchildren(), *(c for tr in tbl.iterchildren(W_TR) for c in tr.iterchildren())]:
            if isinstance(part.tag, str) and part.tag not in _TABLE_PARTS and (err := _unsupported(part)):
                raise err
        rows = []
        for tr in tbl.iterchildren(W_TR):
            col = _prop(tr, f"{qn('w:trPr')}/{qn('w:gridBefore')}", 0, minimum=0)
            row: list[_Cell] = []
            for tc in tr.iterchildren(W_TC):
                self.cells += 1
                if self.cells > MAX_TABLE_CELLS:
                    raise SourceError(f"Word document has more than {MAX_TABLE_CELLS} table cells (MAX_TABLE_CELLS)")
                span = _prop(tc, f"{qn('w:tcPr')}/{qn('w:gridSpan')}", 1, minimum=1)
                vmerge = tc.find(f"{qn('w:tcPr')}/{qn('w:vMerge')}")
                continued = vmerge is not None and vmerge.get(W_VAL) != "restart"
                row.append((col, span, "" if continued else self.cell(tc, depth)))
                col += span
            if any(v for _, _, v in row):
                rows.append(row)
        return rows

    def cell(self, tc, depth: int) -> str:
        """A cell's text; a nested table adds its rows' `Header: value` pairs, joined by '; '."""
        pieces: list[str] = []
        words: list[str] = []
        for el in _children(tc):
            if el.tag == W_P:
                if text := self.text(el):
                    words.append(text)
                continue
            if words:
                pieces.append(" ".join(words))
                words = []
            pieces += self.pairs(self.rows(el, depth + 1))
        if words:
            pieces.append(" ".join(words))
        return "; ".join(pieces)

    def pairs(self, rows: list[list[_Cell]]) -> list[str]:
        if len(rows) <= 1:
            return [v for row in rows for _, _, v in row if v]
        header, *body = rows
        heads = _headers(header, body)
        return [self.count(f"{heads[col]}: {v}" if heads[col] else v) for row in body for col, _, v in row if v]

    def table(self, tbl) -> str | None:
        rows = self.rows(tbl, 1)
        if not rows:
            return None
        if len(rows) == 1:
            return self.count(row_line([v for _, _, v in rows[0]]))
        header, *body = rows
        heads = _headers(header, body)
        lines = [
            row_line([self.count(f"{heads[col]}: {v}" if heads[col] else v) for col, _, v in row])
            for row in body
        ]
        return "\n".join(lines)


def read(data: bytes) -> NormalisedText:
    _check_archive(data)
    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:  # python-docx/lxml raise many error types on malformed packages
        raise SourceError(f"cannot read Word document: {e}") from e
    reader = _Reader(doc)
    blocks: list[str] = []
    lines: list[str] = []  # consecutive list items stay in one block

    def flush() -> None:
        if lines:
            blocks.append("\n".join(lines))
            lines.clear()

    for el in _children(doc.element.body):
        out = reader.table(el) if el.tag == W_TBL else reader.paragraph(el)
        if out is None:
            continue
        if out.startswith("- "):
            lines.append(out)
            continue
        flush()
        blocks.append(out)
    flush()
    return NormalisedText(join_blocks(blocks), [(1, 0)])
