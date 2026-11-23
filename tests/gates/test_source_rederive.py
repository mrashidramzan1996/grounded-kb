"""C1: `kb check` re-derives the source store from the originals; nothing agent-editable under kb/sources is trusted."""

import hashlib
import json

import pytest

from helpers import git, invoke, make_git_repo

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - {id: MOD-billing, owner: "@acme/billing"}\n'
DOC = "# Seats\n\nA seat costs 5 EUR.\n\n# Keys\n\nThe staging key is AKIAIOSFODNN7EXAMPLE.\n"
STORE = "kb/sources/SRC-n"


@pytest.fixture
def repo(tmp_path):
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/n.md": DOC})
    assert invoke(root, "ingest").exit_code == 0
    git(root, "add", "-A")
    git(root, "commit", "-m", "sources")
    return root


def replace(root, rel, old, new):
    path = root / rel
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def invented_quote(root):
    """Same length, so the stored segment offsets still fit; then a requirement quoting the invented text."""
    replace(root, f"{STORE}/text.md", "5 EUR", "9 EUR")
    (root / "kb/requirements").mkdir(parents=True, exist_ok=True)
    (root / "kb/requirements/REQ-F-001.yaml").write_text(
        "id: REQ-F-001\nstatement: A seat costs 9 EUR.\nmodules: [MOD-billing]\n"
        "evidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: A seat costs 9 EUR.\n"
    )


def zero_secrets(root):
    replace(root, f"{STORE}/source.yaml", "secrets: 1", "secrets: 0")


def rewritten_sha(root):
    """Edit the original, then make source.yaml claim the stored text is the edited document's."""
    replace(root, "docs/n.md", "5 EUR", "7 EUR")
    sha = hashlib.sha256((root / "docs/n.md").read_bytes()).hexdigest()
    path = root / f"{STORE}/source.yaml"
    lines = [f"original_sha256: {sha}" if l.startswith("original_sha256:") else l for l in path.read_text().splitlines()]
    path.write_text("\n".join(lines) + "\n")


TAMPERS = {"invented_quote": invented_quote, "zero_secrets": zero_secrets, "rewritten_sha": rewritten_sha}


def errors(root, *args):
    r = invoke(root, "check", "--format", "json", *args)
    assert r.exit_code in (0, 1), r.output
    return {(i["code"], i["record"]) for i in json.loads(r.stdout)["issues"] if i["severity"] == "error"}


def test_untampered_store_passes(repo):
    assert errors(repo) == {("source.secret_in_original", "SRC-n")}


@pytest.mark.parametrize("tamper", TAMPERS)
def test_tampering_is_caught_uncommitted(repo, tamper):
    TAMPERS[tamper](repo)
    found = errors(repo)
    assert ("source.tampered", "SRC-n") in found
    assert ("source.secret_in_original", "SRC-n") in found  # from the original, never from the stored count
    assert ("source.tampered", "SRC-n") in errors(repo, "--changed")


@pytest.mark.parametrize("tamper", TAMPERS)
def test_tampering_is_caught_after_a_commit(repo, tamper):
    git(repo, "checkout", "-q", "-b", "feature")
    TAMPERS[tamper](repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "tamper")
    for args in ((), ("--base", "main"), ("--changed",), ("--changed", "--base", "main")):
        found = errors(repo, *args)
        assert ("source.tampered", "SRC-n") in found, args
        assert ("source.secret_in_original", "SRC-n") in found, args


def test_original_edited_without_sync_is_outdated_not_tampered(repo):
    replace(repo, "docs/n.md", "5 EUR", "6 EUR")
    found = errors(repo)
    assert ("source.outdated", "SRC-n") in found and ("source.tampered", "SRC-n") not in found


def test_original_edited_and_synced_on_a_branch_passes(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    replace(repo, "docs/n.md", "5 EUR", "6 EUR")
    assert invoke(repo, "source", "sync").exit_code == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "doc update")
    for args in ((), ("--base", "main")):
        assert errors(repo, *args) == {("source.secret_in_original", "SRC-n")}, args


def test_secret_removed_from_the_original_clears_the_error_only_after_sync(repo):
    replace(repo, "docs/n.md", "The staging key is AKIAIOSFODNN7EXAMPLE.", "No key here.")
    assert ("source.secret_in_original", "SRC-n") not in errors(repo)
    assert ("source.outdated", "SRC-n") in errors(repo)
    assert invoke(repo, "source", "sync").exit_code == 0
    assert errors(repo) == set()


def test_tampered_ingest_base_file_does_not_hide_tampering_from_check(repo):
    """kb check never reads .kb/: a forged report base changes nothing."""
    git(repo, "checkout", "-q", "-b", "feature")
    invented_quote(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "tamper")
    base = repo / ".kb/cache/ingest/base"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text(git(repo, "rev-parse", "HEAD"))
    assert ("source.tampered", "SRC-n") in errors(repo, "--changed")
