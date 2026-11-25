"""EXCL scope-exclusion records: quoted "out of scope" statements (0.4.0, Task 5)."""

import json

import pytest
from helpers import nt

from kb.gates.base import GateContext
from kb.gates.coverage import coverage_gate, quote_spans
from kb.gates.quotes import quote_gate
from kb.gates.triage import triage_gate
from kb.records.ids import RecordType, parse_id
from kb.records.io import RecordError, parse_record, render_record
from kb.records.models import Evidence, Exclusion
from kb.records.repo import KbRepo
from kb.sources.segment import assign_segment_ids, segment

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
next_seg: 3
"""
OUT = "Mobile apps are not required in the first release."
DOC = f"# Seats\n\nA seat must be paid before use.\n\n# Scope\n\n{OUT}\n"
# seg-01 Seats (one rule), seg-02 Scope (the out-of-scope sentence, rule-like through "required")

EXCL_YAML = f"""\
id: EXCL-001
statement: Mobile apps are out of scope for the first release.
evidence:
  quotes:
  - source: {SRC}#seg-02
    text: {OUT}
modules:
- MOD-a
"""


def repo_of(files: dict[str, str]) -> KbRepo:
    segs, _ = assign_segment_ids([], segment(nt(DOC)), 1)
    stored = {
        f"kb/sources/{SRC}/source.yaml": SOURCE_YAML,
        f"kb/sources/{SRC}/text.md": DOC,
        f"kb/sources/{SRC}/segments.jsonl": "".join(
            json.dumps({"id": s.id, "heading": s.heading, "page": s.page, "start": s.start, "end": s.end, "hash": s.hash}) + "\n"
            for s in segs
        ),
    }
    repo = KbRepo.from_files({p: t.encode() for p, t in (stored | files).items()})
    assert repo.errors == [], repo.errors
    return repo


def gate(repo: KbRepo, g):
    return g(GateContext(None, None, repo))  # type: ignore[arg-type]


def test_parse_and_render_round_trip():
    rec = parse_record("EXCL-001.yaml", EXCL_YAML.encode())
    assert isinstance(rec, Exclusion)
    assert rec.statement == "Mobile apps are out of scope for the first release." and rec.modules == ["MOD-a"] and rec.features == []
    assert render_record(rec) == EXCL_YAML.encode()
    assert list(Exclusion.model_fields)[-5:] == ["id", "statement", "evidence", "modules", "features"]


def test_exclusion_fields_are_only_the_briefs():
    # no requirement classification: modality, actor, category and origin are not exclusion fields
    for field in ("modality: must_not", "actor: the app", "origin: stated", "flags: [vague]"):
        with pytest.raises(RecordError):
            parse_record("EXCL-001.yaml", (EXCL_YAML + field + "\n").encode())
    with pytest.raises(RecordError, match="evidence"):
        parse_record("EXCL-001.yaml", b"id: EXCL-001\nstatement: s\nmodules: [MOD-a]\n")
    with pytest.raises(RecordError, match="extension"):
        parse_record("EXCL-001.md", b"---\nid: EXCL-001\n---\n")
    with pytest.raises(ValueError):
        Exclusion(id="EXCL-01", statement="s", evidence=Evidence(quotes=[{"source": f"{SRC}#seg-02", "text": OUT}]))


def test_parse_id():
    assert parse_id("EXCL-001") == (RecordType.EXCLUSION, "EXCL-001", None)
    assert parse_id("EXCL-1234").type is RecordType.EXCLUSION
    for bad in ("EXCL-01", "EXCL-abc", "EXC-001"):
        with pytest.raises(ValueError):
            parse_id(bad)


def test_repo_loads_exclusions_dir():
    repo = repo_of({"kb/exclusions/EXCL-001.yaml": EXCL_YAML})
    assert isinstance(repo.records["EXCL-001"], Exclusion)
    assert repo.paths["EXCL-001"] == "kb/exclusions/EXCL-001.yaml"


def test_missing_quote_is_quote_not_found():
    assert [i for i in gate(repo_of({"kb/exclusions/EXCL-001.yaml": EXCL_YAML}), quote_gate) if i.record == "EXCL-001"] == []
    wrong = EXCL_YAML.replace(OUT, "Mobile apps are out of scope entirely.")
    issues = [i for i in gate(repo_of({"kb/exclusions/EXCL-001.yaml": wrong}), quote_gate) if i.record == "EXCL-001"]
    assert [(i.code, i.severity) for i in issues] == [("quote.not_found", "error")]
    assert issues[0].message == f"EXCL-001: quote not found in {SRC}#seg-02: `Mobile apps are out of scope entirely.`"


def test_quoted_out_of_scope_sentence_is_covered():
    def uncovered(repo):
        return [i.message for i in gate(repo, coverage_gate) if i.code == "sentence.uncovered"]

    assert any(OUT in m for m in uncovered(repo_of({})))
    repo = repo_of({"kb/exclusions/EXCL-001.yaml": EXCL_YAML})
    assert [rid for spans in quote_spans(repo).values() for *_, rid in spans] == ["EXCL-001"]
    assert not any(OUT in m for m in uncovered(repo))
    # a rejected exclusion covers nothing
    rejected = repo_of({"kb/exclusions/EXCL-001.yaml": EXCL_YAML + "status: rejected\n"})
    assert any(OUT in m for m in uncovered(rejected))


def test_triage_naming_an_exclusion_must_be_quoted():
    good = repo_of({"kb/exclusions/EXCL-001.yaml": EXCL_YAML, TRIAGE: "segments:\n  seg-02: [EXCL-001]\n"})
    assert [i for i in gate(good, triage_gate) if i.code == "triage.not_quoted"] == []
    bad = repo_of({"kb/exclusions/EXCL-001.yaml": EXCL_YAML, TRIAGE: "segments:\n  seg-01: [EXCL-001]\n"})
    assert [i.message for i in gate(bad, triage_gate) if i.code == "triage.not_quoted"] == [
        f"{SRC}#seg-01: triage names `EXCL-001` but none of them quotes this segment"]


def test_use_exclusion_names_the_file_form():
    repo = repo_of({TRIAGE: "segments:\n  seg-01: 'none: heading'\n  seg-02: 'none: out-of-scope: v2'\n"})
    (i,) = [i for i in gate(repo, triage_gate) if i.code == "triage.use_exclusion"]
    assert "`kb/exclusions/EXCL-NNN.yaml`" in i.message
