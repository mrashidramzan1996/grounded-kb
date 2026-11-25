import json

from helpers import quiet, break_ref, edit, git, invoke, make_git_repo, set_mode, write, GitCtx
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
    assert "1 errors, 3 warnings (mode: warn-only)" in r.stderr


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


def test_valid_repo_has_no_warnings(valid_git_repo):
    assert quiet(run_check(valid_git_repo).issues) == []  # the fixture quotes carry no rule word: implied, unconfirmed


def test_changed_widens_when_kb_changes(valid_git_repo):
    write(valid_git_repo, "services/billing/seats.py", "# r[impl REQ-F-002]\ndef f(): ...\n")
    git(valid_git_repo, "add", "-A")
    git(valid_git_repo, "commit", "-m", "marker")
    (valid_git_repo / "kb/requirements/REQ-F-002.yaml").unlink()
    codes_ = {i.code for i in run_check(valid_git_repo, changed_only=True).issues}
    assert "marker.unknown_id" in codes_


def test_changed_widens_when_config_changes(valid_git_repo):
    write(valid_git_repo, "services/billing/seats.py", "def f(): ...\n")
    git(valid_git_repo, "add", "-A")
    git(valid_git_repo, "commit", "-m", "code")
    cfg = valid_git_repo / "kb.config.yaml"
    cfg.write_text(cfg.read_text().replace("services/billing/**", "services/other/**"))
    issues = run_check(valid_git_repo, changed_only=True).issues
    assert any(i.code == "config.file_unassigned" and i.path == "services/billing/seats.py" for i in issues)


def test_changed_ignores_deleted_files(valid_git_repo):
    write(valid_git_repo, "other/gone.py", "def g(): ...\n")
    git(valid_git_repo, "add", "-A")
    git(valid_git_repo, "commit", "-m", "gone")
    (valid_git_repo / "other/gone.py").unlink()
    assert not [i for i in run_check(valid_git_repo, changed_only=True, base="HEAD").issues if i.path == "other/gone.py"]


def test_decision_guard_uses_merge_base(valid_git_repo):
    git(valid_git_repo, "checkout", "-q", "-b", "feature")
    git(valid_git_repo, "checkout", "-q", "main")
    (valid_git_repo / "kb/decisions/ADR-0009.md").write_text(
        "---\nid: ADR-0009\ntitle: New\ngoverns: [MOD-sessions]\n---\nBody.\n"
    )
    git(valid_git_repo, "add", "-A")
    git(valid_git_repo, "commit", "-m", "adr on main")
    git(valid_git_repo, "checkout", "-q", "feature")
    assert "decision_guard" not in {i.code for i in run_check(valid_git_repo, base="main").issues}
