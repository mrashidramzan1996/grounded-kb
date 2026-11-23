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


def text_and_sha(root):
    """Security review: an invented text.md plus a forged original_sha256, the original untouched."""
    invented_quote(root)
    replace(root, f"{STORE}/source.yaml", "original_sha256: ", "original_sha256: 00")


TAMPERS = {
    "invented_quote": invented_quote, "zero_secrets": zero_secrets, "rewritten_sha": rewritten_sha,
    "text_and_sha": text_and_sha,
}


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


def test_original_edited_without_sync_is_outdated_against_a_base(repo):
    """Outdated only when the original moved on since the base and the store did not; with no base nothing
    says which side changed, so the mismatch is tampering (head's own source.yaml is never believed)."""
    replace(repo, "docs/n.md", "5 EUR", "6 EUR")
    for args in (("--changed",), ("--base", "main")):
        found = errors(repo, *args)
        assert ("source.outdated", "SRC-n") in found and ("source.tampered", "SRC-n") not in found, args
    assert ("source.tampered", "SRC-n") in errors(repo)


def test_tampered_store_with_deleted_original_is_tampered(repo):
    invented_quote(repo)
    (repo / "docs/n.md").unlink()
    for args in ((), ("--changed",)):
        assert ("source.tampered", "SRC-n") in errors(repo, *args), args


def test_deleted_original_with_untouched_store_is_outdated(repo):
    (repo / "docs/n.md").unlink()
    found = errors(repo, "--changed")
    assert ("source.outdated", "SRC-n") in found and ("source.tampered", "SRC-n") not in found


def test_report_does_not_accept_records_citing_a_tampered_source_whose_original_is_gone(repo):
    from kb.ingest.report import ingest_report

    invented_quote(repo)
    (repo / "docs/n.md").unlink()
    r = ingest_report(repo, "HEAD")
    assert ("SRC-n", "source.tampered") in {(i.record, i.code) for i in r.sources}
    assert "REQ-F-001" not in r.accepted and "REQ-F-001" in {i.record for i in r.rejected}


def test_document_edit_committed_with_its_sync_is_not_tampering(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    replace(repo, "docs/n.md", "5 EUR", "6 EUR")
    assert invoke(repo, "source", "sync").exit_code == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "doc update with sync")
    for args in ((), ("--changed",), ("--base", "main")):
        assert ("source.tampered", "SRC-n") not in errors(repo, *args), args


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
    assert ("source.outdated", "SRC-n") in errors(repo, "--changed")
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
