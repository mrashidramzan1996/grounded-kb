"""M6: a marker quoted inside a product document is document text, not code evidence."""

import json

import pytest

from helpers import ctx, invoke, write
from kb.check import run_check
from kb.config.load import load_config
from kb.modules import suggest_paths
from kb.records.repo import KbRepo
from kb.status import requirement_status

DOC = "# Notes\n\nEngineers tag code with r[impl REQ-F-001] and tests with r[verify REQ-F-001]; r[impl REQ-F-999] too.\n"


@pytest.fixture
def repo(valid_git_repo):
    write(valid_git_repo, "docs/howto.md", DOC)
    return valid_git_repo


def test_markers_in_source_documents_are_not_unknown_ids(repo):
    assert "marker.unknown_id" not in {i.code for i in run_check(repo).issues}


def test_markers_in_source_documents_do_not_build_or_test(repo):
    with_doc = requirement_status(repo, load_config(repo / "kb.config.yaml"), KbRepo.load(repo))
    (repo / "docs/howto.md").unlink()
    assert requirement_status(repo, load_config(repo / "kb.config.yaml"), KbRepo.load(repo)) == with_doc


def test_markers_in_source_documents_suggest_no_paths(repo):
    assert not any("docs" in g for globs in suggest_paths(*ctx(repo)).values() for g in globs)


def test_context_for_a_source_document_does_not_pick_up_its_markers(repo):
    out = json.loads(invoke(repo, "context", "--paths", "docs/howto.md", "--format", "json").stdout)
    assert "REQ-F-001" not in json.dumps(out)
