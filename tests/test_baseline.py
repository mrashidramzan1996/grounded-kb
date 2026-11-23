import hashlib
import os

import pytest

from helpers import apply_drafts, git, invoke
from kb.baseline import BaselineError, freeze_baseline
from kb.records.repo import KbRepo
from kb.yamlio import load_yaml_strict


@pytest.fixture
def committed(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    git(acme_repo_ingested, "add", "-A")
    git(acme_repo_ingested, "commit", "-m", "drafts")
    return acme_repo_ingested


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
