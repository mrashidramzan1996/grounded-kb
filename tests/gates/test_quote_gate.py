from helpers import add_adr, codes, req, set_field

from kb.gates.base import run_gates
from kb.gates.quotes import MAX_QUOTES_PER_CLAIM, quote_gate

SPEC = "pricing-spec-v3"


def test_valid_fixture_is_clean(valid_ctx):
    assert run_gates(valid_ctx, [quote_gate]) == []


def test_grounded_quote_passes(acme_kb_with):
    ctx = acme_kb_with(req("REQ-F-001", "Annual billing is available on the Team plan.", quote=(SPEC, "2. Plans", "Annual billing is available on the Team and Enterprise plans.")))
    assert run_gates(ctx, [quote_gate]) == []


def test_invented_quote_rejected(acme_kb_with):
    ctx = acme_kb_with(req("REQ-F-009", "Annual plans get two months free.", quote=(SPEC, "2. Plans", "Annual plans get two months free")))
    assert codes(run_gates(ctx, [quote_gate])) == ["quote.not_found"]


def test_invented_number_flagged(acme_kb_with):
    ctx = acme_kb_with(req("REQ-NF-001", "Checkout p95 is under 300 ms.", quote=(SPEC, "5. Non-functional requirements", "The checkout page must be fast.")))
    issues = run_gates(ctx, [quote_gate])
    assert codes(issues) == ["statement.number_not_in_quote"]
    assert issues[0].severity == "warning"


def test_number_with_thousands_separator_matches(acme_kb_with):
    ctx = acme_kb_with(req("REQ-NF-002", "Generating invoices for 10000 subscriptions takes under 15 minutes.", quote=(SPEC, "5. Non-functional requirements", "Invoice generation for 10,000 subscriptions must complete within 15 minutes.")))
    assert run_gates(ctx, [quote_gate]) == []


def test_quote_in_other_segment_is_not_enough(acme_kb_with):
    ctx = acme_kb_with(req("REQ-F-010", "Seats can be added.", quote=(SPEC, "3.2 Removing seats", "An administrator can add seats at any time.")))
    assert codes(run_gates(ctx, [quote_gate])) == ["quote.not_found"]


def test_too_many_quotes(acme_kb_with):
    def many(segment_id):
        rec = req("REQ-F-011", "Seats can be added.", quote=(SPEC, "3.1 Adding seats", "An administrator can add seats at any time."))(segment_id)
        q = rec.evidence.quotes[0]
        return rec.model_copy(update={"evidence": rec.evidence.model_copy(update={"quotes": [q] * (MAX_QUOTES_PER_CLAIM + 1)})})

    assert codes(run_gates(acme_kb_with(many), [quote_gate])) == ["quote.too_many"]


def test_vague_flag_is_listed(acme_kb_with):
    ctx = acme_kb_with(req("REQ-NF-003", "The checkout page is fast.", quote=(SPEC, "5. Non-functional requirements", "The checkout page must be fast."), flags=["vague"]))
    issues = run_gates(ctx, [quote_gate])
    assert codes(issues) == ["requirement.vague"] and issues[0].severity == "warning"


def test_missing_segment_is_left_to_id_gate(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence={"quotes": [{"source": "SRC-pricing-spec-v3#seg-99", "text": "x"}]}))
    assert "quote.not_found" not in codes(run_gates(ctx, [quote_gate]))


def test_not_found_in_valid_fixture(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence={"quotes": [{"source": "SRC-pricing-spec-v3#seg-03", "text": "Refunds are always given."}]}))
    assert codes(run_gates(ctx, [quote_gate])) == ["quote.not_found"]


GONE = {"quotes": [{"source": "SRC-pricing-spec-v3#seg-03", "text": "Refunds are always given."}]}


def test_superseded_record_quote_not_found_is_warning(valid_ctx_with):
    ctx = valid_ctx_with(
        set_field("REQ-F-001", evidence=GONE, status="rejected"),
        add_adr("ADR-0901", evidence=GONE),
        add_adr("ADR-0902", supersedes="ADR-0901"),
    )
    found = [i for i in run_gates(ctx, [quote_gate]) if i.code == "quote.not_found"]
    assert [(i.record, i.severity) for i in found] == [("ADR-0901", "warning"), ("REQ-F-001", "warning")]
    assert found[0].message == (
        "ADR-0901: quote no longer in the current source (SRC-pricing-spec-v3#seg-03); the record is superseded or rejected"
    )
    live = valid_ctx_with(add_adr("ADR-0901", evidence=GONE))
    assert [i.severity for i in run_gates(live, [quote_gate]) if i.code == "quote.not_found"] == ["error"]
