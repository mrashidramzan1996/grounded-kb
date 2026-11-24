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
    assert (
        "2 records cite changed text: REQ-F-002, REQ-NF-001 (vague). Run `kb redraft --changed` to redraft them."
        in r.stderr
    )


def test_sync_without_changes_prints_no_hint(md_repo_ingested):
    add_reqs(md_repo_ingested)
    r = invoke(md_repo_ingested, "source", "sync")
    assert r.exit_code == 0 and "redraft" not in r.stderr
