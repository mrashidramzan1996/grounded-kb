import json
import os
import stat

import pytest

from helpers import git, invoke, make_git_repo
from kb.config.load import load_config
from kb.init.scaffold import init_repo


def _codes(repo, severity="error"):
    r = invoke(repo, "check", "--format", "json")
    return r, {i["code"] for i in json.loads(r.stdout)["issues"] if i["severity"] == severity}


def test_init_on_docs_only_repo(fixtures, tmp_path):
    repo = make_git_repo(fixtures / "repos/acme-billing", tmp_path, extra={"AGENTS.md": "# Ours\nkeep me\n"})
    init_repo(repo)
    cfg = load_config(repo / "kb.config.yaml")
    assert cfg.modules == [] and cfg.sources[0].paths == ["docs/**"]
    assert "keep me" in (repo / "AGENTS.md").read_text() and "@AGENTS.md" in (repo / "CLAUDE.md").read_text()
    assert "r[impl" in (repo / "AGENTS.md").read_text() and "fetch-depth: 0" in (repo / ".github/workflows/kb.yml").read_text()
    r, errors = _codes(repo)
    assert r.exit_code == 0 and errors == {"config.owner_todo"}


def test_init_takes_modules_from_codeowners_only(fixtures, tmp_path):
    repo = make_git_repo(fixtures / "repos/acme-billing", tmp_path, extra={"CODEOWNERS": "/services/billing/ @acme/billing\n* @acme/all\n"})
    init_repo(repo)
    m = load_config(repo / "kb.config.yaml").modules
    assert [(x.id, x.paths, x.owner, x.risk) for x in m] == [("MOD-billing", ["services/billing/**"], "@acme/billing", "standard")]


def test_init_refuses_twice(fixtures, tmp_path):
    repo = make_git_repo(fixtures / "repos/acme-billing", tmp_path)
    init_repo(repo)
    assert invoke(repo, "init").exit_code == 2


def test_cli_init_writes_and_lists_files(fixtures, tmp_path):
    repo = make_git_repo(fixtures / "repos/acme-billing", tmp_path)
    r = invoke(repo, "init")
    assert r.exit_code == 0
    assert (repo / "kb/README.md").is_file() and (repo / "kb.config.yaml").is_file()


def _modules(tmp_path, codeowners):
    repo = make_git_repo(None, tmp_path, extra={"CODEOWNERS": codeowners})
    init_repo(repo)
    return [(m.id, m.paths, m.owner) for m in load_config(repo / "kb.config.yaml").modules]


def test_modules_only_from_directory_rules(tmp_path):
    text = "* @a/all\n*.js @a/js\n/README.md @a/docs\n/src/api/** @a/api\nweb/ui/ @a/ui @a/other\n/glob*/x/ @a/g\n"
    assert _modules(tmp_path, text) == [
        ("MOD-api", ["src/api/**"], "@a/api"),
        ("MOD-ui", ["web/ui/**"], "@a/ui"),
    ]


def test_module_slug_collision_uses_full_path(tmp_path):
    text = "/services/billing/ @a/x\n/web/billing/ @a/y\n/lib/core/ @a/z\n"
    assert _modules(tmp_path, text) == [
        ("MOD-services-billing", ["services/billing/**"], "@a/x"),
        ("MOD-web-billing", ["web/billing/**"], "@a/y"),
        ("MOD-core", ["lib/core/**"], "@a/z"),
    ]


def test_module_without_handle_owner_is_todo(tmp_path):
    assert _modules(tmp_path, "/svc/a/ dev@acme.com\n/svc/b/\n") == [
        ("MOD-a", ["svc/a/**"], "TODO"),
        ("MOD-b", ["svc/b/**"], "TODO"),
    ]


def test_codeowners_location_order_first_found(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={".github/CODEOWNERS": "/gh/mod/ @a/gh\n", "docs/CODEOWNERS": "/dc/mod/ @a/dc\n"})
    init_repo(repo)
    assert [m.id for m in load_config(repo / "kb.config.yaml").modules] == ["MOD-mod"]
    assert load_config(repo / "kb.config.yaml").modules[0].paths == ["gh/mod/**"]
    assert "kb:begin" in (repo / ".github/CODEOWNERS").read_text()
    assert not (repo / "CODEOWNERS").exists()


def test_codeowners_block_is_commented_and_user_content_kept(tmp_path):
    original = "# mine\n* @a/all\n"
    repo = make_git_repo(None, tmp_path, extra={"CODEOWNERS": original})
    init_repo(repo)
    text = (repo / "CODEOWNERS").read_text()
    assert text.startswith(original)
    for p in ("/kb/requirements/", "/kb/decisions/", "/kb/invariants/", "/kb.config.yaml"):
        assert f"# {p} " in text
    live = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    assert live == ["* @a/all"]


