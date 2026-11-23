import json

from helpers import break_ref, edit, git, invoke, make_git_repo, set_mode, write, GitCtx
from kb.check import DEFAULT_GATES, run_check
from kb.gates.anchors import anchor_gate
from kb.gates.ids import id_gate
from kb.gates.quotes import quote_gate
from kb.gates.sources import source_gate
from kb.gates.triage import triage_gate
from kb.modules import module_paths_gate


def test_default_gates_always_include_every_gate():
    for g in (id_gate, quote_gate, anchor_gate, source_gate, module_paths_gate, triage_gate):
        assert g in DEFAULT_GATES


def test_valid_repo_passes_strict(valid_git_repo):
    r = invoke(valid_git_repo, "check", "--strict")
    assert r.exit_code == 0 and "0 errors" in r.stderr


def test_warn_only_passes_strict_fails(valid_git_repo):
    break_ref(valid_git_repo)
    assert invoke(valid_git_repo, "check").exit_code == 0
    r = invoke(valid_git_repo, "check", "--strict", "--format", "json")
    assert r.exit_code == 1 and json.loads(r.stdout)["issues"][0]["code"] == "ref.missing"


def test_enforce_fails_without_strict(valid_git_repo):
    set_mode(valid_git_repo, "enforce")
    break_ref(valid_git_repo)
    assert invoke(valid_git_repo, "check").exit_code == 1


def test_missing_config_exits_2(tmp_path):
    r = invoke(make_git_repo(None, tmp_path), "check")
    assert r.exit_code == 2 and "kb init" in r.stderr


def test_base_detects_edited_decision(valid_git_repo):
    edit(GitCtx(valid_git_repo, None), "kb/decisions/ADR-0002.md", "x")
    assert "decision_guard" in invoke(valid_git_repo, "check", "--base", "HEAD", "--format", "json").stdout


def test_bad_base_exits_2_without_traceback(valid_git_repo):
    r = invoke(valid_git_repo, "check", "--base", "no-such-ref")
    assert r.exit_code == 2 and r.stderr.strip()
    assert "Traceback" not in r.output


def test_json_shape_and_text_format(valid_git_repo):
    break_ref(valid_git_repo)
    data = json.loads(invoke(valid_git_repo, "check", "--format", "json").stdout)
    assert set(data) == {"mode", "issues", "summary"} and data["mode"] == "warn-only"
    assert data["summary"]["errors"] == 1
    assert set(data["issues"][0]) == {"code", "severity", "path", "record", "message"}
    r = invoke(valid_git_repo, "check")
    line = r.stdout.splitlines()[0]
    assert line.startswith("error ref.missing kb/requirements/REQ-F-001.yaml REQ-F-001: ")
    assert "1 errors, 2 warnings (mode: warn-only)" in r.stderr


def test_unknown_format_exits_2(valid_git_repo):
    assert invoke(valid_git_repo, "check", "--format", "xml").exit_code == 2


def test_untriaged_segment_warns(valid_git_repo):
    path = valid_git_repo / "kb/sources/SRC-pricing-spec-v3/triage.yaml"
    path.write_text("\n".join(l for l in path.read_text().splitlines() if "seg-01" not in l) + "\n")
    report = run_check(valid_git_repo)
    (issue,) = [i for i in report.issues if i.code == "segment.untriaged"]
    assert issue.severity == "warning" and "SRC-pricing-spec-v3#seg-01" in issue.message


def test_changed_limits_file_level_checks(valid_git_repo):
    write(valid_git_repo, "other/b.py", "def b(): ...\n")
    git(valid_git_repo, "add", "-A")
    git(valid_git_repo, "commit", "-m", "b")
    write(valid_git_repo, "other/c.py", "def c(): ...\n")

    def unassigned(**kw):
        return {i.path for i in run_check(valid_git_repo, **kw).issues if i.code == "config.file_unassigned"}

    assert unassigned() >= {"other/b.py", "other/c.py"}
    assert unassigned(changed_only=True, base="HEAD") == {"other/c.py"}
    assert unassigned(changed_only=True) == {"other/c.py"}  # base defaults to the default branch (main == HEAD here)
