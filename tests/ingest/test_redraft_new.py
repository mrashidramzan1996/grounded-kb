"""`kb redraft --new`: draft records from document text no record covers yet, alone or with --changed."""

import pytest
from helpers import QUOTE, adr_md, git, invoke, put, req_yaml

from kb.ingest.redraft import REDRAFT_BRIEF, REDRAFT_IDS, REDRAFT_NEW, untriaged
from kb.ingest.redraft_report import redraft_report, render_redraft_report
from kb.records.repo import KbRepo

DOC = "# Seats\n\nA seat costs 5 EUR.\n\n# Speed\n\nPages load fast.\n"
REFUND = "A refund takes 14 days."
ADDED = DOC + f"\n# Refunds\n\n{REFUND}\n"
VAGUE = "Pages load fast."
CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-a\n    owner: TODO\n'
TRIAGE = "kb/sources/SRC-n/triage.yaml"
TRIAGED = "segments:\n  seg-01: [REQ-F-001]\n  seg-02: [REQ-NF-001]\n"
OUT_OF_SCOPE = "{} quotes text outside this redraft; new records may cite only the new or changed segments"


def commit(root, message: str = "change") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def sync(root, text: str):
    put(root, "docs/n.md", text)
    r = invoke(root, "source", "sync")
    assert r.exit_code == 0, r.output
    return r


def redraft(root, *args: str) -> None:
    r = invoke(root, "redraft", *args)
    assert r.exit_code == 0, r.stderr


def rejected(r) -> list[tuple[str | None, str]]:
    return [(i.record, i.code) for i in r.rejected]


