"""Word documents (python-docx), walked in document order."""

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


def _flat(text: str) -> str:
    return " ".join(text.split())


def _paragraph(p: Paragraph) -> str | None:
    text = _flat(p.text)
    if not text:
        return None
    style = p.style.name if p.style is not None else ""
    if style == "Title":
        return f"# {text}"
    if m := _HEADING.match(style):
        return "#" * (int(m.group(1)) + 1) + f" {text}"
    if style.startswith("List"):
        return f"- {text}"
    return text


def _table(t: Table) -> str | None:
    rows = [[_flat(c.text) for c in row.cells] for row in t.rows]
    rows = [r for r in rows if any(r)]
    return "\n".join(table_rows(rows)) if rows else None


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

    for el in doc.element.body.iterchildren():
        if el.tag == qn("w:p"):
            out = _paragraph(Paragraph(el, doc))
        elif el.tag == qn("w:tbl"):
            out = _table(Table(el, doc))
        else:
            continue
        if out is None:
            continue
        if out.startswith("- "):
            lines.append(out)
            continue
        flush()
        blocks.append(out)
    flush()
    return NormalisedText(join_blocks(blocks), [(1, 0)])
