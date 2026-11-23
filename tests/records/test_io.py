import pytest

from kb.records.io import RecordError, parse_record, render_record
from kb.records.models import Decision, Feature, Requirement, Term


def test_canonical_fixtures_round_trip(fixtures):
    seen = 0
    for p in (fixtures / "kb_valid/kb").rglob("*"):
        if p.suffix in {".yaml", ".md"} and "sources" not in p.parts:
            assert render_record(parse_record(p.name, p.read_bytes())) == p.read_bytes(), p
            seen += 1
    assert seen == 9


def test_render_omits_defaults_and_none():
    out = render_record(Term(id="TERM-seat", name="seat", definition="A licence."))
    assert out == b"id: TERM-seat\nname: seat\ndefinition: A licence.\n"


def test_md_record_has_front_matter_and_body():
    d = Decision(id="ADR-0001", title="T", governs=["MOD-a"], body="Body text.\n")
    out = render_record(d)
    assert out == b"---\nid: ADR-0001\ntitle: T\ngoverns:\n- MOD-a\n---\nBody text.\n"
    back = parse_record("ADR-0001.md", out)
    assert isinstance(back, Decision) and back.body == "Body text.\n"


def test_feature_without_body_round_trips():
    f = Feature(id="FEAT-001", title="T", module="MOD-a")
    assert parse_record("FEAT-001.md", render_record(f)) == f


def test_parse_dispatches_on_file_name():
    r = parse_record(
        "REQ-F-001.yaml",
        b"id: REQ-F-001\nstatement: s\nevidence:\n  quotes:\n  - source: SRC-a#seg-01\n    text: t\nmodules:\n- MOD-a\n",
    )
    assert isinstance(r, Requirement)


@pytest.mark.parametrize(
    "name,data",
    [
        ("REQ-F-001.yaml", b"id: REQ-F-001\nstatus: maybe\n"),
        ("REQ-F-001.yaml", b"id: [unclosed\n"),
        ("REQ-F-001.yaml", b"- a\n- b\n"),
        ("REQ-F-001.yaml", b"a: 1\na: 2\n"),
        ("REQ-F-001.yaml", b"\xff\xfe"),
        ("TERM-seat.yaml", b"id: TERM-other\nname: n\ndefinition: d\n"),  # id != file name
        ("ADR-0001.md", b"id: ADR-0001\n"),  # no front matter
        ("ADR-0001.md", b"---\nid: ADR-0001\n"),  # unterminated
        ("ADR-0001.md", b"---\nid: ADR-0001\ntitle: T\ngoverns: [MOD-a]\nbody: x\n---\nbody\n"),  # body in front matter
        ("ADR-0001.md", b"---\nid: ADR-0001\ntitle: T\ngoverns: [MOD-a]\napproved_by: x\n---\nb\n"),  # derived field
        ("REQ-F-001.md", b"---\nid: REQ-F-001\n---\n"),  # wrong extension for type
        ("notes.txt", b"hi"),
        ("WEIRD-001.yaml", b"id: x\n"),
    ],
)
def test_bad_records_raise_record_error(name, data):
    with pytest.raises(RecordError):
        parse_record(name, data)
