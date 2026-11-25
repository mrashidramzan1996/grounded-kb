"""source.not_ingested: a document under config.sources that `kb ingest` can read but that was never stored."""

import json

from helpers import git, invoke, make_git_repo

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\n'
RESTRICTED = CONFIG + 'visibility:\n  restricted:\n    - paths: ["docs/private/**"]\n      readers: ["@acme/hr"]\n'
DOC = "# Seats\n\nA seat costs 5 EUR.\n"


def ingested(tmp_path, config: str = CONFIG, **docs: str):
    """A committed repo with docs/n.md stored as a source and the extra files `docs` (path -> text) not ingested."""
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": config, "docs/n.md": DOC, **docs})
    r = invoke(root, "ingest", "docs/n.md")
    assert r.exit_code == 0, r.output
    git(root, "add", "-A")
    git(root, "commit", "-qm", "ingest")
    return root


def not_ingested(root) -> list[dict]:
    r = invoke(root, "check", "--format", "json")
    assert r.exit_code in (0, 1), r.output
    return [i for i in json.loads(r.stdout)["issues"] if i["code"] == "source.not_ingested"]


def todo_ids(root) -> list[str]:
    r = invoke(root, "todo", "--format", "json")
    assert r.exit_code == 0, r.output
    return [d["id"] for d in json.loads(r.stdout)["do"]]


def test_not_ingested_document_listed_and_warned(tmp_path):
    root = ingested(tmp_path, **{"docs/refund-policy.md": "# Refunds\n\nA refund takes 14 days.\n"})
    assert not_ingested(root) == [{
        "code": "source.not_ingested",
        "severity": "warning",
        "path": "docs/refund-policy.md",
        "record": None,
        "message": "docs/refund-policy.md is a document under config.sources that was never ingested; "
        "run `kb ingest docs/refund-policy.md`",
    }]
    r = invoke(root, "todo", "--format", "json")
    (item,) = [d for d in json.loads(r.stdout)["do"] if d["id"] == "ingest:docs/refund-policy.md"]
    assert item["code"] == "kb.ingest"
    assert item["command"] == "kb ingest docs/refund-policy.md"
    assert item["path"] == "docs/refund-policy.md"
    assert "ingest:all" not in todo_ids(root)  # a source is stored: only the missing document is named
    text = invoke(root, "todo")
    assert "  run: kb ingest docs/refund-policy.md\n" in text.stdout


def test_ingested_document_not_reported(tmp_path):
    root = ingested(tmp_path)
    assert not_ingested(root) == []
    assert not any(i.startswith("ingest:") for i in todo_ids(root))


def test_unreadable_file_not_reported(tmp_path):
    root = ingested(tmp_path, **{"docs/diagram.png": "not a document kb can read", "docs/notes.rtf": "{\\rtf1}"})
    assert not_ingested(root) == []
    assert not any(i.startswith("ingest:") for i in todo_ids(root))


def test_restricted_path_not_reported(tmp_path):
    root = ingested(tmp_path, RESTRICTED, **{"docs/private/salaries.md": "# Salaries\n\nSecret.\n"})
    assert not_ingested(root) == []
    assert not any(i.startswith("ingest:") for i in todo_ids(root))


def test_files_outside_config_sources_not_reported(tmp_path):
    root = ingested(tmp_path, **{"README.md": "# Readme\n"})
    assert not_ingested(root) == []