def test_empty_modules_comment_and_no_docs(tmp_path):
    repo = make_git_repo(None, tmp_path)
    init_repo(repo)
    text = (repo / "kb.config.yaml").read_text()
    assert "# add with: kb module add (or let kb ingest propose them)" in text
    assert load_config(repo / "kb.config.yaml").sources == []
    assert "mode: warn-only" in text


def test_agents_block_uses_agent_rules_and_readme_points_to_it(tmp_path):
    from kb.rules import AGENT_RULES

    repo = make_git_repo(None, tmp_path)
    init_repo(repo)
    agents = (repo / "AGENTS.md").read_text()
    assert all(rule in agents for rule in AGENT_RULES)
    readme = (repo / "kb/README.md").read_text()
    assert "Commit document edits together with `kb source sync`, before running `kb ingest`." in readme
    assert "AGENTS.md" in readme and AGENT_RULES[2] not in readme


def test_github_ci_template(tmp_path):
    repo = make_git_repo(None, tmp_path)
    init_repo(repo, install_spec="grounded-kb==9.9")
    text = (repo / ".github/workflows/kb.yml").read_text()
    assert "fetch-depth: 0" in text and "KB_INSTALL_SPEC: 'grounded-kb==9.9'" in text
    assert 'uvx --from "$KB_INSTALL_SPEC" kb verify --base "origin/$BASE_REF" --comment --forge auto' in text
    assert "--strict" not in text
    assert "BASE_REF: ${{ github.base_ref }}" in text  # M7: never expanded inside run:


@pytest.mark.parametrize("ci, rel", [("github", ".github/workflows/kb.yml"), ("gitlab", ".gitlab-ci.yml")])
def test_no_default_install_spec(tmp_path, ci, rel):
    """I6: nothing installs an unpinned or unclaimed package name; the check warns until the spec is set."""
    repo = make_git_repo(None, tmp_path)
    init_repo(repo, ci=ci)
    text = (repo / rel).read_text()
    assert "KB_INSTALL_SPEC: ''" in text and "grounded-kb==" not in text
    assert all(line.split("uvx --from ", 1)[1].startswith('"$KB_INSTALL_SPEC"') for line in text.splitlines() if "uvx --from " in line)
    assert f"kb: set the install spec in {rel} — see kb/README.md" in text
    assert "install spec" in (repo / "kb/README.md").read_text()


def test_base_branch_follows_repo(tmp_path):
    repo = make_git_repo(None, tmp_path)
    git(repo, "branch", "-m", "trunk")
    init_repo(repo)
    assert "branches: [trunk]" in (repo / ".github/workflows/kb.yml").read_text()


def test_gitlab_ci_template(tmp_path):
    repo = make_git_repo(None, tmp_path)
    r = init_repo(repo, ci="gitlab")
    text = (repo / ".gitlab-ci.yml").read_text()
    assert 'GIT_DEPTH: "0"' in text and 'kb verify --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"' in text and "--strict" not in text
    assert not (repo / ".github").exists() and ".gitlab-ci.yml" in r.files_written


def test_existing_gitlab_ci_is_not_overwritten(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={".gitlab-ci.yml": "stages: [test]\n"})
    r = init_repo(repo, ci="gitlab")
    assert (repo / ".gitlab-ci.yml").read_text() == "stages: [test]\n"
    assert ".gitlab-ci.yml" not in r.files_written
    assert any("kb-check-mr" in i.message and "kb-check-default-branch" in i.message for i in r.issues)


def test_ci_none_writes_no_template(tmp_path):
    repo = make_git_repo(None, tmp_path)
    r = init_repo(repo, ci="none")
    assert not (repo / ".github").exists() and not (repo / ".gitlab-ci.yml").exists()
    assert r.files_written == sorted(r.files_written)
    assert r.files_written == [".gitignore", "AGENTS.md", "CLAUDE.md", "CODEOWNERS", "kb.config.yaml", "kb/README.md"]


def test_gitignore_block_keeps_user_content(tmp_path):
    """I5 (A-11): .kb/ holds caches and the brief; it is never committed."""
    repo = make_git_repo(None, tmp_path, extra={".gitignore": "node_modules/\n"})
    init_repo(repo, ci="none")
    text = (repo / ".gitignore").read_text()
    assert text.startswith("node_modules/\n") and "# kb:begin kb\n.kb/\n# kb:end kb\n" in text
    from kb.textblocks import upsert_block

    assert upsert_block(text, "kb", ".kb/", "hash") == text
    (repo / ".kb/cache").mkdir(parents=True)
    (repo / ".kb/cache/x").write_text("x")
    assert ".kb/" not in git(repo, "status", "--porcelain", "--untracked-files=all")