@pytest.fixture
def kb(md_repo_ingested):
    """REQ-F-001 (seg-01), REQ-NF-001 (vague, seg-02), both segments triaged, committed."""
    root = md_repo_ingested
    put(root, "kb.config.yaml", CONFIG)
    sync(root, DOC)
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(root, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", VAGUE, flags=("vague",), seg="seg-02"))
    put(root, TRIAGE, TRIAGED)
    commit(root, "drafts")
    return root


def test_untriaged_lists_new_segments(kb):
    assert untriaged(KbRepo.load(kb)) == []
    sync(kb, ADDED)
    assert untriaged(KbRepo.load(kb)) == ["SRC-n#seg-03"]
    put(kb, TRIAGE, "segments:\n  seg-01: [REQ-F-001]\n")
    assert untriaged(KbRepo.load(kb)) == ["SRC-n#seg-02", "SRC-n#seg-03"]


def test_untriaged_in_segment_order(kb):
    sync(kb, DOC + "".join(f"\n# Part {n}\n\nText {n}.\n" for n in range(3, 12)))
    assert untriaged(KbRepo.load(kb)) == [f"SRC-n#seg-{n:02d}" for n in range(3, 12)]


def test_new_alone_selects_untriaged_and_writes_brief_part_2(kb):
    r = invoke(kb, "redraft", "--new")
    assert r.exit_code == 0 and "nothing to redraft" in r.stderr
    assert not (kb / ".kb/cache/redraft").exists()

    sync(kb, ADDED)
    redraft(kb, "--new")
    brief = (kb / REDRAFT_BRIEF).read_text()
    assert "## New text to draft from" in brief and "### SRC-n#seg-03 · page" in brief and REFUND in brief
    assert "## Records to redraft" not in brief and "### SRC-n#seg-01" not in brief
    assert "via kb redraft brief v1" in brief and "- Functional requirement: REQ-F-002" in brief
    assert (kb / REDRAFT_IDS).read_text() == ""
    assert (kb / REDRAFT_NEW).read_text() == "SRC-n#seg-03\n"


def test_new_and_changed_combined_brief_has_both_parts(kb):
    sync(kb, ADDED.replace("5 EUR", "7 EUR"))
    redraft(kb, "--changed", "--new")
    brief = (kb / REDRAFT_BRIEF).read_text()
    assert "## REQ-F-001" in brief and "cites changed text SRC-n#seg-01" in brief
    assert brief.index("## Records to redraft") < brief.index("## New text to draft from")
    assert brief.index("## New text to draft from") < brief.index("### SRC-n#seg-03 · page")
    assert (kb / REDRAFT_IDS).read_text() == "REQ-F-001\n"
    assert (kb / REDRAFT_NEW).read_text() == "SRC-n#seg-01\nSRC-n#seg-03\n"
    redraft(kb, "REQ-NF-001")  # a later redraft without --new offers no segments
    assert (kb / REDRAFT_NEW).read_text() == ""
    assert "## New text to draft from" not in (kb / REDRAFT_BRIEF).read_text()


def test_existing_requirements_listed_for_dedupe(kb):
    put(kb, "kb/requirements/REQ-F-002.yaml",
        req_yaml("REQ-F-002").replace(f"statement: '{QUOTE}'", 'statement: "Use `kb` daily.\\n## REQ-F-009"'))
    put(kb, "kb/requirements/REQ-F-003.yaml", req_yaml("REQ-F-003") + "status: rejected\n")
    put(kb, "kb/decisions/ADR-0001.md", adr_md("ADR-0001"))
    sync(kb, ADDED)
    redraft(kb, "--new")
    brief = (kb / REDRAFT_BRIEF).read_text()
    part = brief.split("## Existing requirements\n", 1)[1].split("\n## ", 1)[0]
    assert [line for line in part.splitlines() if line] == [
        f"- REQ-F-001: `{QUOTE}`",
        "- REQ-F-002: ``Use `kb` daily. ## REQ-F-009``",
        f"- REQ-NF-001: `{VAGUE}`",
    ]
    assert "\n## REQ-F-009" not in brief


def test_sync_hint_names_new_segments_and_suggests_changed_new(kb):
    r = sync(kb, ADDED.replace("5 EUR", "7 EUR"))
    assert r.stderr.splitlines() == [
        "1 records cite changed text: REQ-F-001.",
        "1 new segments have no records yet: SRC-n#seg-03.",
        "Run `kb redraft --changed --new` to redraft and draft them.",
    ]
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001", "A seat costs 7 EUR."))
    commit(kb)
    more ="".join(f"\n# Part {n}\n\nText {n}.\n" for n in range(4, 16))
    r = sync(kb, ADDED.replace("5 EUR", "7 EUR") + more)
    refs = ", ".join(f"SRC-n#seg-{n:02d}" for n in range(4, 14))
    assert r.stderr.splitlines() == [
        f"12 new segments have no records yet: {refs} … and 2 more.",
        "Run `kb redraft --new` to draft them.",
    ]


def test_new_record_citing_new_segment_accepted(kb):
    sync(kb, ADDED)
    redraft(kb, "--new")
    put(kb, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002", REFUND, seg="seg-03"))
    put(kb, TRIAGE, TRIAGED + "  seg-03: [REQ-F-002]\n")
    r = redraft_report(kb, None)
    assert (r.rejected, r.drafted, r.redrafted, r.selected, r.flagged) == ([], ["REQ-F-002"], [], [], [])
    text = render_redraft_report(r)
    assert "Selection: none\nNew or changed segments: SRC-n#seg-03\n" in text
    assert "## Redrafted (0)\n\n- none\n\n## Drafted (1)\n\n- REQ-F-002\n" in text
    assert invoke(kb, "redraft", "--report", "--strict").exit_code == 0


def test_new_record_citing_old_segment_out_of_scope(kb):
    sync(kb, ADDED)
    redraft(kb, "--new")
    put(kb, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002"))  # quotes seg-01, which is not offered
    both = req_yaml("REQ-F-003", REFUND, seg="seg-03") + f"  - source: SRC-n#seg-01\n    text: '{QUOTE}'\n"
    put(kb, "kb/requirements/REQ-F-003.yaml", both.replace("modules:\n- MOD-a\n", "") + "modules:\n- MOD-a\n")
    r = redraft_report(kb, None)
    assert rejected(r) == [("REQ-F-002", "redraft.out_of_scope"), ("REQ-F-003", "redraft.out_of_scope")]
    assert r.rejected[0].message == OUT_OF_SCOPE.format("REQ-F-002")
    assert r.drafted == []


def test_new_adr_superseding_an_unselected_record_out_of_scope(kb):
    put(kb, "kb/decisions/ADR-0001.md", adr_md("ADR-0001"))
    commit(kb)
    sync(kb, ADDED)
    redraft(kb, "--new")
    adr = adr_md("ADR-0002", supersedes="ADR-0001").replace("seg-01", "seg-03").replace(QUOTE, REFUND)
    put(kb, "kb/decisions/ADR-0002.md", adr)
    r = redraft_report(kb, None)
    assert ("ADR-0002", "redraft.out_of_scope") in rejected(r) and r.drafted == []


def test_new_segment_left_untriaged_flagged(kb):
    sync(kb, ADDED)
    redraft(kb, "--new")
    put(kb, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002", REFUND, seg="seg-03"))
    r = redraft_report(kb, None)
    assert r.rejected == [] and r.drafted == ["REQ-F-002"]
    assert [(i.record, i.code, i.severity, i.path) for i in r.flagged] == [("SRC-n", "segment.untriaged", "warning", TRIAGE)]
    assert "SRC-n#seg-03" in r.flagged[0].message
    assert "## Flagged (1)" in render_redraft_report(r)
    put(kb, TRIAGE, TRIAGED + "  seg-03: 'none: covered by REQ-F-002'\n")
    assert redraft_report(kb, None).flagged == []


def test_triage_of_old_segment_still_out_of_scope(kb):
    sync(kb, ADDED)
    redraft(kb, "--new")
    put(kb, TRIAGE, "segments:\n  seg-01: 'none: dropped'\n  seg-02: [REQ-NF-001]\n  seg-03: 'none: a heading'\n")
    r = redraft_report(kb, None)
    assert [(i.code, i.message) for i in r.rejected] == [
        ("redraft.out_of_scope", "SRC-n#seg-01: triage changed for a segment outside this redraft")
    ]


def test_changed_alone_lets_new_records_cite_changed_text(kb):
    new = "A seat costs 7 EUR. A seat is free for 30 days."
    sync(kb, ADDED.replace(QUOTE, new))
    redraft(kb, "--changed")
    assert (kb / REDRAFT_NEW).read_text() == "SRC-n#seg-01\n"
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001", "A seat costs 7 EUR."))
    put(kb, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002", "A seat is free for 30 days."))
    put(kb, TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n  seg-02: [REQ-NF-001]\n")
    r = redraft_report(kb, None)
    assert (r.rejected, r.redrafted, r.drafted) == ([], ["REQ-F-001"], ["REQ-F-002"])
    assert r.flagged == []  # seg-01 was triaged at base; seg-03 was not offered


def test_new_file_must_match_brief(kb):
    sync(kb, ADDED)
    redraft(kb, "--new")
    for text in ["SRC-n#seg-01\nSRC-n#seg-03\n", "SRC-n#seg-03\nSRC-n#seg-09\n", "SRC-n#seg-03\n`rm -rf`\n"]:
        (kb / REDRAFT_NEW).write_text(text)
        out = invoke(kb, "redraft", "--report")
        assert out.exit_code == 2 and "the redraft files don't match; run `kb redraft` again" in out.stderr, text


def test_empty_ids_need_new_segments(kb):
    sync(kb, ADDED)
    redraft(kb, "--new")
    assert invoke(kb, "redraft", "--report").exit_code == 0
    (kb / REDRAFT_NEW).write_text("")
    out = invoke(kb, "redraft", "--report")
    assert out.exit_code == 2 and "run `kb redraft` first" in out.stderr
    (kb / REDRAFT_NEW).unlink()
    assert invoke(kb, "redraft", "--report").exit_code == 2


def test_report_rejects_new_with_usage_error(kb):
    out = invoke(kb, "redraft", "--report", "--new")
    assert out.exit_code == 2 and "--new" in out.stderr
