"""Deterministic alignment of the kb records with run B: quote-span overlap, entity names and relations."""

from helpers import stored_repo

from kb.eval.align import (
    Alignment,
    Item,
    Link,
    agreement,
    align,
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
    assert agreement(al, 3, 3)["matched"] == 2


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


def test_empty_entity_key_never_aligns():
    def term(name: str, *aliases: str) -> Term:
        return Term(id="TERM-x", name=name, aliases=list(aliases), definition="d")

    assert entity_names(term("Seat Pool", "pools")) == ("seat pool", "pools")
    # every name unusable (a stop word, one character): fall back to the folded raw name
    assert entity_names(term("It", ".")) == ("it",)
    assert entity_names(term("  ", "x")) == ()
    al = align([ent("TERM-a"), ent("TERM-it", "it")], [ent("B-TERM-a"), ent("B-TERM-it", "it")])
    assert al.matched_entities == [("TERM-it", "B-TERM-it")]
    assert al.only_kb_entities == ["TERM-a"] and [i.id for i in al.only_b_entities] == ["B-TERM-a"]
    # a relation with an unaligned end never matches either
    al = align([ent("TERM-a", relations=[("owns", "TERM-it")]), ent("TERM-it", "it")],
               [ent("B-TERM-a", relations=[("owns", "B-TERM-it")]), ent("B-TERM-it", "it")])
    assert al.matched_relations == [] and al.only_b_relations == [("B-TERM-a", "owns", "it")]


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
    assert agr["entity_f1"] is None and agr["relation_f1"] is None
    assert agreement(Alignment([], ["REQ-F-001"], [], [], []), 1, 0)["estimated_total"] is None
    text = render_agreement(agr, 16)
    assert "upper bound on recall" in text and "21" in text and "16 of 21" in text
    assert "upper bound on recall" not in render_agreement(agreement(Alignment([], [], [], [], []), 0, 0), 0)