def test_config_has_no_spec_reference(tmp_path):
    repo = make_git_repo(None, tmp_path)
    init_repo(repo, ci="none")
    assert "kb_spec.md" not in (repo / "kb.config.yaml").read_text()


def test_unsafe_install_spec_is_refused_before_writing(tmp_path):
    repo = make_git_repo(None, tmp_path)
    with pytest.raises(Exception):
        init_repo(repo, install_spec="x\n  - run: evil")
    assert not (repo / "kb.config.yaml").exists()


@pytest.mark.parametrize("target", ["AGENTS.md", "CLAUDE.md", "CODEOWNERS", ".gitlab-ci.yml", "kb/README.md", ".github/workflows/kb.yml"])
def test_refuses_symlinked_target_and_writes_nothing(tmp_path, target):
    repo = make_git_repo(None, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    (repo / target).parent.mkdir(parents=True, exist_ok=True)
    (repo / target).symlink_to(outside)
    r = invoke(repo, "init", "--ci", "gitlab" if target == ".gitlab-ci.yml" else "github")
    assert r.exit_code == 2
    assert outside.read_text() == "secret\n" and not (repo / "kb.config.yaml").exists()


def test_refuses_non_regular_target(tmp_path):
    repo = make_git_repo(None, tmp_path)
    (repo / "AGENTS.md").mkdir()
    assert invoke(repo, "init").exit_code == 2 and not (repo / "kb.config.yaml").exists()


def test_refuses_symlinked_kb_dir(tmp_path):
    repo = make_git_repo(None, tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (repo / "kb").symlink_to(tmp_path / "elsewhere")
    assert invoke(repo, "init").exit_code == 2 and not (tmp_path / "elsewhere/README.md").exists()


def test_existing_file_mode_preserved(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"AGENTS.md": "mine\n"})
    os.chmod(repo / "AGENTS.md", 0o600)
    init_repo(repo)
    assert stat.S_IMODE((repo / "AGENTS.md").stat().st_mode) == 0o600


def test_existing_kb_readme_is_kept(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"kb/README.md": "mine\n"})
    r = init_repo(repo)
    assert (repo / "kb/README.md").read_text() == "mine\n" and "kb/README.md" not in r.files_written


def test_init_twice_blocks_idempotent_via_rewrite(tmp_path):
    repo = make_git_repo(None, tmp_path)
    init_repo(repo)
    (repo / "kb.config.yaml").unlink()
    before = {p: (repo / p).read_text() for p in ("AGENTS.md", "CLAUDE.md", "CODEOWNERS")}
    init_repo(repo)
    assert before == {p: (repo / p).read_text() for p in before}


def test_install_spec_is_single_quoted_in_both_templates(tmp_path):
    from ruamel.yaml import YAML

    for ci, rel in (("github", ".github/workflows/kb.yml"), ("gitlab", ".gitlab-ci.yml")):
        (tmp_path / ci).mkdir()
        repo = make_git_repo(None, tmp_path / ci)
        init_repo(repo, ci=ci, install_spec="grounded-kb>=1")
        text = (repo / rel).read_text()
        assert "KB_INSTALL_SPEC: 'grounded-kb>=1'" in text and 'uvx --from "$KB_INSTALL_SPEC" kb verify' in text
        YAML(typ="safe").load(text)


@pytest.mark.parametrize("spec", ["a'b", "a b", "a\tb", "a\nb", "a\x00b", "a;b", "a$b", 'a"b', "a`b", "a|b", "a&b"])
def test_unsafe_specs_rejected(tmp_path, spec):
    repo = make_git_repo(None, tmp_path)
    with pytest.raises(Exception):
        init_repo(repo, install_spec=spec)
    assert not (repo / "kb.config.yaml").exists()


