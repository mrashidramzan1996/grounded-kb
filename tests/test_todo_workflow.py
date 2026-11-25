"""`kb todo` workflow state: a brief whose result is not committed yet, uncommitted knowledge-base changes, documents
`kb ingest` cannot read, and items one fix already covers."""

import json

from helpers import TODO_CONFIG, TODO_DOC, TODO_TRIAGE, commit, git, ok, put, req_yaml
from helpers import todo_drafted as drafted
from helpers import todo_ingested as ingested
from helpers import todo_json as todo
from helpers import todo_repo as repo

from kb.todo import AskItem, Todo, build_todo, summary_line

COMMIT = {
    "id": "commit", "code": "kb.commit",
    "message": "commit the knowledge-base changes: kb/, kb.config.yaml, docs/clarifications.md — before the baseline "
               "on the current branch, after it on a pull-request branch",
    "command": None, "path": None, "record": None,
}


def items(root) -> dict[str, dict]:
    return {d["id"]: d for d in todo(root)["do"]}


# --- report and commit items (Critical 1) -----------------------------------------------------------------------

def test_ingest_brief_not_committed_asks_for_report(tmp_path):
    root = repo(tmp_path)
    ok(root, "ingest")
    found = items(root)
    assert found["report:ingest"] == {
        "id": "report:ingest", "code": "kb.ingest",
        "message": "a `kb ingest` brief was written at this commit; check the drafts with `kb ingest --report`, "
                   "fix what it rejects, then commit",
        "command": "kb ingest --report", "path": None, "record": None,
    }
    assert "commit" not in found  # another do item is listed
    t = todo(root)
    assert t["state"]["clean"] is False and t["ask"] == []
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001]\n")
    ok(root, "ingest", "--report", "--strict")
    assert "report:ingest" in items(root)  # until it is committed
    commit(root, "drafts")
    t = todo(root)
    assert t["do"] == [] and t["state"]["clean"] is True and [a["id"] for a in t["ask"]] == ["freeze:v1"]


def test_redraft_brief_not_committed_asks_for_report(tmp_path):
    root = drafted(tmp_path)
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001", flags=("vague",)))
    commit(root, "flag")
    ok(root, "redraft", "REQ-F-001")
    found = items(root)
    assert found["report:redraft"] == {
        "id": "report:redraft", "code": "kb.redraft",
        "message": "a `kb redraft` brief was written at this commit; check the redraft with `kb redraft --report`, "
                   "fix what it rejects, then commit",
        "command": "kb redraft --report", "path": None, "record": None,
    }
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    commit(root, "redraft")
    assert "report:redraft" not in items(root)


def test_stale_or_unsafe_base_file_gives_no_report_item(tmp_path):
    root = repo(tmp_path)
    ok(root, "ingest")
    commit(root, "ingest")
    assert "report:ingest" not in items(root)  # the base names the commit before
    base = root / ".kb/cache/ingest/base"
    base.unlink()
    (tmp_path / "elsewhere").write_text(git(root, "rev-parse", "HEAD"))
    base.symlink_to(tmp_path / "elsewhere")  # read the way the reports read it: a symlink is ignored
    assert "report:ingest" not in items(root)


def test_uncommitted_kb_changes_ask_for_commit(tmp_path):
    root = drafted(tmp_path)
    for rel, text in [
        ("kb.config.yaml", TODO_CONFIG + "# a note\n"),
        ("kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001") + "# a note\n"),
    ]:
        before = (root / rel).read_text()
        put(root, rel, text)
        t = todo(root)
        assert t["do"] == [COMMIT] and t["ask"] == [] and t["state"]["clean"] is False  # no freeze before it
        put(root, rel, before)
        assert todo(root)["do"] == []
    put(root, "docs/clarifications.md", TODO_DOC)  # not ingested: the ingest item comes first
    assert list(items(root)) == ["ingest:docs/clarifications.md"]
    ok(root, "ingest", "docs/clarifications.md")
    put(root, "kb/sources/SRC-clarifications/triage.yaml", 'segments:\n  seg-01: "none: a copy"\n')
    assert list(items(root)) == ["report:ingest"]
    commit(root, "clarifications")
    put(root, "docs/clarifications.md", TODO_DOC + "\nMore.\n")
    assert "commit" not in items(root)  # the sync comes first
    git(root, "checkout", "docs/clarifications.md")
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001") + "# a note\n")
    put(root, "docs/b.md", "# B\n\nText.\n")
    assert list(items(root)) == ["ingest:docs/b.md"]


# --- unreadable documents (Important 3) -------------------------------------------------------------------------

UNREADABLE = "unreadable:docs/bad.pdf"


