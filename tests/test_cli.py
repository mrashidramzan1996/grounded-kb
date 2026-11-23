import importlib.metadata

from helpers import invoke


def test_version_matches_metadata(tmp_path):
    r = invoke(tmp_path, "version")
    assert r.exit_code == 0 and r.stdout.strip() == importlib.metadata.version("grounded-kb")


def test_help_lists_version(tmp_path):
    assert "version" in invoke(tmp_path, "--help").stdout


# --- M1: every tool error is exit 2 with a message, never a traceback ---------------------------

import pytest  # noqa: E402

from kb.records.io import RecordError  # noqa: E402
from kb.safepath import UnsafePath  # noqa: E402
from kb.yamlio import YamlError  # noqa: E402

# (command, the name in kb.cli it calls first)
COMMANDS = [
    (("source", "add", "docs/pricing-spec-v3.md"), "load_config"),
    (("source", "sync"), "load_config"),
    (("ingest",), "load_config"),
    (("ingest", "--report"), "ingest_report"),
    (("context", "--module", "MOD-billing"), "load_config"),
    (("trace", "REQ-F-001"), "trace_chain"),
    (("status",), "load_config"),
    (("module", "list"), "load_config"),
    (("module", "add", "MOD-x", "--owner", "TODO"), "add_module"),
    (("module", "paths", "MOD-billing", "--set", "x/**"), "load_config"),
    (("baseline", "freeze", "v1"), "freeze_baseline"),
]
ERRORS = [OSError("disk on fire"), UnsafePath("bad path"), RecordError("bad record"), YamlError("bad yaml")]


@pytest.mark.parametrize("args, target", COMMANDS, ids=[" ".join(a) for a, _ in COMMANDS])
@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
def test_tool_errors_exit_2_without_traceback(valid_git_repo, monkeypatch, args, target, error):
    def boom(*a, **k):
        raise error

    monkeypatch.setattr(f"kb.cli.{target}", boom)
    r = invoke(valid_git_repo, *args)
    assert r.exit_code == 2, (r.output, r.exception)
    assert str(error) in r.stderr and "Traceback" not in r.output


# --- M3: a repository without commits --------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ("check", "--base", "main"), ("ingest",), ("ingest", "--report"), ("baseline", "freeze", "v1"), ("context", "--changed"),
])
def test_commands_needing_head_say_commit_first(tmp_path, args):
    import subprocess

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/n.md").write_text("# N\n\nText.\n")
    (tmp_path / "kb.config.yaml").write_text('version: 1\nsources:\n  - paths: ["docs/**"]\n')
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    r = invoke(tmp_path, *args)
    assert r.exit_code == 2 and "commit at least once first" in r.stderr, r.output
    assert "Traceback" not in r.output


def test_readme_covers_install_every_command_and_posix():
    """M5."""
    from pathlib import Path

    readme = (Path(__file__).parents[1] / "README.md").read_text()
    assert "uv tool install" in readme and "POSIX" in readme
    for cmd in ("kb init", "kb source add", "kb source sync", "kb ingest", "kb ingest --report", "kb module add",
                "kb module paths", "kb check", "kb context", "kb trace", "kb status", "kb baseline freeze",
                "kb agents install", "kb hook"):
        assert f"`{cmd}" in readme, cmd
