import pytest

from kb.records.ids import RecordType, parse_id


@pytest.mark.parametrize(
    "s,t,seg",
    [
        ("SRC-pricing-spec-v3#seg-08", RecordType.SOURCE, "seg-08"),
        ("REQ-NF-004", RecordType.REQUIREMENT, None),
        ("FEAT-014.C2", RecordType.CLAIM, None),
        ("ADR-0041", RecordType.DECISION, None),
    ],
)
def test_parse_id(s, t, seg):
    assert (parse_id(s).type, parse_id(s).segment) == (t, seg)


def test_parse_id_value():
    assert parse_id("FEAT-014.C2").id == "FEAT-014.C2"
    p = parse_id("SRC-x#seg-08")
    assert (p.id, p.segment) == ("SRC-x", "seg-08")


@pytest.mark.parametrize("bad", ["REQ-34", "adr-0041", "FEAT-014.C", "SRC-x#seg-8", "MOD-billing"])
def test_rejects_bad_id(bad):
    with pytest.raises(ValueError):
        parse_id(bad)
