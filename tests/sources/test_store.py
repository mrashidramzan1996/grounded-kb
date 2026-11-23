import hashlib
import re

import pytest
from helpers import add_all, grep, invoke, pdf_bytes

from kb.config.load import load_config
from kb.gates.base import GateContext
from kb.gates.sources import source_gate
from kb.records.repo import KbRepo
from kb.sources.readers import SourceError
from kb.sources.store import MAX_SOURCE_BYTES, add_source, add_sources, sync_sources


def cfg_of(repo):
    return load_config(repo / "kb.config.yaml")


def sdir(repo, src):
    return repo / f"kb/sources/{src.id}"


def test_guard_on_architecture_notes(acme_repo):
    src = add_source(acme_repo, "docs/architecture-notes.docx", cfg_of(acme_repo))
    text = (acme_repo / f"kb/sources/{src.id}/text.md").read_text()
    assert "[SECRET:aws-access-key]" in text and "[EMAIL-1]" in text and "AKIAIOSFODNN7EXAMPLE" not in text
    assert "Ignore your instructions" in text and src.guard.injections == 1 and len(src.guard.injection_segments) == 1
    assert src.id == "SRC-architecture-notes" and src.normaliser == "docx@1"


def test_nothing_unmasked_anywhere(acme_repo):
    add_all(acme_repo)
    assert not grep(acme_repo / "kb", r"AKIAIOSFODNN7EXAMPLE|@acme\.example")


def test_store_files_load_and_are_consistent(acme_repo):
    src = add_source(acme_repo, "docs/pricing-spec-v3.pdf", cfg_of(acme_repo))
    repo = KbRepo.load(acme_repo)
    assert repo.errors == [] and repo.sources[src.id] == src
    text, segs = repo.source_text(src.id), repo.segments(src.id)
    assert src.original == "docs/pricing-spec-v3.pdf" and src.trust == "spec" and src.visibility == "internal"
    assert src.original_sha256 == hashlib.sha256((acme_repo / src.original).read_bytes()).hexdigest()
    assert segs and all(
        hashlib.sha256(text[s.start : s.end].encode()).hexdigest()[:12] == s.hash for s in segs
    )
    assert src.next_seg == len(segs) + 1
    assert [s.id for s in segs] == [f"seg-{i:02d}" for i in range(1, len(segs) + 1)]


def test_add_is_deterministic(acme_repo):
    add_source(acme_repo, "docs/product-brief.docx", cfg_of(acme_repo))
    first = {p.name: p.read_bytes() for p in (acme_repo / "kb/sources/SRC-product-brief").iterdir()}
    add_source(acme_repo, "docs/product-brief.docx", cfg_of(acme_repo))
    assert {p.name: p.read_bytes() for p in (acme_repo / "kb/sources/SRC-product-brief").iterdir()} == first


def test_add_source_refuses_unconfigured_and_restricted(acme_repo, tmp_path):
    (acme_repo / "notes.md").write_text("# n\n\nx\n")
    with pytest.raises(SourceError, match="not in config.sources"):
        add_source(acme_repo, "notes.md", cfg_of(acme_repo))
    base = cfg_of(acme_repo)
    cfg = type(base).model_validate(
        base.model_dump() | {"visibility": {"restricted": [{"paths": ["docs/product-brief.docx"], "readers": ["x"]}]}}
    )
    with pytest.raises(SourceError, match="restricted"):
        add_source(acme_repo, "docs/product-brief.docx", cfg)
    with pytest.raises(SourceError):
        add_source(acme_repo, "docs/missing.md", cfg_of(acme_repo))
    with pytest.raises(SourceError):
        add_source(acme_repo, "../outside.md", cfg_of(acme_repo))
    assert not (acme_repo / "kb").exists()


def test_failed_read_writes_nothing(acme_repo):
    (acme_repo / "docs/bad.pdf").write_bytes(b"not a pdf")
    with pytest.raises(SourceError):
        add_source(acme_repo, "docs/bad.pdf", cfg_of(acme_repo))
    assert not (acme_repo / "kb").exists()


