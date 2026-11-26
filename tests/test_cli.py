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
                "kb agents install", "kb hook", "kb todo", "kb clarify"):
        assert f"`{cmd}" in readme, cmd
    # kb redraft takes --base with --changed (a committed sync) and with --report
    assert "`kb redraft [IDs] [--flagged] [--changed [--base REF]] [--new]`" in readme
    assert "`kb redraft --report [--strict] [--base REF]`" in readme
    upgrade = readme.split("## Upgrading to 0.3.1\n", 1)[1].split("\n## ", 1)[0]  # team approvals now fail closed
    assert "teams.<name>.members" in upgrade and "read:org" in upgrade and "unreadable_teams: defer" in upgrade
    kb_readme = (Path(__file__).parents[1] / "src/kb/init/templates/kb_readme.md").read_text()
    assert "2. Before committing, run the `kb redraft` it suggests" in kb_readme
    assert "run `kb redraft --changed --base <commit before it>`" in kb_readme


def test_readme_covers_the_evaluation_and_the_040_upgrade():
    from pathlib import Path

    from kb import __version__

    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text()
    assert "## Evaluate the extraction\n" in readme
    for cmd in ("kb eval extract", "kb eval judge", "kb eval report", "kb eval decide", "kb eval status",
                "kb eval sample", "kb graph"):
        assert f"`{cmd}" in readme, cmd
    upgrade = readme.split("## Upgrading to 0.4.0\n", 1)[1].split("\n## ", 1)[0]
    for text in ("cuts a word or number", "triage.none_uncoded", "`modality`", "`actor`", "`category`", "`origin`",
                 "/kb/exclusions/", "/kb/sources/*/triage.yaml", "/kb/eval/", "/kb/baseline/", "/.github/workflows/", ".github/workflows/kb-claims.yml",
                 "kb-policy", "teams.<name>.members", "unreadable_teams: defer"):
        assert text in upgrade, text
    assert f"grounded-kb=={__version__}" in readme and f"grounded-kb@v{__version__}" in readme
    kb_readme = (root / "src/kb/init/templates/kb_readme.md").read_text()
    assert "## Evaluating the extraction\n" in kb_readme and "eval.decisions_invalid" in kb_readme
    for cmd in ("kb eval extract", "kb eval judge", "kb eval report --write", "kb eval decide", "kb eval status",
                "kb eval sample"):
        assert f"`{cmd}" in kb_readme, cmd


def test_upgrade_notes_say_what_breaks_and_in_which_order():
    """0.4.0 upgrade notes (final review): live records whose quotes cut a word or number fail with quote.not_found
    (errors, not warnings), so they are redrafted before upgrading; the requirement fields are added before the first
    evaluation; `kb agents install` refreshes kb/README.md and reminds about the CODEOWNERS block."""
    import re
    from pathlib import Path

    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text()
    intro, rest = readme.split("## Upgrading to 0.4.0\n", 1)[1].split("\n## ", 1)[0].split("\n- ", 1)
    intro = " ".join(intro.split())
    assert "with warnings" not in intro or "quote.not_found" in intro
    assert "`quote.not_found`" in intro and "error" in intro and "kb redraft" in intro and "before upgrading" in intro
    upgrade = " ".join(rest.split())
    assert re.search(r"requirement fields.*before the first evaluation", upgrade)
    assert "kb agents install" in upgrade and "kb/README.md" in upgrade and "CODEOWNERS" in upgrade
    kb_readme = " ".join((root / "src/kb/init/templates/kb_readme.md").read_text().split())
    assert "non-space characters is rejected" not in kb_readme
    assert re.search(r"shorter than 12 non-space characters .*`quote.too_short`.*warning.*error.*drafted", kb_readme)