def test_unreadable_document_is_a_blocking_ask(tmp_path):
    root = repo(tmp_path, **{"docs/bad.pdf": "not a pdf"})
    t = todo(root)
    assert [d["id"] for d in t["do"]] == ["ingest:docs/n.md"]  # not ingest:all: `kb ingest` would refuse the set
    assert t["do"][0]["command"] == "kb ingest docs/n.md"
    (ask,) = t["ask"]
    assert ask["id"] == UNREADABLE and ask["kind"] == "unreadable" and ask["evidence"] == {}
    assert ask["question"].startswith("docs/bad.pdf could not be read (cannot read PDF:")
    assert ask["question"].endswith("). What should I do?")
    assert ask["options"] == [
        {"key": "fix", "label": "I will replace it with a readable file",
         "then": "wait; run kb todo again after the file is replaced"},
        {"key": "remove", "label": "Remove it from docs/", "then": "git rm the file"},
        {"key": "exclude", "label": "Keep it but leave it out of the knowledge base",
         "then": "add a `!docs/bad.pdf` line to that config.sources entry's paths and commit kb.config.yaml on its own"},
    ]
    assert t["state"]["blocking_asks"] == 1 and t["state"]["clean"] is False
    text = ok(root, "todo").stdout
    assert "1. `docs/bad.pdf` could not be read (`cannot read PDF:" in text

    ok(root, "ingest", "docs/n.md")
    put(root, TODO_TRIAGE, 'segments:\n  seg-01: "none: test"\n')
    commit(root, "n")
    t = todo(root)
    assert t["do"] == [] and [a["id"] for a in t["ask"]] == [UNREADABLE]  # no freeze while it blocks
    put(root, "kb.config.yaml", TODO_CONFIG.replace('["docs/**"]', '["docs/**", "!docs/bad.pdf"]'))
    commit(root, "exclude")
    assert UNREADABLE not in [a["id"] for a in todo(root)["ask"]]


def test_unreadable_reason_is_inline_code(tmp_path):
    root = repo(tmp_path, **{"docs/`x` *y*.pdf": "not a pdf"})
    (ask,) = todo(root)["ask"]
    assert ask["id"] == "unreadable:docs/`x` *y*.pdf"
    assert "1. ``docs/`x` *y*.pdf`` could not be read (`" in ok(root, "todo").stdout
    assert ask["options"][2]["then"].startswith("add a ``!docs/`x` *y*.pdf`` line")


def test_readable_documents_keep_ingest_all(tmp_path):
    root = repo(tmp_path, **{"docs/b.md": "# B\n\nText.\n"})
    assert list(items(root)) == ["ingest:all"]


# --- duplicates and stale paths (Minors 7-9) --------------------------------------------------------------------

def test_quote_not_found_covered_by_redraft_changed(tmp_path):
    root = drafted(tmp_path)
    put(root, "docs/n.md", TODO_DOC.replace("5 EUR", "7 EUR"))
    ok(root, "source", "sync")
    found = items(root)
    assert "redraft:changed" in found and "REQ-F-001" in found["redraft:changed"]["message"]
    assert not any(k.startswith("fix:quote.not_found") for k in found)


def test_not_ingested_message_quotes_the_path(tmp_path):
    root = ingested(tmp_path)
    put(root, "docs/b `x`.md", "# B\n\nText.\n")
    r = ok(root, "check", "--format", "json")
    (issue,) = [i for i in json.loads(r.stdout)["issues"] if i["code"] == "source.not_ingested"]
    assert issue["message"] == ("``docs/b `x`.md`` is a document under config.sources that was never ingested; "
                                "run `kb ingest 'docs/b `x`.md'`")


def test_deleted_but_indexed_document_is_not_ingested_work(tmp_path):
    root = ingested(tmp_path, **{"docs/b.md": "# B\n\nText.\n"})
    put(root, "docs/c.md", "# C\n\nText.\n")
    git(root, "add", "docs/c.md")
    (root / "docs/c.md").unlink()  # still in the index
    assert not any(k.startswith("ingest:") for k in items(root))
    r = ok(root, "check", "--format", "json")
    assert not [i for i in json.loads(r.stdout)["issues"] if i["code"] == "source.not_ingested"]


# --- summary (Minor 5) ------------------------------------------------------------------------------------------

def test_summary_line_pluralises(tmp_path):
    root = repo(tmp_path)
    t = build_todo(root)
    assert summary_line(t) == "1 thing to do, 0 questions for the user"
    (item,) = t.do
    ask = AskItem("freeze:v1", "freeze", "Freeze?", {}, (), "Freeze?")
    assert summary_line(Todo((item, item), (ask,), {})) == "2 things to do, 1 question for the user"
    assert summary_line(Todo((), (ask, ask), {})) == "0 things to do, 2 questions for the user"
    assert ok(root, "todo").stderr.strip() == "1 thing to do, 0 questions for the user"
