import hashlib

from kb.records.models import FeatureClaim, Source, Triage
from kb.records.repo import KbRepo
from kb.sources.segment import Segment
from helpers import load_dir, make_git_repo

SRC = "SRC-pricing-spec-v3"


def test_bad_files_are_all_reported_and_others_still_load(fixtures):
    files = load_dir(fixtures / "kb_valid") | {
        "kb/requirements/REQ-F-099.yaml": b"id: REQ-F-099\nstatus: maybe\n",
        "kb/requirements/notes.txt": b"hi",
    }
    repo = KbRepo.from_files(files)
    assert "REQ-F-001" in repo.records and "REQ-F-099" not in repo.records
    assert sorted((i.code, i.path) for i in repo.errors) == [
        ("schema", "kb/requirements/REQ-F-099.yaml"),
        ("schema.unknown_file", "kb/requirements/notes.txt"),
    ]


def test_at_ref_reads_committed_state(fixtures, tmp_path):
    repo = make_git_repo(fixtures / "kb_valid", tmp_path)
    (repo / "kb/decisions/ADR-0001.md").unlink()
    assert "ADR-0001" in KbRepo.at_ref(repo, "HEAD").records
    assert "ADR-0001" not in KbRepo.load(repo).records


def test_valid_fixture_loads_clean(fixtures):
    repo = KbRepo.load(fixtures / "kb_valid")
    assert repo.errors == []
    assert sorted(repo.records) == [
        "ADR-0001", "ADR-0002", "ADR-0003", "FEAT-001", "INV-001",
        "REQ-F-001", "REQ-F-002", "REQ-F-003", "TERM-seat",
    ]
    assert repo.paths["REQ-F-001"] == "kb/requirements/REQ-F-001.yaml"
    assert repo.paths["ADR-0002"] == "kb/decisions/ADR-0002.md"


def test_claims_yield_feature_claim_pairs(fixtures):
    repo = KbRepo.load(fixtures / "kb_valid")
    assert [(f.id, c.id) for f, c in repo.claims()] == [("FEAT-001", "FEAT-001.C1")]
    assert all(isinstance(c, FeatureClaim) for _, c in repo.claims())


def test_source_accessors(fixtures):
    repo = KbRepo.load(fixtures / "kb_valid")
    src = repo.sources[SRC]
    assert isinstance(src, Source) and src.normaliser == "text@1"
    text = repo.source_text(SRC)
    assert text == (fixtures / "kb_valid/docs/pricing-spec-v3.md").read_text(encoding="utf-8")
    assert src.original_sha256 == hashlib.sha256((fixtures / "kb_valid/docs/pricing-spec-v3.md").read_bytes()).hexdigest()
    segs = repo.segments(SRC)
    assert [s.id for s in segs] == ["seg-01", "seg-02", "seg-03", "seg-04"]
    assert src.next_seg == len(segs) + 1
    for s in segs:
        assert isinstance(s, Segment) and s.page == 1
        assert s.hash == hashlib.sha256(text[s.start : s.end].encode()).hexdigest()[:12]
    assert "No refund is given." in text[segs[2].start : segs[2].end]


def test_triage_covers_every_segment(fixtures):
    repo = KbRepo.load(fixtures / "kb_valid")
    tri = repo.triage(SRC)
    assert isinstance(tri, Triage)
    assert set(tri.segments) == {s.id for s in repo.segments(SRC)}
    assert repo.triage("SRC-missing") is None
    assert repo.segments("SRC-missing") == [] and repo.source_text("SRC-missing") == ""


def test_source_file_problems_are_reported(fixtures):
    files = load_dir(fixtures / "kb_valid")
    files[f"kb/sources/{SRC}/segments.jsonl"] = b'{"id": "seg-01"}\nnot json\n'
    files[f"kb/sources/{SRC}/triage.yaml"] = b"segments:\n  seg-01: skipped\n"
    files[f"kb/sources/{SRC}/extra.bin"] = b"x"
    files["kb/sources/SRC-other/source.yaml"] = b"id: SRC-wrong\n"
    repo = KbRepo.from_files(files)
    got = sorted((i.code, i.path) for i in repo.errors)
    assert got == [
        ("schema", "kb/sources/SRC-other/source.yaml"),
        ("schema", f"kb/sources/{SRC}/segments.jsonl"),
        ("schema", f"kb/sources/{SRC}/triage.yaml"),
        ("schema.unknown_file", f"kb/sources/{SRC}/extra.bin"),
    ]
    assert "SRC-other" not in repo.sources


def test_files_outside_kb_are_ignored():
    repo = KbRepo.from_files({"README.md": b"x", "docs/a.txt": b"y"})
    assert repo.records == {} and repo.errors == []
