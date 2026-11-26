import hashlib
import os

import pytest

from helpers import apply_drafts, clean_drafted_acme, evaluated, git, invoke
from kb.gitfiles import GitError
from kb.baseline import BaselineError, freeze_baseline
from kb.records.repo import KbRepo
from kb.yamlio import load_yaml_strict


@pytest.fixture
def unevaluated(acme_repo_ingested, fixtures):
    """The drafted acme repo with what kb check reports resolved, committed on main; never evaluated."""
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    clean_drafted_acme(acme_repo_ingested)
    git(acme_repo_ingested, "add", "-A")
    git(acme_repo_ingested, "commit", "-m", "drafts")
    return acme_repo_ingested


@pytest.fixture
def committed(unevaluated):
    """`unevaluated` with a settled evaluation of it committed: what `kb baseline freeze` needs."""
    evaluated(unevaluated)
    return unevaluated


def test_freeze_baseline(committed):
    p = freeze_baseline(committed, "v1")
    assert p == committed / "kb/baseline/v1.yaml"
    assert "REQ-F-002" in p.read_text()
    assert git(committed, "tag", "-l").strip() == "kb-baseline-v1"
    data = load_yaml_strict(p.read_text())
    assert data["version"] == "v1" and data["commit"] == git(committed, "rev-parse", "HEAD").strip()
    ids = [r["id"] for r in data["requirements"]]
    assert ids == sorted(ids) and "REQ-F-002" in ids
    want = hashlib.sha256((committed / "kb/requirements/REQ-F-002.yaml").read_bytes()).hexdigest()
    assert {r["id"]: r["hash"] for r in data["requirements"]}["REQ-F-002"] == want
    assert data["frozen_at"].endswith("Z")
    with pytest.raises(BaselineError):
        freeze_baseline(committed, "v1")


def test_frozen_at_is_deterministic_commit_date(committed):
    env = {"GIT_COMMITTER_DATE": "2026-01-02T03:04:05+02:00"}
    os.environ.update(env)
    try:
        git(committed, "commit", "--allow-empty", "-m", "dated")
    finally:
        del os.environ["GIT_COMMITTER_DATE"]
    p = freeze_baseline(committed, "v1")
    assert load_yaml_strict(p.read_text())["frozen_at"] == "2026-01-02T01:04:05Z"


@pytest.mark.parametrize("bad", ["1", "v", "v1.0", "V1", "v1/../x", "v-1"])
def test_bad_version(committed, bad):
    with pytest.raises(BaselineError):
        freeze_baseline(committed, bad)


def test_refuses_dirty_kb(committed):
    (committed / "kb/requirements/REQ-F-002.yaml").write_text("x: 1\n")
    with pytest.raises(BaselineError, match="uncommitted"):
        freeze_baseline(committed, "v1")


def test_refuses_existing_tag_and_file(committed):
    git(committed, "tag", "kb-baseline-v2")
    with pytest.raises(BaselineError):
        freeze_baseline(committed, "v2")
    (committed / "kb/baseline").mkdir()
    (committed / "kb/baseline/v3.yaml").write_text("x: 1\n")
    git(committed, "add", "-A")
    git(committed, "commit", "-m", "b")
    with pytest.raises(BaselineError):
        freeze_baseline(committed, "v3")
    assert (committed / "kb/baseline/v3.yaml").read_text() == "x: 1\n"


