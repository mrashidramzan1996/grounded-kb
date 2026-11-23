import pytest
from helpers import docx_bytes, pdf_bytes

from kb.sources.readers import NormalisedText, SourceError, read_document

DOCS = "repos/acme-billing/docs"


def pdf(fixtures) -> NormalisedText:
    return read_document("pricing-spec-v3.pdf", (fixtures / DOCS / "pricing-spec-v3.pdf").read_bytes())[0]


def test_pdf_strips_repeated_header_and_footer(fixtures):
    t = pdf(fixtures).text
    assert "Page 1 of 2" not in t and "Pricing Specification v3 · Internal" not in t


def test_pdf_joins_wrapped_lines_and_hyphenation(fixtures):
    t = pdf(fixtures).text
    assert "for the remaining days of the current billing cycle." in t
    assert "the next invoice of the subscription." in t


def test_pdf_headings_and_table_rows(fixtures):
    t = pdf(fixtures).text
    assert t.startswith("# Acme Billing - Pricing Specification v3\n")
    assert "\n## 1. Overview\n" in t
    assert "\n### 3.2 Removing seats\n" in t and "\n## 5. Non-functional requirements\n" in t
    assert "| Plan: Team | Price per seat: $12 per month | Billing period: monthly or annual | Minimum seats: 3 |" in t
    assert "Team $12 per month" not in t


def test_pdf_pages(fixtures):
    n = pdf(fixtures)

    def page_of(i: int) -> int:
        return max(p for p, s in n.pages if s <= i)

    assert page_of(n.text.index("No refund is given.")) == 1
    assert page_of(n.text.index("Invoices are sent")) == 2


def test_pdf_reader_id_and_layout(fixtures):
    n, norm = read_document("Spec.PDF", (fixtures / DOCS / "pricing-spec-v3.pdf").read_bytes())
    assert norm == "pdf@1"
    assert n.text.endswith("\n") and not n.text.endswith("\n\n") and "\n\n\n" not in n.text
    assert "\r" not in n.text


def test_docx_keeps_document_order_and_levels(fixtures):
    t, norm = read_document("architecture-notes.docx", (fixtures / DOCS / "architecture-notes.docx").read_bytes())
    assert norm == "docx@1" and t.pages == [(1, 0)]
    assert t.text.startswith("# Acme Billing - Architecture Notes\n")
    assert t.text.index("| Service: billing-service") < t.text.index("## Notes pasted from the review chat")
    assert "| Service: billing-service | Responsibility: plans, seats and invoices | Owner team: Billing team |" in t.text
    assert "\n- Redis.\n" in t.text
    brief = read_document("b.docx", (fixtures / DOCS / "product-brief.docx").read_bytes())[0].text
    assert "account’s" in brief and "\n## 2. Goals\n" in brief


def test_text_and_markdown_normalisation():
    n, norm = read_document("notes.MD", b"# Title  \r\n\r\nBody line.\t\r\n\n\n")
    assert norm == "text@1" and n == NormalisedText("# Title\n\nBody line.\n", [(1, 0)])
    assert read_document("a.txt", b"plain")[0].text == "plain\n"


def test_deterministic_and_unknown_format(fixtures):
    data = (fixtures / DOCS / "pricing-spec-v3.pdf").read_bytes()
    assert read_document("a.pdf", data) == read_document("a.pdf", data)
    with pytest.raises(SourceError, match=r"^no reader for \.pages \(available: md, txt, pdf, docx\)$"):
        read_document("x.pages", b"")


def test_pdf_keeps_paragraphs_apart(fixtures):
    t = pdf(fixtures).text
    assert "current billing cycle.\n\nThe new seat becomes active" in t
    assert "No refund is given.\n\nA subscription cannot" in t


@pytest.mark.parametrize("name", ["broken.pdf", "broken.docx", "broken.txt"])
def test_unreadable_input_is_a_source_error(name):
    with pytest.raises(SourceError):
        read_document(name, b"\xff\xfe not a document")


# --- review fixes: stricter PDF headings and headers, no silent Word drops -----------------------

