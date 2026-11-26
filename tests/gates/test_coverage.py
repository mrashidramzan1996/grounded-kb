import json

from helpers import nt

from kb.gates.base import GateContext
from kb.gates.coverage import coverage_gate, coverage_stats, quote_spans, uncovered
from kb.gates.triage import triage_gate
from kb.records.repo import KbRepo
from kb.sources.segment import assign_segment_ids, segment
from kb.sources.sentences import sentence_key

SRC = "SRC-spec"
TRIAGE = f"kb/sources/{SRC}/triage.yaml"
SOURCE_YAML = f"""\
id: {SRC}
original: docs/spec.md
original_sha256: "{"a" * 64}"
normaliser: text@1
trust: spec
visibility: internal
guard:
  emails: 0
  secrets: 0
  injections: 0
  injection_segments: []
next_seg: 9
"""
DOC = """\
# Seats

## Adding

A seat must be paid before use. The account owner must approve every new seat.

## Removing

## Refunds

A removed seat stays active until the end of the billing cycle and no refund must be given for it.
"""
# seg-01 "Seats" (heading only), seg-02 Adding (two rules), seg-03 Removing (heading only), seg-04 Refunds


def req(rid: str, seg: str, text: str, **extra) -> tuple[str, str]:
    lines = [f"id: {rid}", f"statement: {text}", "evidence:", "  quotes:", f"  - source: {SRC}#{seg}", f"    text: {text}",
             "modules: [MOD-a]"] + [f"{k}: {v}" for k, v in extra.items()]
    return f"kb/requirements/{rid}.yaml", "\n".join(lines) + "\n"


def repo_of(*records: tuple[str, str], triage: str | None = None, doc: str = DOC) -> KbRepo:
    segs, _ = assign_segment_ids([], segment(nt(doc)), 1)
    files = {
        f"kb/sources/{SRC}/source.yaml": SOURCE_YAML.encode(),
        f"kb/sources/{SRC}/text.md": doc.encode(),
        f"kb/sources/{SRC}/segments.jsonl": "".join(
            json.dumps({"id": s.id, "heading": s.heading, "page": s.page, "start": s.start, "end": s.end, "hash": s.hash}) + "\n"
            for s in segs
        ).encode(),
    }
    if triage is not None:
        files[TRIAGE] = triage.encode()
    files.update({p: t.encode() for p, t in records})
    repo = KbRepo.from_files(files)
    assert repo.errors == [], repo.errors
    return repo


def gate(repo: KbRepo, g=coverage_gate):
    return g(GateContext(None, None, repo))  # type: ignore[arg-type]


def test_uncovered_rule_sentence_warns():
    repo = repo_of(req("REQ-F-001", "seg-02", "A seat must be paid before use."))
    issues = [i for i in gate(repo) if i.code == "sentence.uncovered"]
    assert len(issues) == 2  # seg-02's second sentence and seg-04's sentence
    seg2 = [i for i in issues if i.message.startswith(f"{SRC}#seg-02")]
    assert len(seg2) == 1
    (i,) = seg2
    assert i.severity == "warning" and i.path == TRIAGE and i.record == SRC
    assert i.message == f"{SRC}#seg-02: rule-like sentence no record quotes: `The account owner must approve every new seat.`"
    assert [s.text for s in uncovered(repo) if s.ref == f"{SRC}#seg-02"] == ["The account owner must approve every new seat."]


def test_uncovered_skips_decided_sentences():
    repo = repo_of()
    keys = {sentence_key(s) for s in uncovered(repo)}
    assert len(keys) == 3
    assert uncovered(repo, keys) == []


def test_heading_only_segment_is_silent():
    doc = "# Seats\n\n## Overview\n\nThis page describes seats.\n"
    repo = repo_of(triage="segments:\n  seg-01: 'none: heading'\n  seg-02: 'none: background: intro'\n", doc=doc)
    assert gate(repo) == []
    assert [i for i in gate(repo, triage_gate)] == []
    assert uncovered(repo) == []


