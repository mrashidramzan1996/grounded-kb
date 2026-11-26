"""`kb codeowners sync`: kb's marked CODEOWNERS block, written from policy.approvers, and the `kb todo` item for it."""

from helpers import commit, invoke, ok, todo_json


def repo_with_init(tmp_path, intent="[\"@acme/product\"]", decisions="[\"@acme/arch\"]", extra_owners=""):
    from helpers import make_git_repo

    root = make_git_repo(None, tmp_path)
    ok(root, "init", "--ci", "none")
    config = (root / "kb.config.yaml").read_text()
    config = config.replace("intent: [TODO]", f"intent: {intent}").replace("decisions: [TODO]", f"decisions: {decisions}")
    (root / "kb.config.yaml").write_text(config)
    if extra_owners:
        (root / "CODEOWNERS").write_text(extra_owners + (root / "CODEOWNERS").read_text())
    commit(root, "init")
    return root


def block(text):
    return text.split("# kb:begin kb\n", 1)[1].split("# kb:end kb", 1)[0]


def test_sync_activates_the_block_with_intent_and_decision_handles(tmp_path):
    root = repo_with_init(tmp_path)
    r = ok(root, "codeowners", "sync")
    assert "CODEOWNERS" in r.stdout
    lines = block((root / "CODEOWNERS").read_text()).splitlines()
    rules = {l.split()[0]: l.split()[1:] for l in lines if l.strip() and not l.startswith("#")}
    assert rules["/kb/requirements/"] == ["@acme/product"] and rules["/kb/eval/"] == ["@acme/product"]
    assert rules["/kb/decisions/"] == ["@acme/arch"] and rules["/kb.config.yaml"] == ["@acme/arch"]
    assert rules["/.github/workflows/"] == ["@acme/arch"] and rules["/CODEOWNERS"] == ["@acme/arch"]
    assert not any("TODO" in l for l in lines if not l.startswith("#"))


def test_sync_is_idempotent_and_leaves_outside_lines_alone(tmp_path):
    root = repo_with_init(tmp_path, extra_owners="/src/ @acme/dev\n")
    ok(root, "codeowners", "sync")
    first = (root / "CODEOWNERS").read_text()
    assert first.startswith("/src/ @acme/dev\n")
    r = ok(root, "codeowners", "sync")
    assert "up to date" in r.stdout and (root / "CODEOWNERS").read_text() == first
    (root / "CODEOWNERS").write_text(first + "/docs/ @acme/docs\n")
    ok(root, "codeowners", "sync")
    assert (root / "CODEOWNERS").read_text().endswith("/docs/ @acme/docs\n")


def test_sync_refuses_while_approvers_are_todo(tmp_path):
    root = repo_with_init(tmp_path, intent="[TODO]")
    before = (root / "CODEOWNERS").read_text()
    r = invoke(root, "codeowners", "sync")
    assert r.exit_code == 2 and "policy.approvers" in r.output
    assert (root / "CODEOWNERS").read_text() == before


def test_sync_refuses_without_a_kb_block(tmp_path):
    root = repo_with_init(tmp_path)
    (root / "CODEOWNERS").write_text("/src/ @acme/dev\n")
    assert invoke(root, "codeowners", "sync").exit_code == 2


def test_todo_asks_for_the_sync_until_it_is_done(tmp_path):
    root = repo_with_init(tmp_path)
    item = next((d for d in todo_json(root)["do"] if d["id"] == "codeowners"), None)
    assert item is not None and item["command"] == "kb codeowners sync"
    ok(root, "codeowners", "sync")
    commit(root, "codeowners")
    assert "codeowners" not in [d["id"] for d in todo_json(root)["do"]]
    # a changed approver makes the block differ from what sync would write
    config = (root / "kb.config.yaml").read_text().replace("@acme/arch", "@acme/arch2")
    (root / "kb.config.yaml").write_text(config)
    commit(root, "approver")
    assert "codeowners" in [d["id"] for d in todo_json(root)["do"]]


def test_todo_does_not_ask_while_approvers_are_todo(tmp_path):
    root = repo_with_init(tmp_path, decisions="[TODO]")
    assert "codeowners" not in [d["id"] for d in todo_json(root)["do"]]