LONG_BOLD = ("Every seat that is added during a billing cycle is charged for the remaining days, "
             "and the charge appears on the next invoice of the subscription")


def test_pdf_bold_sentences_stay_body_text():
    data = pdf_bytes([[
        ("B", 16, "Seat Report"),
        ("", 11, "Seats are counted at the start of each day by the billing service."),
        ("B", 11, "Every seat is billed monthly."),
        ("B", 11, "3 apples were sold."),
        ("B", 11, LONG_BOLD),
        ("B", 11, "Summary of seat counts"),
        ("", 11, "Counts are stored for audit purposes and never deleted."),
    ]])
    t = read_document("r.pdf", data)[0].text
    assert t.startswith("# Seat Report\n")
    assert "\n\nEvery seat is billed monthly.\n\n" in t and "\n\n3 apples were sold.\n\n" in t
    assert f"\n\n{LONG_BOLD}\n\n" in t
    assert "\n## Summary of seat counts\n" in t
    assert t.count("#") == 3


def test_pdf_repeated_edge_line_at_different_heights_is_kept():
    data = pdf_bytes(
        [
            [("", 11, "Seats are counted daily."), ("", 11, "Total: $5 per seat.")],
            [("", 11, "Total: $7 per seat."), ("", 11, "Invoices follow.")],
        ],
        header="Acme Billing Internal",
        footer="Acme draft {n}",
    )
    t = read_document("r.pdf", data)[0].text
    assert "Total: $5 per seat." in t and "Total: $7 per seat." in t
    assert "Acme Billing Internal" not in t and "Acme draft" not in t


def _doc():
    from docx import Document

    return Document()


def _wxml(tag: str, inner: str):
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    return parse_xml(f"<w:{tag} {nsdecls('w')}>{inner}</w:{tag}>")


RUN = "<w:r><w:t>{}</w:t></w:r>"


def test_docx_nested_table_text_is_kept():
    doc = _doc()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Service", "Details"
    t.cell(1, 0).text, t.cell(1, 1).text = "billing", "Owns invoices."
    inner = t.cell(1, 1).add_table(rows=2, cols=2)
    inner.cell(0, 0).text, inner.cell(0, 1).text = "Region", "Owner"
    inner.cell(1, 0).text, inner.cell(1, 1).text = "eu", "Billing team"
    text = read_document("n.docx", docx_bytes(doc))[0].text
    assert text == "| Service: billing | Details: Owns invoices.; Region: eu; Owner: Billing team |\n"


def test_docx_content_controls_are_read_in_order():
    doc = _doc()
    doc.add_paragraph("Before.")
    after = doc.add_paragraph("After.")
    after._p.addprevious(_wxml("sdt", "<w:sdtPr/><w:sdtContent><w:p>" + RUN.format("Inside a content control.") + "</w:p></w:sdtContent>"))
    inline = doc.add_paragraph("Inline ")
    inline._p.append(_wxml("sdt", "<w:sdtContent>" + RUN.format("control text.") + "</w:sdtContent>"))
    text = read_document("c.docx", docx_bytes(doc))[0].text
    assert text == "Before.\n\nInside a content control.\n\nAfter.\n\nInline control text.\n"


def test_docx_empty_paragraphs_make_no_blocks():
    doc = _doc()
    for line in ["A.", "", "  ", "B."]:
        doc.add_paragraph(line)
    assert read_document("e.docx", docx_bytes(doc))[0].text == "A.\n\nB.\n"


def test_docx_unknown_element_with_text_is_an_error():
    doc = _doc()
    doc.add_paragraph("Kept.")._p.addnext(_wxml("customXml", "<w:p>" + RUN.format("Hidden.") + "</w:p>"))
    with pytest.raises(SourceError, match=r"^unsupported Word element w:customXml$"):
        read_document("u.docx", docx_bytes(doc))


def test_docx_unknown_element_without_text_is_ignored():
    doc = _doc()
    doc.add_paragraph("Kept.")._p.addnext(_wxml("bookmarkStart", ""))
    assert read_document("u.docx", docx_bytes(doc))[0].text == "Kept.\n"


# --- review fix: bounded Word table extraction ---------------------------------------------------

