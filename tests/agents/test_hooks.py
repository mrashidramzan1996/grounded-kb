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


def test_pre_commit_config_missing_exits_2(tmp_path):
    assert run_hook(tmp_path, "pre-commit", "generic", "")[1] == 2


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
