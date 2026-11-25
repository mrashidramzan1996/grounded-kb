"""Requirement classification fields against the quote."""

import json

from helpers import apply_drafts, codes, git, invoke, req

from kb.gates.base import run_gates
from kb.gates.quotes import quote_gate
from kb.ingest.report import ingest_report

SPEC = "pricing-spec-v3"
NF = "5. Non-functional requirements"
MUST = (SPEC, NF, "The checkout page must be fast.")
FIELDS = {"modality": "must", "actor": "the checkout page", "category": "performance"}


def gate(acme_kb_with, *records):
    return run_gates(acme_kb_with(*records), [quote_gate])


def test_fields_missing_is_warning_in_check_error_in_report(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    path = acme_repo_ingested / "kb/requirements/REQ-NF-001.yaml"
    path.write_text("".join(l for l in path.read_text().splitlines(keepends=True) if not l.startswith(("modality", "actor", "category"))))
    data = json.loads(invoke(acme_repo_ingested, "check", "--format", "json").stdout)
    missing = [i for i in data["issues"] if i["code"] == "requirement.fields_missing"]
    assert [(i["record"], i["severity"]) for i in missing] == [("REQ-NF-001", "warning")]
    assert "modality" in missing[0]["message"] and "category" in missing[0]["message"]
    r = ingest_report(acme_repo_ingested, "HEAD")
    assert ("REQ-NF-001", "requirement.fields_missing") in [(i.record, i.code) for i in r.rejected]
    assert "REQ-NF-001" not in r.accepted


def test_category_missing_on_nf_only(acme_kb_with):
    issues = gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, modality="must", actor="the checkout page", category=None))
    assert codes(issues) == ["requirement.fields_missing"] and "category" in issues[0].message
    assert gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS)) == []


def test_modality_mismatch(acme_kb_with):
    issues = gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS | {"modality": "should"}))
    assert codes(issues) == ["requirement.modality_mismatch"] and issues[0].severity == "warning"
    assert gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS)) == []


def test_modality_mismatch_any_cue_matches(acme_kb_with, monkeypatch):
    import kb.gates.quotes as q

    monkeypatch.setattr(q, "cues", lambda text: frozenset({"must", "may"}))
    assert gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS | {"modality": "may"})) == []


def test_stated_without_rule_word(acme_kb_with):
    quote = (SPEC, "3.2 Removing seats", "No refund is given.")
    f = {"modality": "must_not", "actor": "billing", "origin": "stated"}
    issues = gate(acme_kb_with, req("REQ-F-002", "No refund is given.", quote, **f))
    assert codes(issues) == ["requirement.no_rule_word"] and "origin: implied" in issues[0].message
    assert codes(gate(acme_kb_with, req("REQ-F-002", "No refund is given.", quote, **f | {"origin": "implied"}))) == ["requirement.implied_unconfirmed"]


def test_implied_needs_clarification(acme_kb_with):
    issues = gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS, origin="implied"))
    assert codes(issues) == ["requirement.implied_unconfirmed"] and issues[0].severity == "warning"