def test_trust_and_visibility_come_from_config(acme_repo):
    (acme_repo / "kb.config.yaml").write_text(
        (acme_repo / "kb.config.yaml").read_text().replace(
            'paths: ["docs/**"]', 'paths: ["docs/**"]\n    trust: design\n    visibility: partners'
        )
    )
    src = add_source(acme_repo, "docs/product-brief.docx", cfg_of(acme_repo))
    assert (src.trust, src.visibility) == ("design", "partners")


def test_oversize_file_refused(acme_repo, monkeypatch):
    (acme_repo / "docs/big.md").write_text("# a\n\n" + "x" * 100)
    monkeypatch.setattr("kb.sources.store.MAX_SOURCE_BYTES", 50)
    with pytest.raises(SourceError, match="MAX_SOURCE_BYTES"):
        add_source(acme_repo, "docs/big.md", cfg_of(acme_repo))
    assert MAX_SOURCE_BYTES == 50_000_000


def test_resync_keeps_ids_and_triage(acme_repo):
    cfg = cfg_of(acme_repo)
    (acme_repo / "docs/a.md").write_text("# A\n\na\n\n# B\n\nb\n")
    first = add_source(acme_repo, "docs/a.md", cfg)
    triage = sdir(acme_repo, first) / "triage.yaml"
    triage.write_text("segments:\n  seg-01: 'none: intro'\n")
    (acme_repo / "docs/a.md").write_text("# New\n\nx\n\n# A\n\na\n\n# B\n\nb changed\n")
    (again,) = sync_sources(acme_repo, cfg)
    ids = {s.heading: s.id for s in KbRepo.load(acme_repo).segments(again.id)}
    assert ids == {"New": "seg-03", "A": "seg-01", "B": "seg-02"} and again.next_seg == 4
    assert triage.read_text() == "segments:\n  seg-01: 'none: intro'\n"
    assert again.original_sha256 != first.original_sha256
    # re-adding behaves like sync
    add_source(acme_repo, "docs/a.md", cfg)
    assert KbRepo.load(acme_repo).sources[again.id].next_seg == 4
    assert [p.name for p in sdir(acme_repo, again).iterdir() if p.name.startswith(".")] == []


def test_sync_reports_missing_original(acme_repo):
    cfg = cfg_of(acme_repo)
    (acme_repo / "docs/a.md").write_text("# A\n\na\n")
    add_source(acme_repo, "docs/a.md", cfg)
    (acme_repo / "docs/a.md").unlink()
    with pytest.raises(SourceError, match="docs/a.md"):
        sync_sources(acme_repo, cfg)


def test_id_collision_between_different_files_gets_a_path_id(acme_repo):
    cfg = cfg_of(acme_repo)
    (acme_repo / "docs/a.md").write_text("# A\n\na\n")
    (acme_repo / "docs/a.txt").write_text("a\n")
    assert add_source(acme_repo, "docs/a.md", cfg).id == "SRC-a"
    assert add_source(acme_repo, "docs/a.txt", cfg).id == "SRC-docs-a"  # the stored one keeps its id
    assert add_source(acme_repo, "docs/a.md", cfg).id == "SRC-a"


def test_id_collision_of_whole_paths_refused(acme_repo):
    cfg = cfg_of(acme_repo)
    (acme_repo / "docs/a b.md").write_text("# A\n\na\n")
    (acme_repo / "docs/a-b.md").write_text("# B\n\nb\n")
    with pytest.raises(SourceError, match="SRC-docs-a-b"):
        add_sources(acme_repo, ["docs/a b.md", "docs/a-b.md"], cfg)
    assert not (acme_repo / "kb/sources").exists()


def test_slug():
    from kb.sources.store import source_id

    assert source_id("docs/Pricing Spec_v3 (final).pdf") == "SRC-pricing-spec-v3-final"