def test_github_workflow_pr_and_push_steps(tmp_path):
    from ruamel.yaml import YAML

    repo = make_git_repo(None, tmp_path)
    init_repo(repo, install_spec="grounded-kb==9.9")
    text = (repo / ".github/workflows/kb.yml").read_text()
    wf = YAML(typ="safe").load(text)
    assert set(wf[True] if True in wf else wf["on"]) == {"pull_request", "push"}
    steps = wf["jobs"]["check"]["steps"]
    runs = {s["if"]: s["run"] for s in steps if "run" in s}
    assert len(runs) == 3
    pr, push = "env.KB_INSTALL_SPEC != '' && github.event_name == 'pull_request'", "env.KB_INSTALL_SPEC != '' && github.event_name == 'push'"
    assert runs[pr].endswith('kb verify --base "origin/$BASE_REF" --comment --forge auto') and "${{" not in runs[pr]
    assert runs[push].endswith("kb verify --all") and "--base" not in runs[push]
    assert runs["env.KB_INSTALL_SPEC == ''"].startswith("echo ")
    assert wf["jobs"]["check"]["env"] == {"KB_INSTALL_SPEC": "grounded-kb==9.9", "BASE_REF": "${{ github.base_ref }}"}
    assert wf["jobs"]["check"]["steps"][0]["with"]["fetch-depth"] == 0
    assert "branches: [main]" in text


def test_gitlab_ci_mr_and_default_branch_jobs(tmp_path):
    from ruamel.yaml import YAML

    repo = make_git_repo(None, tmp_path)
    init_repo(repo, ci="gitlab")
    ci = YAML(typ="safe").load((repo / ".gitlab-ci.yml").read_text())
    mr, default = ci["kb-check-mr"], ci["kb-check-default-branch"]
    assert any('--base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"' in s for s in mr["script"])
    assert all("--base" not in s for s in default["script"]) and any(s.endswith("kb verify --all") for s in default["script"])
    assert "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH" in str(default["rules"])
    assert mr["variables"]["GIT_DEPTH"] == "0" and default["variables"]["GIT_DEPTH"] == "0"


def _snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*")) if ".git" not in p.relative_to(root).parts}


def test_failed_write_rolls_back_and_init_can_be_rerun(tmp_path, monkeypatch):
    from kb.init import scaffold

    repo = make_git_repo(None, tmp_path, extra={"AGENTS.md": "mine\n", "docs/a.md": "x\n"})
    os.chmod(repo / "AGENTS.md", 0o640)
    before = _snapshot(repo)
    real, calls = scaffold._write_atomic, []

    def flaky(root, rel, text):
        calls.append(rel)
        if len(calls) == 3:
            raise OSError("disk full")
        real(root, rel, text)

    monkeypatch.setattr(scaffold, "_write_atomic", flaky)
    with pytest.raises(OSError):
        init_repo(repo)
    assert _snapshot(repo) == before and stat.S_IMODE((repo / "AGENTS.md").stat().st_mode) == 0o640
    monkeypatch.setattr(scaffold, "_write_atomic", real)
    assert init_repo(repo).files_written


def test_config_is_written_last(tmp_path, monkeypatch):
    from kb.init import scaffold

    repo = make_git_repo(None, tmp_path)
    order, real = [], scaffold._write_atomic
    monkeypatch.setattr(scaffold, "_write_atomic", lambda r, rel, t: (order.append(rel), real(r, rel, t)))
    init_repo(repo)
    assert order[-1] == "kb.config.yaml"


def test_github_template_runs_verify_with_comment_and_artifact(tmp_path):
    from ruamel.yaml import YAML

    repo = make_git_repo(None, tmp_path)
    init_repo(repo, install_spec="grounded-kb==9.9")
    text = (repo / ".github/workflows/kb.yml").read_text()
    wf = YAML(typ="safe").load(text)
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["check"]["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert "# install your project's test dependencies here" in text
    steps = wf["jobs"]["check"]["steps"]
    verify = next(s for s in steps if "--comment" in s.get("run", ""))
    assert verify["env"] == {"GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    assert "${{ secrets.GITHUB_TOKEN }}" in text
    upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@v4"))
    assert upload["if"] == "always()"
    assert ".kb/cache/status.json" in upload["with"]["path"] and ".kb/cache/verify.md" in upload["with"]["path"]
    assert upload["with"]["include-hidden-files"] is True and upload["with"]["if-no-files-found"] == "warn"
    assert any(s.get("uses") == "actions/checkout@v4" for s in steps)


def test_gitlab_template_runs_verify_and_keeps_artifacts(tmp_path):
    from ruamel.yaml import YAML

    repo = make_git_repo(None, tmp_path)
    init_repo(repo, ci="gitlab")
    ci = YAML(typ="safe").load((repo / ".gitlab-ci.yml").read_text())
    mr = ci["kb-check-mr"]
    assert any(s.endswith('kb verify --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" --format markdown') for s in mr["script"])
    assert mr["artifacts"]["paths"] == [".kb/cache/status.json", ".kb/cache/verify.md"]
    assert mr["artifacts"]["when"] == "always"
    assert any("kb verify --all" in s for s in ci["kb-check-default-branch"]["script"])
