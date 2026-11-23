import json
import os
import stat

import pytest

from helpers import SETTINGS_PATH, invoke
from kb.agents.registry import get_agent

CMD = "command -v kb >/dev/null 2>&1 && kb hook {event} --agent {agent} || true"


def install(repo, agent):
    return get_agent(agent).install(repo)


def seed(repo, agent, data):
    path = repo / SETTINGS_PATH[agent]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def settings(repo, agent):
    return json.loads((repo / SETTINGS_PATH[agent]).read_text())


def commands(data, event):
    return [h["command"] for group in data["hooks"][event] for h in group["hooks"]]


@pytest.mark.parametrize("agent", ["claude-code", "codex"])
def test_install_is_idempotent_and_preserves_settings(acme_repo, agent):
    seed(acme_repo, agent, {"other": 1})
    install(acme_repo, agent)
    first = (acme_repo / SETTINGS_PATH[agent]).read_bytes()
    install(acme_repo, agent)
    assert (acme_repo / SETTINGS_PATH[agent]).read_bytes() == first
    data = settings(acme_repo, agent)
    assert data["other"] == 1
    assert commands(data, "SessionStart") == [CMD.format(event="session-start", agent=agent)]
    assert commands(data, "Stop") == [CMD.format(event="stop", agent=agent)]


def test_install_keeps_existing_hooks_and_mode(acme_repo):
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    path = seed(acme_repo, "claude-code", {"hooks": {"Stop": [other]}})
    path.chmod(0o640)
    install(acme_repo, "claude-code")
    data = settings(acme_repo, "claude-code")
    assert data["hooks"]["Stop"][0] == other
    assert commands(data, "Stop")[1].startswith("command -v kb")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


def test_codex_install_prints_trust_notice(acme_repo):
    lines = install(acme_repo, "codex")
    assert any("trusted project" in line and "/hooks" in line for line in lines)


@pytest.mark.parametrize("agent", ["claude-code", "codex"])
def test_install_refuses_symlinked_target(acme_repo, agent, tmp_path):
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("{}")
    path = acme_repo / SETTINGS_PATH[agent]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(elsewhere)
    result = invoke(acme_repo, "agents", "install", agent)
    assert result.exit_code == 2
    assert elsewhere.read_text() == "{}"


def test_install_refuses_invalid_json_and_writes_nothing(acme_repo):
    path = acme_repo / SETTINGS_PATH["claude-code"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert invoke(acme_repo, "agents", "install", "claude-code").exit_code == 2
    assert path.read_text() == "{not json"


def test_generic_install_writes_pre_commit_only_if_absent(acme_repo):
    hook = acme_repo / ".git/hooks/pre-commit"
    hook.unlink(missing_ok=True)
    install(acme_repo, "generic")
    assert stat.S_IMODE(hook.stat().st_mode) == 0o755
    assert "kb hook pre-commit" in hook.read_text()
    hook.write_text("#!/bin/sh\necho mine\n")
    lines = install(acme_repo, "generic")
    assert hook.read_text() == "#!/bin/sh\necho mine\n"
    assert any("kb hook pre-commit" in line for line in lines)


def test_cli_install_unknown_agent_exits_2(acme_repo):
    result = invoke(acme_repo, "agents", "install", "nope")
    assert result.exit_code == 2


def test_cli_install_several(acme_repo):
    result = invoke(acme_repo, "agents", "install", "claude-code", "codex")
    assert result.exit_code == 0, result.output
    assert (acme_repo / ".claude/settings.json").exists() and (acme_repo / ".codex/hooks.json").exists()


def test_pre_commit_hooks_yaml_exposes_kb_check():
    from pathlib import Path

    from kb.yamlio import load_yaml_strict

    data = load_yaml_strict((Path(__file__).parents[2] / ".pre-commit-hooks.yaml").read_text())
    assert data[0]["id"] == "kb-check" and "kb hook pre-commit" in data[0]["entry"]


def test_generic_install_honours_core_hooks_path(acme_repo):
    from helpers import git

    git(acme_repo, "config", "core.hooksPath", ".githooks")
    lines = install(acme_repo, "generic")
    hook = acme_repo / ".githooks/pre-commit"
    assert "kb hook pre-commit" in hook.read_text() and stat.S_IMODE(hook.stat().st_mode) == 0o755
    assert not (acme_repo / ".git/hooks/pre-commit").exists() and any("pre-commit" in line for line in lines)


def test_generic_install_in_a_worktree(acme_repo, tmp_path):
    from helpers import git

    wt = tmp_path / "wt"
    git(acme_repo, "worktree", "add", str(wt), "-b", "other")
    assert (wt / ".git").is_file()
    install(wt, "generic")
    assert "kb hook pre-commit" in (acme_repo / ".git/hooks/pre-commit").read_text()


def test_generic_install_refuses_symlinked_pre_commit(acme_repo):
    hook = acme_repo / ".git/hooks/pre-commit"
    hook.unlink(missing_ok=True)
    target = acme_repo / "elsewhere.sh"
    target.write_text("x")
    hook.symlink_to(target)
    assert invoke(acme_repo, "agents", "install", "generic").exit_code == 2
    assert target.read_text() == "x"