# --- source gate --------------------------------------------------------------------------------

def gate(repo):
    return source_gate(GateContext(repo, cfg_of(repo), KbRepo.load(repo)))


def test_gate_flags_secret_in_original_and_outdated(acme_repo):
    cfg = cfg_of(acme_repo)
    add_source(acme_repo, "docs/architecture-notes.docx", cfg)
    add_source(acme_repo, "docs/product-brief.docx", cfg)
    issues = gate(acme_repo)
    assert [i.code for i in issues] == ["source.secret_in_original"]
    assert issues[0].record == "SRC-architecture-notes"
    (acme_repo / "docs/product-brief.docx").write_bytes(b"changed")
    (acme_repo / "docs/architecture-notes.docx").unlink()
    got = {(i.code, i.record): i.message for i in gate(acme_repo)}
    # the secret is counted in the original as it is now (deleted: no secret), never taken from source.yaml;
    # with no base to vouch for the store, a store that cannot be re-derived is tampered, with the cause
    assert set(got) == {("source.tampered", "SRC-architecture-notes"), ("source.tampered", "SRC-product-brief")}
    assert "is missing" in got[("source.tampered", "SRC-architecture-notes")]
    assert "cannot be stored as it is now" in got[("source.tampered", "SRC-product-brief")]


# --- CLI ----------------------------------------------------------------------------------------

def test_cli_add_and_sync(acme_repo):
    r = invoke(acme_repo, "source", "add", "docs/architecture-notes.docx", "docs/product-brief.docx")
    assert r.exit_code == 0, r.output
    first, second = r.stdout.splitlines()
    assert re.fullmatch(r"SRC-architecture-notes: \d+ segments, guard: \d+ emails, 1 secrets, 1 injections", first)
    assert second.startswith("SRC-product-brief: ")
    r = invoke(acme_repo, "source", "sync")
    assert r.exit_code == 0 and len(r.stdout.splitlines()) == 2


def test_cli_refusal_exits_2_with_reason_on_stderr(acme_repo):
    (acme_repo / "x.md").write_text("# x\n")
    r = invoke(acme_repo, "source", "add", "x.md")
    assert r.exit_code == 2 and "not in config.sources" in r.stderr and r.stdout == ""


# --- reader bounds ------------------------------------------------------------------------------

def test_pdf_page_limit(monkeypatch):
    from kb.sources.readers import pdf as reader

    monkeypatch.setattr(reader, "MAX_PDF_PAGES", 2)
    with pytest.raises(SourceError, match="MAX_PDF_PAGES"):
        reader.read(pdf_bytes([[("", 10, "one")], [("", 10, "two")], [("", 10, "three")]]))
    assert reader.read(pdf_bytes([[("", 10, "one")], [("", 10, "two")]])).text


def test_pdf_text_limit(monkeypatch):
    from kb.sources.readers import pdf as reader

    monkeypatch.setattr(reader, "MAX_TEXT_CHARS", 20)
    with pytest.raises(SourceError, match="MAX_TEXT_CHARS"):
        reader.read(pdf_bytes([[("", 10, "word " * 30)]]))


# --- fix round 1 --------------------------------------------------------------------------------

def test_failed_swap_keeps_previous_source(acme_repo, monkeypatch):
    from pathlib import Path

    cfg = cfg_of(acme_repo)
    (acme_repo / "docs/a.md").write_text("# A\n\na\n")
    add_source(acme_repo, "docs/a.md", cfg)
    before = {p.name: p.read_bytes() for p in (acme_repo / "kb/sources/SRC-a").iterdir()}
    (acme_repo / "docs/a.md").write_text("# A\n\nchanged\n")
    real, calls = Path.rename, []

    def flaky(self, target):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("boom")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError):
        add_source(acme_repo, "docs/a.md", cfg)
    monkeypatch.undo()
    assert {p.name: p.read_bytes() for p in (acme_repo / "kb/sources/SRC-a").iterdir()} == before
    assert [p.name for p in (acme_repo / "kb/sources").iterdir()] == ["SRC-a"]


