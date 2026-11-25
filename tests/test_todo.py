"""`kb todo`: what the agent does next (`do`) and what it asks the person (`ask`, filled in later)."""

import json

from helpers import git, invoke, make_git_repo, put, req_yaml

from kb.todo import build_todo

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-a\n    owner: "@alice"\n'
DOC = "# Seats\n\nA seat costs 5 EUR.\n"
TRIAGE = "kb/sources/SRC-n/triage.yaml"


def commit(root, message: str = "change") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def ok(root, *args: str):
    r = invoke(root, *args)
    assert r.exit_code == 0, r.output
    return r


def todo(root) -> dict:
    return json.loads(ok(root, "todo", "--format", "json").stdout)


def items(root) -> dict[str, dict]:
    return {d["id"]: d for d in todo(root)["do"]}


def repo(tmp_path, config: str = CONFIG, **docs: str):
    """A committed repo with kb.config.yaml and docs/n.md (plus `docs`), nothing ingested."""
    return make_git_repo(None, tmp_path, extra={"kb.config.yaml": config, "docs/n.md": DOC, **docs})


def ingested(tmp_path, **docs: str):
    """repo() with every document ingested and committed."""
    root = repo(tmp_path, **docs)
    ok(root, "ingest")
    commit(root, "ingest")
    return root


def drafted(tmp_path):
    """ingested() with REQ-F-001 quoting seg-01 and seg-01 triaged, committed: a clean KB."""
    root = ingested(tmp_path)
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(root, TRIAGE, "segments:\n  seg-01: [REQ-F-001]\n")
    commit(root, "drafts")
    return root


def test_todo_without_config_suggests_init(tmp_path):
    root = make_git_repo(None, tmp_path, extra={"docs/n.md": DOC})
    t = todo(root)
    assert t["do"] == [{
        "id": "init", "code": "kb.init", "message": "kb.config.yaml not found; set up the knowledge base",
        "command": "kb init", "path": None, "record": None,
    }]
    assert t["ask"] == []
    assert t["state"]["initialized"] is False and t["state"]["clean"] is False
    r = ok(root, "todo")
    assert "- kb.config.yaml not found; set up the knowledge base\n  run: kb init\n" in r.stdout
    assert r.stderr.strip() == "1 to do, 0 questions for the user"