def test_quote_spanning_two_rules_flags_merge():
    both = "A seat must be paid before use. The account owner must approve every new seat."
    repo = repo_of(req("REQ-F-001", "seg-02", both))
    merges = [i for i in gate(repo) if i.code == "quote.multiple_rules"]
    assert [(i.severity, i.record, i.path) for i in merges] == [("warning", "REQ-F-001", "kb/requirements/REQ-F-001.yaml")]
    assert merges[0].message == "REQ-F-001: its quote spans 2 rule-like sentences; split it into one record per rule"
    one = repo_of(req("REQ-F-001", "seg-02", "A seat must be paid before use."))
    assert [i for i in gate(one) if i.code == "quote.multiple_rules"] == []


def test_one_sentence_that_clause_splits_is_one_rule():
    one = "A seat must be paid before use; it must not be shared, and the owner must approve it."
    two = one + " The account owner must approve every new seat."
    doc = f"# Seats\n\n## Adding\n\n{two}\n"
    repo = repo_of(req("REQ-F-001", "seg-02", one), doc=doc)
    assert [i for i in gate(repo) if i.code == "quote.multiple_rules"] == []
    merges = [i for i in gate(repo_of(req("REQ-F-001", "seg-02", two), doc=doc)) if i.code == "quote.multiple_rules"]
    assert [i.message for i in merges] == ["REQ-F-001: its quote spans 2 rule-like sentences; split it into one record per rule"]


def test_triage_none_with_rule_warns():
    repo = repo_of(triage="segments:\n  seg-01: 'none: heading'\n  seg-02: 'none: background: seat basics'\n"
                          "  seg-03: 'none: heading'\n  seg-04: 'none: other'\n")
    issues = [i for i in gate(repo) if i.code == "triage.none_has_rules"]
    assert [(i.severity, i.path, i.record) for i in issues] == [("warning", TRIAGE, SRC)] * 2
    assert {i.message.split(":")[0] for i in issues} == {f"{SRC}#seg-02", f"{SRC}#seg-04"}


def test_quote_spans_cover_live_records_only_and_use_text_offsets():
    text = "A seat must be paid before use."
    repo = repo_of(req("REQ-F-001", "seg-02", text), req("REQ-F-002", "seg-02", "The account owner must approve every new seat.",
                                                          status="rejected"))
    spans = quote_spans(repo)
    ((start, end, rid),) = spans[SRC]
    assert rid == "REQ-F-001" and repo.source_text(SRC)[start:end] == text
    # a quote not found in its segment (or too short) covers nothing
    missing = repo_of(req("REQ-F-001", "seg-04", text))
    assert quote_spans(missing) == {}


def test_coverage_stats():
    text = "A seat must be paid before use."
    repo = repo_of(req("REQ-F-001", "seg-02", text))
    stats = coverage_stats(repo)
    assert stats == {"normative": 3, "covered": 1, "ratio": 1 / 3, "quoted_chars": len(text), "text_chars": len(DOC)}
    empty = repo_of(doc="# Seats\n")
    assert coverage_stats(empty) == {"normative": 0, "covered": 0, "ratio": 1.0, "quoted_chars": 0, "text_chars": len("# Seats\n")}


def test_overlapping_quotes_count_quoted_chars_once():
    text = "A seat must be paid before use."
    repo = repo_of(req("REQ-F-001", "seg-02", text), req("REQ-F-002", "seg-02", "must be paid before use."))
    assert coverage_stats(repo)["quoted_chars"] == len(text)


def test_stats_skip_sources_with_invalid_segment_ids():
    repo = repo_of()
    bad = "SRC-bad"
    files = dict(repo.raw)
    files[f"kb/sources/{bad}/source.yaml"] = SOURCE_YAML.replace(SRC, bad).encode()
    files[f"kb/sources/{bad}/text.md"] = b"A seat must be paid.\n"
    files[f"kb/sources/{bad}/segments.jsonl"] = (
        json.dumps({"id": "x`y", "heading": "H", "page": 1, "start": 0, "end": 20, "hash": "0" * 12}) + "\n"
    ).encode()
    both = KbRepo.from_files(files)
    assert coverage_stats(both) == coverage_stats(repo)  # neither its sentences nor its text count
    assert gate(both) == gate(repo)


def test_located_quotes_give_text_offsets():
    from kb.gates.quotes import located_quotes

    text = "A seat must be paid before use."
    repo = repo_of(req("REQ-F-001", "seg-02", text))
    ev = repo.records["REQ-F-001"].evidence
    ((q, src, start, end),) = located_quotes(repo, ev)
    assert (q, src, repo.source_text(SRC)[start:end]) == (ev.quotes[0], SRC, text)
