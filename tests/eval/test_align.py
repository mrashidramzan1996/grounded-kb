"""Deterministic alignment of the kb records with run B: quote-span overlap, entity names and relations."""

from helpers import stored_repo

from kb.eval.align import (
    Alignment,
    Item,
    Link,
    agreement,
    align,
    entity_item,
    entity_names,
    kb_items,
    render_agreement,
)
from kb.eval.extract import RUN_B, load_run_b
from kb.records.models import Term

SPEC = (
    "# Billing\n\nA seat costs 5 EUR per month. Refunds are never given for a removed seat.\n\n"
    "# Accounts\n\nAn account is a customer of Acme. Each account owns its invoices.\n"
)


def rec(rid: str, *spans: tuple[str, int, int], kind: str = "requirement") -> Item:
    return Item(rid, kind, f"statement of {rid}", tuple(spans))


def ent(iid: str, *names: str, relations=()) -> Item:
    return Item(iid, "entity", f"definition of {iid}", (), names=names, relations=tuple(Link(t, tgt, "q", "SRC-a#seg-01")
                                                                                       for t, tgt in relations))


def test_align_matches_by_quote_overlap():
    kb = [rec("REQ-F-001", ("SRC-a", 0, 100)), rec("REQ-F-002", ("SRC-a", 300, 400))]
    b = [
        rec("B-REQ-F-001", ("SRC-a", 40, 100)),  # 60 / min(100, 60): inside the kb quote
        rec("B-REQ-F-002", ("SRC-a", 90, 200)),  # 10 / 100: too little
        rec("B-REQ-F-003", ("SRC-b", 300, 400)),  # same offsets, other source
        rec("B-ADR-0001", ("SRC-b", 0, 10), ("SRC-a", 340, 420), kind="decision"),  # 60 / min(100, 80) in SRC-a
    ]
    al = align(kb, b)
    assert al.matched == [("REQ-F-001", "B-REQ-F-001"), ("REQ-F-002", "B-ADR-0001")]
    assert al.only_kb == [] and [i.id for i in al.only_b] == ["B-REQ-F-002", "B-REQ-F-003"]
    assert al.splits == [] and al.merges == []
    # exactly half is enough
    assert align([rec("K", ("SRC-a", 0, 10))], [rec("B", ("SRC-a", 5, 20))]).matched == [("K", "B")]
    assert align([rec("K", ("SRC-a", 0, 10))], [rec("B", ("SRC-a", 6, 20))]).matched == []


def test_align_detects_split_and_merge():
    kb = [rec("REQ-F-001", ("SRC-a", 0, 100)), rec("REQ-F-002", ("SRC-a", 200, 260)), rec("REQ-F-003", ("SRC-a", 270, 330))]
    b = [rec("B-REQ-F-001", ("SRC-a", 0, 50)), rec("B-REQ-F-002", ("SRC-a", 50, 100)), rec("B-REQ-F-003", ("SRC-a", 200, 330))]
    al = align(kb, b)
    assert al.splits == [("REQ-F-001", ["B-REQ-F-001", "B-REQ-F-002"])]
    assert al.merges == [(["REQ-F-002", "REQ-F-003"], "B-REQ-F-003")]
    assert al.only_kb == [] and al.only_b == []
    # a split or a merge is one fact found by both runs
    agr = agreement(al, 3, 3)
    assert agr["matched"] == 2 and agr["kb_items"] == 3 and agr["b_items"] == 3


def test_pure_split_is_full_agreement():
    """One kb record that run B drafted as two: both runs found the same one fact."""
    al = align([rec("REQ-F-001", ("SRC-a", 0, 100))], [rec("B-REQ-F-001", ("SRC-a", 0, 50)), rec("B-REQ-F-002", ("SRC-a", 50, 100))])
    agr = agreement(al, 1, 2)
    assert agr["matched"] == 1 and agr["span_f1"] == 1.0 and agr["estimated_total"] == 1
    assert (agr["kb_items"], agr["b_items"]) == (1, 2)
    # a split next to unmatched items: kb has 1 + 1 facts, run B 1 + 2
    al = align([rec("K1", ("SRC-a", 0, 100)), rec("K2", ("SRC-a", 500, 600))],
               [rec("B1", ("SRC-a", 0, 50)), rec("B2", ("SRC-a", 50, 100)), rec("B3", ("SRC-b", 0, 9)), rec("B4", ("SRC-b", 20, 29))])
    agr = agreement(al, 2, 4)
    assert agr["span_f1"] == 2 * 1 / (2 + 3) and agr["estimated_total"] == 6


def test_estimated_total_rounds_half_up():
    # 5 x 5 / 2 = 12.5: half-up gives 13 (banker's rounding would give 12)
    al = Alignment(matched=[("K1", "B1"), ("K2", "B2")], only_kb=["K3", "K4", "K5"],
                   only_b=[rec(f"B{n}", ("SRC-a", 0, 1)) for n in (3, 4, 5)], splits=[], merges=[])
    assert agreement(al, 5, 5)["estimated_total"] == 13


