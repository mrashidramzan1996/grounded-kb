"""I4: `kb ingest docs/` on an ordinary folder: no-reader files skipped, colliding names, nothing partial."""

import json

import pytest

from helpers import BRIEF, brief_of, git, invoke, make_git_repo

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\n'
PNG = "\x89PNG\r\n\x1a\nnot really an image\n"


@pytest.fixture
def folder(tmp_path):
    return make_git_repo(None, tmp_path, extra={
        "kb.config.yaml": CONFIG,
        "docs/logo.png": PNG,
        "docs/api/README.md": "# API\n\nThe API returns JSON.\n",
        "docs/web/README.md": "# Web\n\nThe web app is a single page.\n",
    })


def sources(root):
    return sorted(p.name for p in (root / "kb/sources").iterdir()) if (root / "kb/sources").exists() else []


@pytest.mark.parametrize("args", [("docs/",), ()])
def test_ingest_folder_skips_files_without_reader_and_names_colliding_sources_by_path(folder, args):
    r = invoke(folder, "ingest", *args)
    assert r.exit_code == 0, r.output
    assert "skipped (no reader): docs/logo.png" in r.stderr
    assert sources(folder) == ["SRC-docs-api-readme", "SRC-docs-web-readme"]
    brief = brief_of(folder)
    assert "### SRC-docs-api-readme#seg-01 " in brief and "### SRC-docs-web-readme#seg-01 " in brief


def test_explicitly_named_file_without_reader_still_fails(folder):
    r = invoke(folder, "ingest", "docs/logo.png")
    assert r.exit_code == 2 and "no reader" in r.stderr and sources(folder) == []


def test_collision_with_an_existing_source_keeps_its_id(tmp_path):
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/README.md": "# Top\n\nTop level.\n"})
    assert invoke(root, "ingest").exit_code == 0
    assert sources(root) == ["SRC-readme"]
    (root / "docs/api").mkdir()
    (root / "docs/api/README.md").write_text("# API\n\nJSON.\n")
    assert invoke(root, "ingest", "docs/").exit_code == 0
    assert sources(root) == ["SRC-docs-api-readme", "SRC-readme"]
    assert invoke(root, "source", "sync").exit_code == 0 and sources(root) == ["SRC-docs-api-readme", "SRC-readme"]
    r = invoke(root, "check", "--format", "json")
    assert not [i for i in json.loads(r.stdout)["issues"] if i["code"].startswith("source.")]


def test_nothing_is_written_when_one_candidate_fails(folder):
    (folder / "docs/broken.pdf").write_bytes(b"%PDF-1.4 truncated")
    r = invoke(folder, "ingest", "docs/")
    assert r.exit_code == 2 and "broken.pdf" in r.stderr
    assert sources(folder) == [] and not (folder / BRIEF).exists()


def test_source_add_validates_every_path_before_writing(folder):
    (folder / "docs/broken.pdf").write_bytes(b"%PDF-1.4 truncated")
    r = invoke(folder, "source", "add", "docs/api/README.md", "docs/broken.pdf")
    assert r.exit_code == 2 and sources(folder) == []


def test_empty_document_warns(tmp_path):
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/blank.md": "\n", "docs/n.md": "# N\n\nText.\n"})
    r = invoke(root, "ingest")
    assert r.exit_code == 0, r.output
    assert "warning source.empty" in r.stderr and "docs/blank.md" in r.stderr
    git(root, "add", "-A")
    issues = json.loads(invoke(root, "check", "--format", "json").stdout)["issues"]
    assert [(i["code"], i["severity"], i["record"]) for i in issues if i["code"] == "source.empty"] == [
        ("source.empty", "warning", "SRC-blank")
    ]
