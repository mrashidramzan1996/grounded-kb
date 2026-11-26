from pathlib import Path
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


def test_generic_install_refuses_a_hooks_dir_outside_the_repo(acme_repo, tmp_path):
    from helpers import git

    shared = tmp_path / "global-hooks"
    shared.mkdir()
    git(acme_repo, "config", "core.hooksPath", str(shared))
    out = invoke(acme_repo, "agents", "install", "generic")
    assert out.exit_code == 2 and not list(shared.iterdir())
    assert "outside" in out.stderr and "kb hook pre-commit" in out.stderr


def _run_hook_script(hook: Path, path_dirs: list[Path], cwd: Path) -> int:
    import subprocess

    env = {"PATH": ":".join(str(p) for p in path_dirs), "HOME": str(cwd)}
    return subprocess.run(["/bin/sh", str(hook)], cwd=cwd, env=env, capture_output=True).returncode


def _fake_kb(directory: Path, exit_code: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "kb"
    script.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    script.chmod(0o755)
    return directory


def test_installed_pre_commit_allows_commits_when_kb_is_not_installed(acme_repo, tmp_path):
    get_agent("generic").install(acme_repo)
    hook = acme_repo / ".git/hooks/pre-commit"
    assert _run_hook_script(hook, [Path("/usr/bin"), Path("/bin")], acme_repo) == 0


def test_installed_pre_commit_passes_kb_exit_code_through(acme_repo, tmp_path):
    get_agent("generic").install(acme_repo)
    hook = acme_repo / ".git/hooks/pre-commit"
    failing = _fake_kb(tmp_path / "fail", 1)
    passing = _fake_kb(tmp_path / "pass", 0)
    assert _run_hook_script(hook, [failing, Path("/usr/bin"), Path("/bin")], acme_repo) == 1
    assert _run_hook_script(hook, [passing, Path("/usr/bin"), Path("/bin")], acme_repo) == 0


# --- kb/README.md and the CODEOWNERS block (0.4.0 upgrade) -------------------------------------------------------

OLD_README = "# kb: product knowledge base\n\nAn older kb's guide.\n"
OLD_BLOCK = ("# kb:begin kb\n# kb: approvals for the knowledge base.\n# /kb/requirements/ TODO\n"
             "# /.github/workflows/kb.yml TODO\n# kb:end kb\n")


def _template() -> str:
    from importlib.resources import files

    return (files("kb.init") / "templates" / "kb_readme.md").read_text(encoding="utf-8")


def test_install_refreshes_the_kb_readme_kb_wrote(acme_repo):
    (acme_repo / "kb").mkdir(exist_ok=True)
    (acme_repo / "kb/README.md").write_text(OLD_README)
    r = invoke(acme_repo, "agents", "install", "generic")
    assert r.exit_code == 0, r.output
    assert (acme_repo / "kb/README.md").read_text() == _template()
    assert "updated kb/README.md" in r.stdout
    again = invoke(acme_repo, "agents", "install", "generic")
    assert "kb/README.md is up to date" in again.stdout


def test_install_leaves_a_readme_kb_did_not_write(acme_repo):
    (acme_repo / "kb").mkdir(exist_ok=True)
    (acme_repo / "kb/README.md").write_text("# Our knowledge base\n\nMine.\n")
    r = invoke(acme_repo, "agents", "install", "generic")
    assert r.exit_code == 0 and (acme_repo / "kb/README.md").read_text() == "# Our knowledge base\n\nMine.\n"
    assert "kb/README.md was not written by kb" in r.stdout


def test_install_refreshes_an_untouched_codeowners_block_and_reminds_about_an_edited_one(acme_repo):
    from kb.init.scaffold import _codeowners_block

    (acme_repo / "CODEOWNERS").write_text("* @acme/all\n" + OLD_BLOCK)
    r = invoke(acme_repo, "agents", "install", "generic")
    assert r.exit_code == 0, r.output
    text = (acme_repo / "CODEOWNERS").read_text()
    assert text.startswith("* @acme/all\n") and _codeowners_block() in text and "updated CODEOWNERS" in r.stdout
    # once the person has named owners in it, kb only says what the block lacks
    edited = OLD_BLOCK.replace("# /kb/requirements/ TODO", "/kb/requirements/ @acme/product")
    (acme_repo / "CODEOWNERS").write_text(edited)
    r = invoke(acme_repo, "agents", "install", "generic")
    assert (acme_repo / "CODEOWNERS").read_text() == edited
    assert "CODEOWNERS: kb's block lacks" in r.stdout
    for p in ("/kb/eval/", "/kb/baseline/", "/.github/workflows/"):
        assert p in r.stdout
    assert "/kb/requirements/," not in r.stdout
