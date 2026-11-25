"""Glossary terms as grounded entities: kind, quoted evidence and quoted relations (0.4.0, Task 6)."""

import pytest

from kb.records.io import RecordError, parse_record, render_record
from kb.records.models import Relation, Requirement, Term

TERM_YAML = """\
id: TERM-account
name: account
aliases:
- accounts
definition: A customer of Acme that holds seats.
kind: actor
evidence:
  quotes:
  - source: SRC-spec#seg-01
    text: An account is a customer of Acme.
relations:
- type: owns
  target: TERM-invoice
  quote:
    source: SRC-spec#seg-01
    text: Each account owns its invoices.
"""


def test_term_with_entity_fields_round_trips():
    rec = parse_record("TERM-account.yaml", TERM_YAML.encode())
    assert isinstance(rec, Term)
    assert rec.kind == "actor" and rec.evidence is not None and rec.evidence.quotes[0].text == "An account is a customer of Acme."
    assert rec.relations == [Relation(type="owns", target="TERM-invoice",
                                      quote={"source": "SRC-spec#seg-01", "text": "Each account owns its invoices."})]
    assert render_record(rec) == TERM_YAML.encode()
    assert list(Term.model_fields)[-7:] == ["id", "name", "aliases", "definition", "kind", "evidence", "relations"]


def test_old_term_still_valid():
    old = b"id: TERM-seat\nname: seat\ndefinition: One licensed user slot on a billing account.\n"
    rec = parse_record("TERM-seat.yaml", old)
    assert isinstance(rec, Term) and rec.kind is None and rec.evidence is None and rec.relations == []
    assert render_record(rec) == old


def test_entity_field_values_are_checked():
    for bad in ("kind: person\n", "relations:\n- type: knows\n  target: TERM-b\n  quote: {source: SRC-spec#seg-01, text: x}\n",
                "relations:\n- type: owns\n  target: REQ-F-001\n  quote: {source: SRC-spec#seg-01, text: x}\n",
                "relations:\n- type: owns\n  target: TERM-b\n"):
        with pytest.raises(RecordError):
            parse_record("TERM-seat.yaml", b"id: TERM-seat\nname: seat\ndefinition: d\n" + bad.encode())
    eleven = "relations:\n" + "- type: owns\n  target: TERM-b\n  quote: {source: SRC-spec#seg-01, text: x}\n" * 11
    with pytest.raises(RecordError, match="10"):
        parse_record("TERM-seat.yaml", b"id: TERM-seat\nname: seat\ndefinition: d\n" + eleven.encode())


def test_requirement_entities_and_term_actor():
    req = {"id": "REQ-F-001", "statement": "s", "evidence": {"quotes": [{"source": "SRC-spec#seg-01", "text": "t"}]},
           "modules": ["MOD-a"]}
    rec = Requirement.model_validate(req | {"actor": "TERM-account", "entities": ["TERM-account", "TERM-invoice"]})
    assert rec.actor == "TERM-account" and rec.entities == ["TERM-account", "TERM-invoice"]
    assert Requirement.model_validate(req | {"actor": "the billing system"}).entities == []
    with pytest.raises(ValueError):
        Requirement.model_validate(req | {"entities": ["Account"]})
