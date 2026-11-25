from helpers import QUOTE, adr_md, invoke, put, req_yaml

from kb.ingest.redraft import changed_segments, citing, flagged, segment_hashes, unfound
from kb.records.repo import KbRepo
from kb.yamlio import load_yaml_strict

def resync(root, text: str) -> KbRepo:
    put(root, "docs/n.md", text)
    assert invoke(root, "source", "sync").exit_code == 0
    return KbRepo.load(root)


def test_changed_segments_by_hash(md_repo_ingested):
    before = KbRepo.load(md_repo_ingested)
    assert segment_hashes(before).keys() == {"SRC-n#seg-01"}
    after = resync(md_repo_ingested, "# Seats\n\nA seat costs 7 EUR.\n")
    assert changed_segments(before, after) == {"SRC-n#seg-01"}
    assert changed_segments(before, before) == set()
    gone = resync(md_repo_ingested, "# Other\n\nunrelated\n")
    assert "SRC-n#seg-01" in changed_segments(before, gone)


def test_citing_only_live_records(md_repo_ingested):
    put(md_repo_ingested, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(md_repo_ingested, "kb/decisions/ADR-0001.md", adr_md("ADR-0001"))
    put(md_repo_ingested, "kb/decisions/ADR-0002.md", adr_md("ADR-0002", supersedes="ADR-0001"))
    repo = KbRepo.load(md_repo_ingested)
    assert citing(repo, {"SRC-n#seg-01"}) == ["ADR-0002", "REQ-F-001"]
    assert citing(repo, {"SRC-n#seg-09"}) == []


def test_flagged_lists_requirements_with_flags(md_repo_ingested):
    put(md_repo_ingested, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(md_repo_ingested, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", flags=("vague",)))
    assert flagged(KbRepo.load(md_repo_ingested)) == ["REQ-NF-001"]


def test_unfound_lists_records_whose_quote_vanished(md_repo_ingested):
    put(md_repo_ingested, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(md_repo_ingested, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002", text="A seat is free."))
    put(md_repo_ingested, "kb/requirements/REQ-F-003.yaml", req_yaml("REQ-F-003", seg="seg-09"))
    assert unfound(KbRepo.load(md_repo_ingested)) == ["REQ-F-002", "REQ-F-003"]


def test_req_yaml_writes_every_flag():
    assert load_yaml_strict(req_yaml("REQ-F-001", flags=("vague", "conflicting")))["flags"] == ["vague", "conflicting"]


def test_unfound_under_mode_any_needs_no_quote_found(md_repo_ingested):
    good = "{'source': 'SRC-n#seg-01', 'text': 'A seat costs 5 EUR.'}"
    bad = "{'source': 'SRC-n#seg-01', 'text': 'A seat is free for everyone.'}"
    def rec(rid, mode, quotes):
        return f"id: {rid}\nstatement: A seat costs 5 EUR.\nevidence:\n  mode: {mode}\n  quotes: [{', '.join(quotes)}]\nmodules:\n- MOD-a\n"
    put(md_repo_ingested, "kb/requirements/REQ-F-001.yaml", rec("REQ-F-001", "any", [good, bad]))
    put(md_repo_ingested, "kb/requirements/REQ-F-002.yaml", rec("REQ-F-002", "all", [good, bad]))
    put(md_repo_ingested, "kb/requirements/REQ-F-003.yaml", rec("REQ-F-003", "any", [bad]))
    assert unfound(KbRepo.load(md_repo_ingested)) == ["REQ-F-002", "REQ-F-003"]