def test_refuses_symlinked_baseline_dir(committed, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (committed / "kb/baseline").symlink_to(outside)
    git(committed, "add", "-A")
    git(committed, "commit", "-m", "link")
    with pytest.raises(BaselineError):
        freeze_baseline(committed, "v1")
    assert not list(outside.iterdir())


def test_baseline_file_is_not_an_unknown_kb_file(committed):
    freeze_baseline(committed, "v1")
    assert not [e for e in KbRepo.load(committed).errors if e.code == "schema.unknown_file"]


def test_cli_freeze(committed):
    out = invoke(committed, "baseline", "freeze", "v1")
    assert out.exit_code == 0, out.output
    assert "wrote kb/baseline/v1.yaml; tagged HEAD as kb-baseline-v1; commit the baseline file" in out.stdout
    again = invoke(committed, "baseline", "freeze", "v1")
    assert again.exit_code == 2


def _break_requirement(repo):
    (repo / "kb/requirements/REQ-F-002.yaml").write_text("id: REQ-F-002\nbogus: [\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "broken")


def test_freeze_refuses_unloadable_records(committed):
    _break_requirement(committed)
    with pytest.raises(BaselineError, match="REQ-F-002"):
        freeze_baseline(committed, "v1")
    assert not (committed / "kb/baseline").exists() and git(committed, "tag", "-l") == ""


def test_freeze_refuses_duplicate_ids(committed):
    src = (committed / "kb/requirements/REQ-F-002.yaml").read_text()
    (committed / "kb/requirements/REQ-F-999.yaml").write_text(src)
    git(committed, "add", "-A")
    git(committed, "commit", "-m", "dup")
    with pytest.raises(BaselineError, match="REQ-F-002"):
        freeze_baseline(committed, "v1")


def test_status_cli_reports_load_failures(committed):
    _break_requirement(committed)
    out = invoke(committed, "status")
    assert out.exit_code == 1
    assert "1 KB files failed to load (run `kb check`): kb/requirements/REQ-F-002.yaml" in out.stderr
    assert "total:" in out.stdout


def test_tag_failure_leaves_nothing(committed, monkeypatch):
    import kb.baseline as b

    def boom(*a):
        raise GitError("tag failed")

    monkeypatch.setattr(b, "create_tag", boom)
    with pytest.raises(GitError):
        freeze_baseline(committed, "v1")
    assert not (committed / "kb/baseline").exists() and git(committed, "tag", "-l") == ""


def test_write_failure_leaves_no_tag_or_partial(committed, monkeypatch):
    import kb.baseline as b

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(b.os, "link", boom)
    with pytest.raises(OSError):
        freeze_baseline(committed, "v1")
    assert not (committed / "kb/baseline").exists() and git(committed, "tag", "-l") == ""
    monkeypatch.undo()
    freeze_baseline(committed, "v1")


def test_baseline_path_is_a_file(committed):
    (committed / "kb/baseline").write_text("x")
    git(committed, "add", "-A")
    git(committed, "commit", "-m", "f")
    with pytest.raises(BaselineError):
        freeze_baseline(committed, "v1")


# --- I2: a baseline is frozen only from a clean default branch ----------------------------------

def test_freeze_refuses_gate_errors(committed):
    (committed / "kb/requirements/REQ-F-009.yaml").write_text(
        "id: REQ-F-009\nstatement: Invented.\nmodules: [MOD-billing]\n"
        "evidence:\n  quotes:\n  - source: SRC-pricing-spec-v3#seg-10\n    text: Nothing like this is in the spec.\n"
    )
    git(committed, "add", "-A")
    git(committed, "commit", "-m", "invented")
    with pytest.raises(BaselineError, match="quote.not_found"):
        freeze_baseline(committed, "v1")
    assert not (committed / "kb/baseline").exists() and git(committed, "tag", "-l") == ""
    out = invoke(committed, "baseline", "freeze", "v1")
    assert out.exit_code == 2 and "quote.not_found" in out.stderr and "Traceback" not in out.output


def test_freeze_refuses_off_the_default_branch_unless_allowed(committed):
    git(committed, "checkout", "-q", "-b", "feature")
    git(committed, "commit", "--allow-empty", "-m", "unmerged")
    with pytest.raises(BaselineError, match="default branch"):
        freeze_baseline(committed, "v1")
    out = invoke(committed, "baseline", "freeze", "v1")
    assert out.exit_code == 2 and "--allow-branch" in out.stderr
    assert invoke(committed, "baseline", "freeze", "v1", "--allow-branch").exit_code == 0


# --- the evaluation gates the freeze -------------------------------------------------------------------------------

def _summary_with(root, *, open_findings=(), dismissed=()):
    """A current summary of the KB as it is, with these open findings and dismissed items, committed."""
    import datetime

    from helpers import head, put
    from kb.eval.findings import fingerprint, summary, write_summary
    from kb.gates.coverage import coverage_stats
    from kb.yamlio import dump_yaml

    repo = KbRepo.load(root)
    agr = {"matched": 0}
    data = summary(head(root), fingerprint(repo), "0" * 32, coverage_stats(repo), agr, list(open_findings),
                   datetime.date.today().isoformat(), list(dismissed))
    write_summary(root, data)
    put(root, "kb/eval/decisions.yaml", dump_yaml({"decisions": [{
        "finding": "categories", "canonical": "categories", "sentences": [], "decision": "not_a_rule",
        "record": None, "record_sha256": None, "reason": "None apply.", "by": "@alice", "date": "2026-11-26"}]}))
    git(root, "add", "-A")
    git(root, "commit", "-m", "evaluated")


def test_freeze_refuses_without_an_evaluation(unevaluated):
    committed = unevaluated
    with pytest.raises(BaselineError, match="kb eval extract") as e:
        freeze_baseline(committed, "v1")
    assert "--unevaluated" in str(e.value)
    assert not (committed / "kb/baseline").exists() and git(committed, "tag", "-l") == ""
    r = invoke(committed, "baseline", "freeze", "v1")
    assert r.exit_code == 1 and "never evaluated" in r.stderr
    # --unevaluated overrides, with a warning, and the baseline says so
    r = invoke(committed, "baseline", "freeze", "v1", "--unevaluated")
    assert r.exit_code == 0, r.output
    assert "warning" in r.stderr and "not evaluated" in r.stderr
    assert load_yaml_strict((committed / "kb/baseline/v1.yaml").read_text())["eval"] is None


def test_freeze_records_a_settled_evaluation(committed):
    from helpers import head

    _summary_with(committed)
    p = freeze_baseline(committed, "v1")
    data = load_yaml_strict(p.read_text())
    summary = load_yaml_strict((committed / "kb/eval/summary.yaml").read_text())
    assert list(data) == ["version", "commit", "frozen_at", "requirements", "eval"]
    assert data["eval"] == {"commit": summary["commit"], "fingerprint": summary["fingerprint"], "open": 0, "decided": 0,
                            "dismissed": 0}
    assert summary["commit"] != head(committed)  # the evaluated commit, not HEAD


def test_freeze_refuses_open_findings_dismissals_and_a_stale_evaluation(committed):
    from kb.eval.findings import Dismissed, Finding

    finding = Finding("F-0123456789", "R:REQ-F-002", "unfaithful", "SRC-x#seg-01", "Some text.", "high", 1, (), "x")
    _summary_with(committed, open_findings=[finding])
    with pytest.raises(BaselineError, match="1 open finding"):
        freeze_baseline(committed, "v1")
    assert invoke(committed, "baseline", "freeze", "v1").exit_code == 1
    dismissed = Dismissed("F-1111111111", "S:000000000000", "omission", "SRC-x#seg-01", "Some text.", "not_a_rule", (), "")
    _summary_with(committed, dismissed=[dismissed])
    with pytest.raises(BaselineError, match="dismissed"):
        freeze_baseline(committed, "v1")
    # a person agreeing with the dismissals settles it; the baseline counts them
    assert invoke(committed, "eval", "decide", "dismissed", "--as", "ok", "--by", "@alice").exit_code == 0
    git(committed, "add", "-A")
    git(committed, "commit", "-m", "agreed")
    assert invoke(committed, "baseline", "freeze", "v1").exit_code == 0
    assert load_yaml_strict((committed / "kb/baseline/v1.yaml").read_text())["eval"]["dismissed"] == 1
    # a record changed since the evaluation: stale
    git(committed, "tag", "-d", "kb-baseline-v1")
    (committed / "kb/baseline/v1.yaml").unlink()
    p = committed / "kb/requirements/REQ-F-002.yaml"
    p.write_text(p.read_text().replace("statement: ", "statement: Changed. ", 1))
    git(committed, "add", "-A")
    git(committed, "commit", "-m", "edited")
    with pytest.raises(BaselineError, match="stale"):
        freeze_baseline(committed, "v1")
    assert freeze_baseline(committed, "v1", unevaluated=True).is_file()


def test_baseline_changes_need_intent_approvers(tmp_path):
    from helpers import make_git_repo
    from kb.config.schema import KbConfig
    from kb.init.scaffold import init_repo
    from kb.verify.approvals import no_approver_warnings, required_owners
    from kb.verify.changes import ChangeSet

    cfg = KbConfig.model_validate({"version": 1, "modules": [],
                                   "policy": {"approvers": {"intent": ["@pm"], "decisions": ["@arch"]}}})
    path = "kb/baseline/v1.yaml"
    head, base = KbRepo.from_files({path: b"version: v1\n"}), KbRepo.from_files({})
    changes = ChangeSet("main", (path,), {}, {}, {}, frozenset())
    assert required_owners(tmp_path, cfg, head, base, changes, []) == {"@pm": ["baseline changed"]}
    assert required_owners(tmp_path, cfg, head, KbRepo.from_files({path: b"version: v1\n"}),
                           ChangeSet("main", (), {}, {}, {}, frozenset()), []) == {}
    empty = KbConfig.model_validate({"version": 1, "modules": [], "policy": {"approvers": {"intent": [], "decisions": ["@a"]}}})
    (w,) = no_approver_warnings(empty, head, base, changes, [])
    assert "baselines" in w.message
    (tmp_path / "init").mkdir()
    repo = make_git_repo(None, tmp_path / "init")
    init_repo(repo)
    assert "# /kb/baseline/ TODO" in (repo / "CODEOWNERS").read_text()
