import json

import pytest

from helpers import break_ref, invoke, payload
from kb.hooks import run_hook


def test_session_start_on_docs_only_repo_lists_modules_and_not_built(acme_repo_drafted, fixtures):
    out, code = run_hook(acme_repo_drafted, "session-start", "claude-code", payload(fixtures, "claude_code/session_start"))
    assert code == 0 and "[MOD-billing]" in out and "not built" in out


def test_session_start_warns_on_load_errors_and_still_exits_0(valid_git_repo, fixtures):
    (valid_git_repo / "kb/requirements/REQ-F-001.yaml").write_text("id: [unclosed\n")
    out, code = run_hook(valid_git_repo, "session-start", "codex", payload(fixtures, "codex/session_start"))
    assert code == 0
    assert out.startswith("⚠ 1 KB files failed to load; run `kb check`\n")


def test_session_start_without_config_prints_nothing_and_exits_0(tmp_path, capsys):
    assert run_hook(tmp_path, "session-start", "claude-code", "") == ("", 0)
    assert "kb hook" in capsys.readouterr().err


def test_claude_stop_blocks_on_errors_but_not_when_active(valid_git_repo, fixtures):
    break_ref(valid_git_repo)
    out, code = run_hook(valid_git_repo, "stop", "claude-code", payload(fixtures, "claude_code/stop"))
    data = json.loads(out)
    assert code == 0 and data["decision"] == "block" and "ref.missing" in data["reason"]
    assert data["reason"].startswith("kb check: ") and "run `kb check --changed`" in data["reason"]
    assert run_hook(valid_git_repo, "stop", "claude-code", payload(fixtures, "claude_code/stop_active")) == ("", 0)


def test_claude_stop_silent_when_clean(valid_git_repo, fixtures):
    assert run_hook(valid_git_repo, "stop", "claude-code", payload(fixtures, "claude_code/stop")) == ("", 0)


def test_codex_stop_outputs_valid_json(valid_git_repo, fixtures):
    break_ref(valid_git_repo)
    out, code = run_hook(valid_git_repo, "stop", "codex", payload(fixtures, "codex/stop"))
    assert code == 0 and json.loads(out)["decision"] == "block"


def test_codex_stop_noop_is_valid_json(valid_git_repo, fixtures):
    break_ref(valid_git_repo)
    out, code = run_hook(valid_git_repo, "stop", "codex", payload(fixtures, "codex/stop_active"))
    assert code == 0 and "decision" not in json.loads(out)
    out, _ = run_hook(valid_git_repo, "stop", "codex", "")  # empty payload -> not active -> blocks
    assert json.loads(out)["decision"] == "block"


@pytest.mark.parametrize("bad", ["", "not json", "[1, 2]", "null"])
def test_invalid_payload_is_an_empty_payload(valid_git_repo, bad):
    break_ref(valid_git_repo)
    out, code = run_hook(valid_git_repo, "stop", "claude-code", bad)
    assert code == 0 and json.loads(out)["decision"] == "block"


def test_stop_internal_failure_never_blocks(tmp_path, capsys):
    assert run_hook(tmp_path, "stop", "claude-code", "") == ("", 0)
    err = capsys.readouterr().err
    assert err.startswith("kb hook stop:") and err.count("\n") == 1


def test_stop_git_failure_never_blocks(valid_git_repo, capsys):
    import shutil

    shutil.rmtree(valid_git_repo / ".git")
    assert run_hook(valid_git_repo, "stop", "claude-code", "") == ("", 0)
    assert capsys.readouterr().err.startswith("kb hook stop:")


def test_pre_commit_fails_on_errors(valid_git_repo):
    break_ref(valid_git_repo)
    out, code = run_hook(valid_git_repo, "pre-commit", "generic", "")
    assert code == 1 and "ref.missing" in out


def test_pre_commit_passes_when_clean(valid_git_repo):
    assert run_hook(valid_git_repo, "pre-commit", "generic", "") == ("", 0)


def test_unknown_event_or_agent_exits_2_via_cli(valid_git_repo):
    assert invoke(valid_git_repo, "hook", "nope").exit_code == 2
    assert invoke(valid_git_repo, "hook", "stop", "--agent", "nope").exit_code == 2


def test_cli_hook_stop_reads_stdin(valid_git_repo, fixtures):
    from typer.testing import CliRunner

    from kb.cli import app

    break_ref(valid_git_repo)
    result = CliRunner().invoke(
        app, ["-C", str(valid_git_repo), "hook", "stop", "--agent", "claude-code"], input=payload(fixtures, "claude_code/stop")
    )
    assert result.exit_code == 0 and json.loads(result.stdout)["decision"] == "block"