def test_align_on_records_and_run_b(tmp_path):
    req = ("id: REQ-F-001\nstatement: A seat costs 5 EUR per month.\nevidence:\n  quotes:\n  - source: SRC-spec#seg-01\n"
           "    text: A seat costs 5 EUR per month. Refunds are never given for a removed seat.\nmodules: [MOD-a]\n")
    repo = stored_repo(SPEC, {"kb/requirements/REQ-F-001.yaml": req})
    b = ("id: B-REQ-F-002\nstatement: A seat costs 5 EUR per month.\nevidence:\n  quotes:\n  - source: SRC-spec#seg-01\n"
         "    text: seat costs 5 EUR per month\nmodules: [MOD-a]\n")
    (tmp_path / RUN_B / "requirements").mkdir(parents=True)
    (tmp_path / RUN_B / "requirements/B-REQ-F-002.yaml").write_text(b)
    b_items, issues = load_run_b(tmp_path, repo)
    assert issues == []
    (k,) = kb_items(repo)
    text = repo.source_text("SRC-spec")
    ((src, a, z),) = k.spans
    assert (src, text[a:z]) == ("SRC-spec", "A seat costs 5 EUR per month. Refunds are never given for a removed seat.")
    assert align([k], b_items).matched == [("REQ-F-001", "B-REQ-F-002")]


def test_kb_items_are_live_records_with_evidence_and_terms():
    files = {
        "kb/requirements/REQ-F-001.yaml": ("id: REQ-F-001\nstatement: x\nevidence:\n  quotes:\n  - source: SRC-spec#seg-01\n"
                                           "    text: A seat costs 5 EUR per month.\nmodules: [MOD-a]\n"),
        "kb/requirements/REQ-F-002.yaml": ("id: REQ-F-002\nstatus: rejected\nstatement: x\nevidence:\n  quotes:\n"
                                           "  - source: SRC-spec#seg-01\n    text: A seat costs 5 EUR per month.\nmodules: [MOD-a]\n"),
        "kb/decisions/ADR-0001.md": "---\nid: ADR-0001\ntitle: No quote\ngoverns: [MOD-a]\n---\nWhy.\n",
        "kb/glossary/TERM-account.yaml": ("id: TERM-account\nname: Account\naliases: [accounts]\ndefinition: A customer.\n"
                                          "relations:\n- type: owns\n  target: TERM-invoice\n  quote:\n    source: SRC-spec#seg-02\n"
                                          "    text: Each account owns its invoices.\n"),
        "kb/glossary/TERM-invoice.yaml": "id: TERM-invoice\nname: invoice\ndefinition: A bill.\n",
    }
    items = kb_items(stored_repo(SPEC, files))
    assert [(i.id, i.kind) for i in items] == [("REQ-F-001", "requirement"), ("TERM-account", "entity"), ("TERM-invoice", "entity")]
    account = items[1]
    assert account.names == ("account", "accounts")
    assert account.relations == (Link("owns", "TERM-invoice", "Each account owns its invoices.", "SRC-spec#seg-02"),)


def test_unlocated_records_are_left_out_of_alignment():
    files = {
        "kb/requirements/REQ-F-001.yaml": ("id: REQ-F-001\nstatement: x\nevidence:\n  quotes:\n  - source: SRC-spec#seg-01\n"
                                           "    text: A seat costs 5 EUR per month.\nmodules: [MOD-a]\n"),
        # its only quote is not in the document: no span to align by
        "kb/requirements/REQ-F-002.yaml": ("id: REQ-F-002\nstatement: x\nevidence:\n  quotes:\n  - source: SRC-spec#seg-01\n"
                                           "    text: A seat costs 9 EUR per month.\nmodules: [MOD-a]\n"),
    }
    al = align(kb_items(stored_repo(SPEC, files)), [])
    assert al.unlocated == ["REQ-F-002"] and al.only_kb == ["REQ-F-001"]
    agr = agreement(al, 2, 0)
    assert agr["unlocated"] == 1 and agr["only_kb"] == 1