def test_todo_without_commits(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-qb", "main")
    put(root, "kb.config.yaml", CONFIG)
    put(root, "docs/n.md", DOC)
    assert list(items(root)) == ["ingest:all"]


def test_todo_new_repo_suggests_ingest(tmp_path):
    root = repo(tmp_path, **{"docs/b.md": "# B\n\nText.\n"})
    t = todo(root)
    assert t["do"] == [{
        "id": "ingest:all", "code": "kb.ingest", "message": "documents under config.sources were never ingested",
        "command": "kb ingest", "path": None, "record": None,
    }]
    assert t["state"]["initialized"] is True and t["state"]["sources"] == 0


def test_changed_document_suggests_sync(tmp_path):
    root = ingested(tmp_path, **{"docs/b.md": "# B\n\nText.\n"})
    assert "sync" not in items(root)
    put(root, "docs/n.md", DOC.replace("5 EUR", "7 EUR"))
    put(root, "docs/b.md", "# B\n\nOther text.\n")
    sync = items(root)["sync"]
    assert sync == {
        "id": "sync", "code": "kb.sync", "message": "documents changed since they were stored",
        "command": "kb source sync", "path": None, "record": None,
    }
    assert not any(i.startswith(("fix:source.outdated", "fix:source.tampered")) for i in items(root))  # the sync covers them


def test_changed_cited_text_suggests_redraft_changed(tmp_path):
    root = drafted(tmp_path)
    assert "redraft:changed" not in items(root)
    put(root, "docs/n.md", DOC.replace("5 EUR", "7 EUR"))
    ok(root, "source", "sync")
    item = items(root)["redraft:changed"]
    assert (item["code"], item["command"]) == ("kb.redraft", "kb redraft --changed")
    assert "REQ-F-001" in item["message"]


def test_quote_not_found_suggests_redraft_changed(tmp_path):
    root = drafted(tmp_path)
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001", "A seat costs 9 EUR."))
    found = items(root)
    assert found["redraft:changed"]["command"] == "kb redraft --changed"
    assert found["fix:quote.not_found:REQ-F-001"]["code"] == "quote.not_found"


def test_new_text_suggests_redraft_new(tmp_path):
    root = drafted(tmp_path)
    assert "redraft:new" not in items(root)
    put(root, "docs/n.md", DOC + "\n# Refunds\n\nA refund takes 14 days.\n")
    ok(root, "source", "sync")
    item = items(root)["redraft:new"]
    assert (item["code"], item["command"]) == ("kb.redraft", "kb redraft --new")
    assert "SRC-n#seg-02" in item["message"]


def test_check_errors_become_fix_items(tmp_path):
    root = drafted(tmp_path)
    put(root, "kb.config.yaml", CONFIG + '  - id: MOD-b\n    owner: TODO\n')
    put(root, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002", "A seat costs 9 EUR."))
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001") + "features:\n- FEAT-999\n")
    check = json.loads(invoke(root, "check", "--format", "json").stdout)
    errors = [i for i in check["issues"] if i["severity"] == "error"]
    assert {i["code"] for i in errors} >= {"quote.not_found", "ref.missing", "config.owner_todo"}
    fixes = {k: v for k, v in items(root).items() if k.startswith("fix:")}
    expected = {
        f"fix:{i['code']}:{i['record'] or i['path']}": i for i in errors if i["code"] != "config.owner_todo"
    }
    assert set(fixes) == set(expected)
    for key, item in fixes.items():
        issue = expected[key]
        assert item == {
            "id": key, "code": issue["code"], "message": issue["message"], "command": None,
            "path": issue["path"], "record": issue["record"],
        }


def test_secret_in_original_is_not_a_fix_item(tmp_path):
    root = ingested(tmp_path, **{"docs/keys.md": "# Keys\n\nThe staging key is AKIAIOSFODNN7EXAMPLE.\n"})
    assert not any(i.startswith("fix:source.secret_in_original") for i in items(root))
    assert todo(root)["state"]["errors"] >= 1


def test_untriaged_grouped_per_source(tmp_path):
    root = ingested(tmp_path, **{"docs/b.md": "# B\n\nOne.\n\n# C\n\nTwo.\n\n# D\n\nThree.\n"})
    triage = {k: v for k, v in items(root).items() if k.startswith("triage:")}
    assert set(triage) == {"triage:SRC-b", "triage:SRC-n"}
    assert triage["triage:SRC-b"]["code"] == "segment.untriaged"
    assert triage["triage:SRC-b"]["record"] == "SRC-b"
    assert triage["triage:SRC-b"]["command"] is None
    assert "give every segment a triage entry" in triage["triage:SRC-b"]["message"]


def test_todo_json_is_deterministic_and_sorted(tmp_path):
    root = ingested(tmp_path, **{"docs/b.md": "# B\n\nOne.\n"})
    put(root, "docs/c.md", "# C\n\nNew.\n")
    put(root, "docs/n.md", DOC.replace("5 EUR", "7 EUR"))
    first = ok(root, "todo", "--format", "json")
    second = ok(root, "todo", "--format", "json")
    assert first.stdout == second.stdout
    data = json.loads(first.stdout)
    assert first.stdout == json.dumps(data, sort_keys=True, indent=2) + "\n"
    ids = [d["id"] for d in data["do"]]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    assert {"ingest:docs/c.md", "sync", "triage:SRC-b", "triage:SRC-n"} <= set(ids)
    assert first.stderr.strip() == f"{len(ids)} to do, 0 questions for the user"
    assert set(data["state"]) == {"initialized", "sources", "records", "errors", "warnings", "baseline", "clean"}


def test_todo_bad_format_is_usage_error(tmp_path):
    r = invoke(repo(tmp_path), "todo", "--format", "yaml")
    assert r.exit_code == 2


def test_todo_bad_config_exits_2(tmp_path):
    root = repo(tmp_path, config="version: 2\n")
    r = invoke(root, "todo")
    assert r.exit_code == 2


def test_clean_repo_is_clean(tmp_path):
    root = drafted(tmp_path)
    t = build_todo(root)
    assert t.do == () and t.ask == ()
    assert t.state == {
        "initialized": True, "sources": 1, "records": 1, "errors": 0, "warnings": 0, "baseline": None, "clean": True,
    }
    r = ok(root, "todo")
    assert r.stderr.strip() == "0 to do, 0 questions for the user"
    ok(root, "baseline", "freeze", "v1")
    commit(root, "baseline v1")
    assert build_todo(root).state["baseline"] == "v1"