def test_hook_without_dash_c_resolves_the_repo_root_from_a_subdirectory(valid_git_repo, fixtures, monkeypatch):
    from typer.testing import CliRunner

    from kb.cli import app

    break_ref(valid_git_repo)
    sub = valid_git_repo / "kb" / "requirements"
    monkeypatch.chdir(sub)
    result = CliRunner().invoke(app, ["hook", "stop", "--agent", "claude-code"], input=payload(fixtures, "claude_code/stop"))
    assert result.exit_code == 0 and json.loads(result.stdout)["decision"] == "block"


def test_hook_outside_a_git_repo_stays_fail_safe(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from kb.cli import app

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["hook", "stop"], input="")
    assert result.exit_code == 0 and result.stdout == ""


def test_hook_with_git_absent_stays_fail_safe(tmp_path, monkeypatch):
    import subprocess

    from typer.testing import CliRunner

    from kb.cli import app

    def no_git(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", no_git)
    result = CliRunner().invoke(app, ["hook", "stop"], input="")
    assert result.exit_code == 0 and result.stdout == ""


# --- I3: hooks block only on new errors the agent can fix ---------------------------------------

def commit_all(repo, message="c"):
    from helpers import git

    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def test_pre_commit_without_config_exits_0_silently(tmp_path, capsys):
    from helpers import make_git_repo

    repo = make_git_repo(None, tmp_path)
    assert run_hook(repo, "pre-commit", "generic", "") == ("", 0)
    assert run_hook(tmp_path, "pre-commit", "generic", "") == ("", 0)
    assert capsys.readouterr().err == ""


def test_unrelated_commit_with_pre_existing_errors_is_allowed(valid_git_repo, fixtures):
    break_ref(valid_git_repo)
    commit_all(valid_git_repo, "broken on main")
    (valid_git_repo / "notes.txt").write_text("meeting notes\n")
    assert run_hook(valid_git_repo, "pre-commit", "generic", "") == ("", 0)
    assert run_hook(valid_git_repo, "stop", "claude-code", payload(fixtures, "claude_code/stop")) == ("", 0)


def test_pre_existing_errors_on_main_do_not_block_a_feature_branch(valid_git_repo, fixtures):
    break_ref(valid_git_repo)
    commit_all(valid_git_repo, "broken on main")
    from helpers import git

    git(valid_git_repo, "checkout", "-q", "-b", "feature")
    (valid_git_repo / "notes.txt").write_text("x\n")
    commit_all(valid_git_repo, "notes")
    assert run_hook(valid_git_repo, "stop", "codex", payload(fixtures, "codex/stop")) == ("{}", 0)


def test_new_dangling_marker_blocks(valid_git_repo, fixtures):
    break_ref(valid_git_repo)
    commit_all(valid_git_repo, "broken on main")
    (valid_git_repo / "services/billing/refunds.py").write_text("# r[impl REQ-F-999]\ndef refund(): ...\n")
    out, code = run_hook(valid_git_repo, "pre-commit", "generic", "")
    assert code == 1 and "marker.unknown_id" in out and "ref.missing" not in out
    data = json.loads(run_hook(valid_git_repo, "stop", "claude-code", payload(fixtures, "claude_code/stop"))[0])
    assert data["decision"] == "block" and "marker.unknown_id" in data["reason"] and "1 errors" in data["reason"]


def test_owner_todo_never_blocks_and_is_noted(valid_git_repo, fixtures, capsys):
    cfg = valid_git_repo / "kb.config.yaml"
    cfg.write_text(cfg.read_text().replace("- id: MOD-sessions\n  owner: team-platform", "- id: MOD-sessions\n  owner: TODO"))
    assert run_hook(valid_git_repo, "stop", "claude-code", payload(fixtures, "claude_code/stop")) == ("", 0)
    assert run_hook(valid_git_repo, "pre-commit", "generic", "") == ("", 0)
    err = capsys.readouterr().err.splitlines()
    assert err and all(line.startswith("kb: note config.owner_todo") for line in err)


def add_secret_doc(repo):
    (repo / "docs/k.md").write_text("# Keys\n\nThe key is AKIAIOSFODNN7EXAMPLE.\n")
    assert invoke(repo, "ingest").exit_code == 0


def test_new_secret_in_original_blocks_pre_commit(md_repo_ingested):
    add_secret_doc(md_repo_ingested)
    out, code = run_hook(md_repo_ingested, "pre-commit", "generic", "")
    assert code == 1 and "source.secret_in_original" in out and "kb source sync" in out


def test_new_secret_in_original_is_only_a_note_at_stop(md_repo_ingested, fixtures, capsys):
    add_secret_doc(md_repo_ingested)
    assert run_hook(md_repo_ingested, "stop", "claude-code", payload(fixtures, "claude_code/stop")) == ("", 0)
    assert "kb: note source.secret_in_original" in capsys.readouterr().err


def test_pre_existing_secret_in_original_does_not_block_pre_commit(md_repo_ingested, capsys):
    add_secret_doc(md_repo_ingested)
    commit_all(md_repo_ingested, "secret already on main")
    (md_repo_ingested / "notes.txt").write_text("x\n")
    assert run_hook(md_repo_ingested, "pre-commit", "generic", "") == ("", 0)
    assert "kb: note source.secret_in_original" in capsys.readouterr().err


def test_without_a_default_branch_every_error_counts(valid_git_repo):
    from helpers import git

    break_ref(valid_git_repo)
    commit_all(valid_git_repo, "broken")
    git(valid_git_repo, "branch", "-m", "main", "trunk")
    out, code = run_hook(valid_git_repo, "pre-commit", "generic", "")
    assert code == 1 and "ref.missing" in out


def test_base_snapshot_leaves_no_worktree_behind(valid_git_repo):
    from helpers import git

    break_ref(valid_git_repo)
    commit_all(valid_git_repo, "broken on main")
    (valid_git_repo / "services/billing/refunds.py").write_text("# r[impl REQ-F-999]\n")
    run_hook(valid_git_repo, "pre-commit", "generic", "")
    assert git(valid_git_repo, "worktree", "list").count("\n") == 1


# --- agent-driven mode: the to-do summary at session start ---------------------------------------

SUMMARY = "## kb to-do\n{n} to do, {m} for the user. Run `kb todo` and follow the playbook in AGENTS.md.\n"


def _count(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def test_session_start_prepends_todo_summary(acme_repo, fixtures):
    from kb.todo import build_todo

    todo = build_todo(acme_repo)
    assert todo.do  # the documents are not ingested yet
    out, code = run_hook(acme_repo, "session-start", "claude-code", payload(fixtures, "claude_code/session_start"))
    assert code == 0 and out.startswith(SUMMARY.format(n=_count(len(todo.do), "thing"), m=_count(len(todo.ask), "question")))
    assert "# KB rules\n" in out


def test_session_start_has_no_summary_when_there_is_nothing_to_do(valid_git_repo, monkeypatch):
    import kb.hooks
    from kb.todo import Todo

    monkeypatch.setattr(kb.hooks, "build_todo", lambda root: Todo((), (), {}))
    out, code = run_hook(valid_git_repo, "session-start", "claude-code", "")
    assert code == 0 and "## kb to-do" not in out and "# KB rules\n" in out


def test_session_start_survives_todo_error(valid_git_repo, monkeypatch, capsys):
    import kb.hooks

    def broken(root):
        raise RuntimeError("todo exploded")

    monkeypatch.setattr(kb.hooks, "build_todo", broken)
    out, code = run_hook(valid_git_repo, "session-start", "claude-code", "")
    assert code == 0 and "## kb to-do" not in out and "# KB rules\n" in out
    assert capsys.readouterr().err == "kb: note todo.failed: RuntimeError: todo exploded\n"


def test_session_start_summary_follows_the_load_warning(valid_git_repo, monkeypatch):
    import kb.hooks
    from kb.todo import DoItem, Todo

    (valid_git_repo / "kb/requirements/REQ-F-001.yaml").write_text("id: [unclosed\n")
    item = DoItem("fix:x", "x", "fix it", None, None, None)
    monkeypatch.setattr(kb.hooks, "build_todo", lambda root: Todo((item,), (), {}))
    out, _ = run_hook(valid_git_repo, "session-start", "codex", "")
    lines = out.splitlines()
    assert lines[0].startswith("⚠ 1 KB files failed to load") and lines[1] == "## kb to-do"
    assert lines[2].startswith("1 thing to do, 0 questions for the user.")


@pytest.mark.parametrize("n_do, n_ask, text", [(2, 1, "2 things to do, 1 question for the user."), (0, 3, "0 things to do, 3 questions for the user.")])
def test_session_start_summary_is_pluralised(valid_git_repo, monkeypatch, n_do, n_ask, text):
    import kb.hooks
    from kb.todo import AskItem, DoItem, Todo

    do = tuple(DoItem(f"fix:{i}", "x", "fix it", None, None, None) for i in range(n_do))
    ask = tuple(AskItem(f"freeze:v{i}", "freeze", "q", {}, (), "q") for i in range(n_ask))
    monkeypatch.setattr(kb.hooks, "build_todo", lambda root: Todo(do, ask, {}))
    out, _ = run_hook(valid_git_repo, "session-start", "claude-code", "")
    assert out.splitlines()[1].startswith(text)
