import pytest

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