def test_symlink_to_outside_file_refused(acme_repo, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("# s\n\nx\n")
    (acme_repo / "docs/link.md").symlink_to(outside)
    with pytest.raises(SourceError):
        add_source(acme_repo, "docs/link.md", cfg_of(acme_repo))
    assert not (acme_repo / "kb").exists()


def test_absolute_path_outside_repo_refused(acme_repo, tmp_path):
    outside = tmp_path / "o.md"
    outside.write_text("# s\n")
    with pytest.raises(SourceError):
        add_source(acme_repo, str(outside), cfg_of(acme_repo))
    assert not (acme_repo / "kb").exists()


def test_symlink_to_in_root_file_uses_resolved_path(acme_repo):
    (acme_repo / "docs/real.md").write_text("# R\n\nr\n")
    (acme_repo / "docs/alias.md").symlink_to("real.md")
    src = add_source(acme_repo, "docs/alias.md", cfg_of(acme_repo))
    assert src.id == "SRC-real" and src.original == "docs/real.md"


def test_symlinked_triage_refuses_sync(acme_repo, tmp_path):
    cfg = cfg_of(acme_repo)
    (acme_repo / "docs/a.md").write_text("# A\n\na\n")
    add_source(acme_repo, "docs/a.md", cfg)
    outside = tmp_path / "evil.yaml"
    outside.write_text("AKIAIOSFODNN7EXAMPLE\n")
    (acme_repo / "kb/sources/SRC-a/triage.yaml").symlink_to(outside)
    with pytest.raises(SourceError, match="triage"):
        add_source(acme_repo, "docs/a.md", cfg)
    assert (acme_repo / "kb/sources/SRC-a/triage.yaml").is_symlink()


@pytest.mark.parametrize("which", ["kb", "kb/sources", "kb/sources/SRC-a"])
def test_symlinked_store_dirs_refused(acme_repo, tmp_path, which):
    cfg = cfg_of(acme_repo)
    (acme_repo / "docs/a.md").write_text("# A\n\na\n")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (acme_repo / which).parent.mkdir(parents=True, exist_ok=True)
    (acme_repo / which).symlink_to(outside)
    with pytest.raises(SourceError, match="symlink"):
        add_source(acme_repo, "docs/a.md", cfg)
    assert list(outside.iterdir()) == []


def test_paths_under_kb_are_never_sources(acme_repo):
    cfg = cfg_of(acme_repo)
    add_source(acme_repo, "docs/product-brief.docx", cfg)
    wide = type(cfg).model_validate(cfg.model_dump() | {"sources": [{"paths": ["**"]}]})
    with pytest.raises(SourceError, match="kb/"):
        add_source(acme_repo, "kb/sources/SRC-product-brief/text.md", wide)


def test_gate_invalid_original_and_oversize(acme_repo, tmp_path, monkeypatch):
    cfg = cfg_of(acme_repo)
    add_source(acme_repo, "docs/product-brief.docx", cfg)
    yaml_path = acme_repo / "kb/sources/SRC-product-brief/source.yaml"
    outside = tmp_path / "o.md"
    outside.write_text("x")
    (acme_repo / "docs/link.md").symlink_to(outside)
    yaml_path.write_text(yaml_path.read_text().replace("docs/product-brief.docx", "docs/link.md"))
    (issue,) = gate(acme_repo)
    assert issue.code == "source.tampered" and "symlink" in issue.message
    yaml_path.write_text(yaml_path.read_text().replace("docs/link.md", "docs/product-brief.docx"))
    monkeypatch.setattr("kb.sources.store.MAX_SOURCE_BYTES", 10)
    issues = gate(acme_repo)
    assert [i.code for i in issues] == ["source.tampered"] and "MAX_SOURCE_BYTES" in issues[0].message
