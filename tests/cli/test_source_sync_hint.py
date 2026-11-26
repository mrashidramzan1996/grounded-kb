from helpers import invoke, put


def add_reqs(root) -> None:
    quote = "A seat costs 5 EUR."
    body = f"statement: '{quote}'\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: '{quote}'\nmodules:\n- MOD-a\n"
    put(root, "kb/requirements/REQ-NF-001.yaml", f"id: REQ-NF-001\n{body}flags:\n- vague\n")
    put(root, "kb/requirements/REQ-F-002.yaml", f"id: REQ-F-002\n{body}")


def test_sync_prints_hint_naming_flagged_citers(md_repo_ingested):
    add_reqs(md_repo_ingested)
    put(md_repo_ingested, "docs/n.md", "# Seats\n\nA seat costs 7 EUR.\n")
    r = invoke(md_repo_ingested, "source", "sync")
    assert r.exit_code == 0
    assert "SRC-n: 1 segments" in r.stdout
    assert r.stderr.splitlines() == [
        "2 records cite changed text: REQ-F-002, REQ-NF-001 (vague).",
        "Before committing, run `kb redraft --changed` to redraft them.",
        "warning segment.untriaged_cited: SRC-n#seg-01 is cited by REQ-F-002, REQ-NF-001 but has no triage entry; "
        "add a triage entry for SRC-n#seg-01 in the same redraft",
    ]


def test_sync_without_changes_prints_no_hint(md_repo_ingested):
    add_reqs(md_repo_ingested)
    r = invoke(md_repo_ingested, "source", "sync")
    assert r.exit_code == 0 and "redraft" not in r.stderr


DOC = "# Seats\n\nA seat costs 5 EUR.\n\n# Speed\n\nPages load fast.\n"


def broken_before_sync(root) -> None:
    """REQ-F-001's quote is not in seg-01 before any sync; REQ-NF-001 quotes seg-02."""
    put(root, "docs/n.md", DOC)
    assert invoke(root, "source", "sync").exit_code == 0
    req = "id: {}\nstatement: '{}'\nevidence:\n  quotes:\n  - source: SRC-n#{}\n    text: '{}'\nmodules:\n- MOD-a\n"
    put(root, "kb/requirements/REQ-F-001.yaml", req.format("REQ-F-001", "A seat costs 9 EUR.", "seg-01", "A seat costs 9 EUR."))
    put(root, "kb/requirements/REQ-NF-001.yaml", req.format("REQ-NF-001", "Pages load fast.", "seg-02", "Pages load fast."))
    put(root, "kb/sources/SRC-n/triage.yaml", "segments:\n  seg-01: [REQ-F-001]\n  seg-02: [REQ-NF-001]\n")


def test_sync_hint_lists_quotes_already_not_found_separately(md_repo_ingested):
    broken_before_sync(md_repo_ingested)
    put(md_repo_ingested, "docs/n.md", DOC.replace("Pages load fast.", "Pages load in 2 seconds."))
    r = invoke(md_repo_ingested, "source", "sync")
    assert r.exit_code == 0
    assert r.stderr.splitlines() == [
        "1 record cites changed text: REQ-NF-001.",
        "1 record has quotes not found: REQ-F-001.",
        "Before committing, run `kb redraft --changed` to redraft them.",
    ]


def test_sync_of_unchanged_document_with_broken_quote_prints_no_hint(md_repo_ingested):
    broken_before_sync(md_repo_ingested)
    r = invoke(md_repo_ingested, "source", "sync")
    assert r.exit_code == 0 and r.stderr == ""
