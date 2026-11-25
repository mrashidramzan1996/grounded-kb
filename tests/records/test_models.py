import pytest
from pydantic import ValidationError

from kb.records.models import Decision, Requirement, Triage


def test_spec_requirement_without_derived_fields_validates(spec_req):
    assert Requirement.model_validate(spec_req).evidence.quotes[0].text == "No refund is given."


@pytest.mark.parametrize("field", ["fingerprint", "approved_by", "confirmed_at", "level", "proposed_by"])
def test_derived_fields_are_rejected(spec_req, field):
    with pytest.raises(ValidationError, match=field):
        Requirement.model_validate({**spec_req, field: "x"})


def test_requirement_needs_feature_or_module(spec_req):
    with pytest.raises(ValidationError):
        Requirement.model_validate({**spec_req, "features": [], "modules": []})


def test_decision_may_carry_quotes():
    d = Decision.model_validate(
        {
            "id": "ADR-0001",
            "title": "Session storage",
            "governs": ["MOD-sessions"],
            "body": "",
            "evidence": {"quotes": [{"source": "SRC-architecture-notes#seg-05", "text": "we will store sessions in Postgres"}]},
        }
    )
    assert d.evidence.quotes[0].text.endswith("Postgres")


def test_triage_string_must_be_none_reason():
    assert Triage.model_validate({"segments": {"seg-01": ["REQ-F-001"], "seg-02": "none: boilerplate"}})
    with pytest.raises(ValidationError, match="none: "):
        Triage.model_validate({"segments": {"seg-01": "skipped"}})


@pytest.mark.parametrize(
    "val",
    [[], ["not-an-id"], ["REQ-F-001", "oops"], "none:", "none: ", "none:   "],
)
def test_triage_rejects_bad_values(val):
    with pytest.raises(ValidationError):
        Triage.model_validate({"segments": {"seg-01": val}})


def test_triage_accepts_ids_and_reasoned_none():
    t = Triage.model_validate({"segments": {"seg-01": ["REQ-F-001", "ADR-0002"], "seg-02": "none: title"}})
    assert t.segments["seg-02"] == "none: title"


def test_classification_fields_default_and_validate(spec_req):
    r = Requirement.model_validate(spec_req)
    assert (r.modality, r.actor, r.category, r.origin) == (None, None, None, "stated")
    nf = {**spec_req, "id": "REQ-NF-001", "modality": "must_not", "actor": "TERM-seat", "category": "security", "origin": "implied"}
    assert Requirement.model_validate(nf).category == "security"


@pytest.mark.parametrize("bad", [
    {"modality": "shall"}, {"origin": "guessed"}, {"actor": "x" * 61}, {"actor": "the `admin`"}, {"actor": "<b>admin</b>"},
    {"actor": "line\nbreak"}, {"actor": "tab\there"}, {"actor": "bell\x07"}, {"actor": "zero\u200bwidth"}, {"category": "performance"}, {"id": "REQ-NF-001", "category": "speed"},
])
def test_classification_fields_rejected(spec_req, bad):
    with pytest.raises(ValidationError):
        Requirement.model_validate({**spec_req, **bad})