def test_entities_and_relations_align_by_name_and_alias():
    kb = [
        ent("TERM-account", "account", relations=[("owns", "TERM-invoice"), ("uses", "TERM-plan")]),
        ent("TERM-invoice", "invoice", "invoices"),
        ent("TERM-plan", "plan"),
    ]
    b = [
        ent("B-TERM-customer-account", "customer account", "account", relations=[("owns", "B-TERM-invoices"),
                                                                                 ("contains", "B-TERM-seat")]),
        ent("B-TERM-invoices", "invoices"),  # matches TERM-invoice by its alias
        ent("B-TERM-seat", "seat"),
    ]
    al = align(kb, b)
    assert al.matched == [] and al.only_kb == [] and al.only_b == []
    assert al.matched_entities == [("TERM-account", "B-TERM-customer-account"), ("TERM-invoice", "B-TERM-invoices")]
    assert al.only_kb_entities == ["TERM-plan"] and [i.id for i in al.only_b_entities] == ["B-TERM-seat"]
    assert al.matched_relations == [("TERM-account:owns:TERM-invoice", "B-TERM-customer-account:owns:B-TERM-invoices")]
    assert al.only_kb_relations == ["TERM-account:uses:TERM-plan"]
    assert al.only_b_relations == [("customer account", "contains", "seat")]
    # the same ends with another type, or reversed, is another relation
    al = align(kb[:2], [ent("B-a", "account", relations=[("part_of", "B-i")]), ent("B-i", "invoice", relations=[("owns", "B-a")])])
    # (TERM-plan is not among these items: a relation to it cannot be keyed and is only kb's)
    assert al.matched_relations == []
    assert al.only_kb_relations == ["TERM-account:owns:TERM-invoice", "TERM-account:uses:TERM-plan"]
    assert al.only_b_relations == [("account", "part_of", "invoice"), ("invoice", "owns", "account")]
    agr = agreement(al, 0, 0)
    assert agr["entity_f1"] == 1.0 and agr["relation_f1"] == 0.0 and agr["span_f1"] is None


def test_unmatched_relations_count_by_id():
    """Two run B terms with the same name, each with the same relation: two relations, not one."""
    b = [ent("B-TERM-a1", "account", relations=[("owns", "B-TERM-i")]), ent("B-TERM-a2", "account", relations=[("owns", "B-TERM-i")]),
         ent("B-TERM-i", "invoice")]
    al = align([ent("TERM-account", "account"), ent("TERM-invoice", "invoice")], b)
    assert al.only_b_relations == [("account", "owns", "invoice"), ("account", "owns", "invoice")]
    # the same relation twice on one term (two quotes) is still one
    al = align([], [ent("B-a", "account", relations=[("owns", "B-i"), ("owns", "B-i")]), ent("B-i", "invoice")])
    assert al.only_b_relations == [("account", "owns", "invoice")]
    agr = agreement(al, 0, 0)
    assert agr["relation_f1"] == 0.0 and agr["entity_f1"] == 0.0


def test_empty_entity_key_never_aligns():
    def term(name: str, *aliases: str) -> Term:
        return Term(id="TERM-x", name=name, aliases=list(aliases), definition="d")

    assert entity_names(term("Seat Pool", "pools")) == ("seat pool", "pools")
    # every name unusable (a stop word, one character): no names at all, never the raw name
    assert entity_names(term("It", ".")) == ()
    assert entity_names(term("  ", "x")) == ()
    kb_it = entity_item("TERM-it", Term(id="TERM-it", name="IT", definition="d"), ())
    b_it = entity_item("B-TERM-it", Term(id="TERM-it", name="it", definition="d"), ())
    al = align([kb_it, ent("TERM-seat", "seat")], [b_it, ent("B-TERM-seat", "seat")])
    assert al.matched_entities == [("TERM-seat", "B-TERM-seat")]
    assert al.only_kb_entities == ["TERM-it"] and [i.id for i in al.only_b_entities] == ["B-TERM-it"]
    # a relation with an unaligned end never matches either
    al = align([ent("TERM-a", relations=[("owns", "TERM-seat")]), ent("TERM-seat", "seat")],
               [ent("B-TERM-a", relations=[("owns", "B-TERM-seat")]), ent("B-TERM-seat", "seat")])
    assert al.matched_relations == [] and al.only_b_relations == [("B-TERM-a", "owns", "seat")]


def test_agreement_numbers():
    al = Alignment(
        matched=[(f"REQ-F-{n:03d}", f"B-REQ-F-{n:03d}") for n in range(1, 15)],
        only_kb=["REQ-F-015", "REQ-F-016"],
        only_b=[rec(f"B-REQ-F-{n:03d}", ("SRC-a", 0, 1)) for n in range(15, 19)],
        splits=[],
        merges=[],
    )
    agr = agreement(al, 16, 18)
    assert agr["matched"] == 14 and agr["only_kb"] == 2 and agr["only_b"] == 4
    assert round(agr["span_f1"], 4) == 0.8235 and agr["estimated_total"] == 21
    assert (agr["kb_items"], agr["b_items"], agr["unlocated"]) == (16, 18, 0)
    assert agr["entity_f1"] is None and agr["relation_f1"] is None
    assert agreement(Alignment([], ["REQ-F-001"], [], [], []), 1, 0)["estimated_total"] is None
    text = render_agreement(agr)
    assert "upper bound on recall" in text and "21" in text and "16 of 21" in text
    assert "upper bound on recall" not in render_agreement(agreement(Alignment([], [], [], [], []), 0, 0))