def _nested_tables(depth: int, span: int = 1):
    """A document with one table nested `depth` levels deep; each table is one cell spanning `span` columns."""
    doc = _doc()
    container = doc
    for level in range(depth):
        t = container.add_table(rows=1, cols=span)
        cell = t.cell(0, 0).merge(t.cell(0, span - 1)) if span > 1 else t.cell(0, 0)
        cell.text = f"level {level + 1}"
        container = cell
    return doc


def test_docx_tables_nested_too_deep_are_an_error():
    read_document("ok.docx", docx_bytes(_nested_tables(5)))
    with pytest.raises(SourceError, match=r"nested deeper than 5"):
        read_document("deep.docx", docx_bytes(_nested_tables(6)))


def test_docx_merged_cell_value_appears_once():
    doc = _doc()
    t = doc.add_table(rows=3, cols=4)
    for i, h in enumerate(["Plan", "Jan", "Feb", "Mar"]):
        t.cell(0, i).text = h
    t.cell(1, 0).text = "Team"
    t.cell(1, 1).merge(t.cell(1, 3)).text = "closed"
    t.cell(2, 0).text = "Starter"
    t.cell(2, 1).merge(t.cell(2, 2)).text = "open"
    t.cell(2, 3).text = "late"
    text = read_document("m.docx", docx_bytes(doc))[0].text
    assert text == "| Plan: Team | Jan: closed |\n| Plan: Starter | Jan: open | Mar: late |\n"


def test_docx_vertically_merged_value_appears_once():
    doc = _doc()
    t = doc.add_table(rows=3, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Team", "Service"
    t.cell(1, 0).merge(t.cell(2, 0)).text = "Billing"
    t.cell(1, 1).text, t.cell(2, 1).text = "invoices", "seats"
    text = read_document("v.docx", docx_bytes(doc))[0].text
    assert text.count("Billing") == 1 and "Service: seats" in text


def test_docx_merged_nested_tables_do_not_explode():
    import time

    data = docx_bytes(_nested_tables(5, span=12))
    start = time.monotonic()
    text = read_document("x.docx", data)[0].text
    assert time.monotonic() - start < 2
    assert text.count("level 5") == 1


def test_docx_text_limit(monkeypatch):
    import kb.sources.readers.docx as reader

    monkeypatch.setattr(reader, "MAX_TEXT_CHARS", 20)
    doc = _doc()
    doc.add_paragraph("This paragraph is longer than twenty characters.")
    with pytest.raises(SourceError, match=r"more than 20 characters"):
        read_document("big.docx", docx_bytes(doc))


def _para_xml(doc, inner: str):
    p = doc.add_paragraph()
    for el in _wxml("p", inner):
        p._p.append(el)
    return p


MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"


def test_docx_special_characters_in_runs():
    doc = _doc()
    _para_xml(doc, "<w:pPr><w:tabs><w:tab w:val='left' w:pos='720'/></w:tabs></w:pPr>"
                   "<w:r><w:t>well</w:t><w:noBreakHyphen/><w:t>known co</w:t><w:softHyphen/><w:t>operation</w:t>"
                   "<w:ptab w:relativeTo='margin' w:alignment='right' w:leader='none'/><w:t>end</w:t></w:r>")
    assert read_document("h.docx", docx_bytes(doc))[0].text == "well-known cooperation end\n"


def test_docx_text_box_text_is_read_once():
    doc = _doc()
    box = "<w:txbxContent><w:p><w:r><w:t>Box text</w:t></w:r></w:p></w:txbxContent>"
    _para_xml(doc, f"<w:r><w:t xml:space='preserve'>Before </w:t></w:r><w:r>"
                   f"<mc:AlternateContent xmlns:mc='{MC}'>"
                   f"<mc:Choice Requires='wps'><w:drawing><wps:txbx xmlns:wps='{WPS}'>{box}</wps:txbx></w:drawing></mc:Choice>"
                   f"<mc:Fallback><w:pict>{box}</w:pict></mc:Fallback>"
                   f"</mc:AlternateContent></w:r>")
    assert read_document("b.docx", docx_bytes(doc))[0].text == "Before Box text\n"
