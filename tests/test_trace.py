from helpers import invoke, replace_doc_text, write
from kb.trace import trace, trace_chain

NOTES_REQ = """\
id: REQ-F-020
statement: "No refund is given when a seat is removed."
evidence:
  quotes:
  - source: SRC-notes#seg-01
    text: "No refund is given."
modules:
- MOD-billing
"""


def test_trace_shows_page_and_fails_on_reworded_source(acme_repo_drafted):
    assert any("page 1" in l and "3.2 Removing seats" in l for l in trace(acme_repo_drafted, "REQ-F-002"))
    # R10: reword a Markdown source rather than regenerating the PDF
    write(acme_repo_drafted, "docs/notes.md", "# Refunds\n\nNo refund is given.\n")
    assert invoke(acme_repo_drafted, "source", "add", "docs/notes.md").exit_code == 0
    write(acme_repo_drafted, "kb/requirements/REQ-F-020.yaml", NOTES_REQ)
    assert invoke(acme_repo_drafted, "trace", "REQ-F-020").exit_code == 0
    replace_doc_text(acme_repo_drafted, "docs/notes.md", "No refund is given.", "Refunds are pro-rata.")
    assert invoke(acme_repo_drafted, "source", "sync").exit_code == 0
    r = invoke(acme_repo_drafted, "trace", "REQ-F-020")
    assert r.exit_code == 1 and "QUOTE NOT FOUND" in r.stdout


def test_trace_chain_runs_record_quote_segment_file(acme_repo_drafted):
    lines, ok = trace_chain(acme_repo_drafted, "REQ-F-002")
    assert ok
    assert lines[0].startswith("[REQ-F-002]") and "`No refund is given when a seat is removed.`" in lines[0]
    order = ["`No refund is given.`", "SRC-pricing-spec-v3#seg-10", "page 1", "docs/pricing-spec-v3.pdf"]
    at = [next(i for i, l in enumerate(lines) if s in l) for s in order]
    assert at == sorted(at)
    assert "QUOTE NOT FOUND" not in "\n".join(lines)


def test_trace_page_two(acme_repo_drafted):
    assert any("page 2" in l and "5. Non-functional requirements" in l for l in trace(acme_repo_drafted, "REQ-NF-002"))


def test_trace_segment_lists_every_record_resting_on_it(acme_repo_drafted):
    lines = trace(acme_repo_drafted, "SRC-pricing-spec-v3#seg-10")
    assert lines[0].startswith("[SRC-pricing-spec-v3#seg-10]") and "page 1" in lines[0]
    cited = [l.split("]")[0].lstrip("[") for l in lines[1:] if not l.startswith("[SRC-")]
    assert cited == ["REQ-F-001", "REQ-F-002"]
    assert any(l.startswith("[ADR-0002]") for l in trace(acme_repo_drafted, "SRC-architecture-notes#seg-03"))
    assert any(l.startswith("[INV-001]") for l in trace(acme_repo_drafted, "SRC-architecture-notes#seg-04"))


def test_trace_marks_superseded_records(valid_git_repo):
    lines = trace(valid_git_repo, "ADR-0001")
    assert "superseded by ADR-0003" in lines[0]


def test_trace_missing_segment_is_a_failed_quote(acme_repo_drafted):
    path = acme_repo_drafted / "kb/requirements/REQ-F-002.yaml"
    path.write_text(path.read_text().replace("seg-10", "seg-99"))
    lines, ok = trace_chain(acme_repo_drafted, "REQ-F-002")
    assert not ok and any("QUOTE NOT FOUND" in l for l in lines)
    assert invoke(acme_repo_drafted, "trace", "REQ-F-002").exit_code == 1


def test_trace_unknown_ids_exit_2(acme_repo_drafted):
    for bad in ["REQ-F-999", "SRC-pricing-spec-v3#seg-99", "SRC-nope#seg-01", "not-an-id"]:
        r = invoke(acme_repo_drafted, "trace", bad)
        assert r.exit_code == 2 and r.stdout == "", bad
