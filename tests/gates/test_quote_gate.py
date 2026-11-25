from helpers import add_adr, codes, load_dir, quiet, req, set_claim, set_field

from kb.gates.base import GateContext, run_gates
from kb.gates.quotes import MAX_QUOTES_PER_CLAIM, quote_gate
from kb.records.repo import KbRepo

SPEC = "pricing-spec-v3"


def test_valid_fixture_is_clean(valid_ctx):
    assert quiet(run_gates(valid_ctx, [quote_gate])) == []


def test_grounded_quote_passes(acme_kb_with):
    ctx = acme_kb_with(req("REQ-F-001", "Annual billing is available on the Team plan.", quote=(SPEC, "2. Plans", "Annual billing is available on the Team and Enterprise plans.")))
    assert quiet(run_gates(ctx, [quote_gate])) == []


def test_invented_quote_rejected(acme_kb_with):
    ctx = acme_kb_with(req("REQ-F-009", "Annual plans get two months free.", quote=(SPEC, "2. Plans", "Annual plans get two months free")))
    assert codes(quiet(run_gates(ctx, [quote_gate]))) == ["quote.not_found"]


def test_invented_number_flagged(acme_kb_with):
    ctx = acme_kb_with(req("REQ-NF-001", "Checkout p95 is under 300 ms.", quote=(SPEC, "5. Non-functional requirements", "The checkout page must be fast.")))
    issues = quiet(run_gates(ctx, [quote_gate]))
    assert codes(issues) == ["statement.number_not_in_quote"]
    assert issues[0].severity == "warning"


def test_number_with_thousands_separator_matches(acme_kb_with):
    ctx = acme_kb_with(req("REQ-NF-002", "Generating invoices for 10000 subscriptions takes under 15 minutes.", quote=(SPEC, "5. Non-functional requirements", "Invoice generation for 10,000 subscriptions must complete within 15 minutes.")))
    assert quiet(run_gates(ctx, [quote_gate])) == []


def test_quote_in_other_segment_is_not_enough(acme_kb_with):
    ctx = acme_kb_with(req("REQ-F-010", "Seats can be added.", quote=(SPEC, "3.2 Removing seats", "An administrator can add seats at any time.")))
    assert codes(quiet(run_gates(ctx, [quote_gate]))) == ["quote.not_found"]


def test_too_many_quotes(acme_kb_with):
    def many(segment_id):
        rec = req("REQ-F-011", "Seats can be added.", quote=(SPEC, "3.1 Adding seats", "An administrator can add seats at any time."))(segment_id)
        q = rec.evidence.quotes[0]
        return rec.model_copy(update={"evidence": rec.evidence.model_copy(update={"quotes": [q] * (MAX_QUOTES_PER_CLAIM + 1)})})

    assert codes(quiet(run_gates(acme_kb_with(many), [quote_gate]))) == ["quote.too_many"]


def test_vague_flag_is_listed(acme_kb_with):
    ctx = acme_kb_with(req("REQ-NF-003", "The checkout page is fast.", quote=(SPEC, "5. Non-functional requirements", "The checkout page must be fast."), flags=["vague"]))
    issues = quiet(run_gates(ctx, [quote_gate]))
    assert codes(issues) == ["requirement.vague"] and issues[0].severity == "warning"


def test_missing_segment_is_left_to_id_gate(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence={"quotes": [{"source": "SRC-pricing-spec-v3#seg-99", "text": "x"}]}))
    assert "quote.not_found" not in codes(quiet(run_gates(ctx, [quote_gate])))


def test_not_found_in_valid_fixture(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence={"quotes": [{"source": "SRC-pricing-spec-v3#seg-03", "text": "Refunds are always given."}]}))
    assert codes(quiet(run_gates(ctx, [quote_gate]))) == ["quote.not_found"]


GONE = {"quotes": [{"source": "SRC-pricing-spec-v3#seg-03", "text": "Refunds are always given."}]}


def test_superseded_record_quote_not_found_is_warning(valid_ctx_with):
    ctx = valid_ctx_with(
        set_field("REQ-F-001", evidence=GONE, status="rejected"),
        add_adr("ADR-0901", evidence=GONE),
        add_adr("ADR-0902", supersedes="ADR-0901"),
    )
    found = [i for i in quiet(run_gates(ctx, [quote_gate])) if i.code == "quote.not_found"]
    assert [(i.record, i.severity) for i in found] == [("ADR-0901", "warning"), ("REQ-F-001", "warning")]
    assert found[0].message == (
        "ADR-0901: quote no longer in the current source (SRC-pricing-spec-v3#seg-03); the record is superseded or rejected"
    )
    live = valid_ctx_with(add_adr("ADR-0901", evidence=GONE))
    assert [i.severity for i in quiet(run_gates(live, [quote_gate])) if i.code == "quote.not_found"] == ["error"]


# --- numbers in feature claims come from their requirements ----------------------------------

_REQ_500 = set_field(
    "REQ-F-001",
    statement="Seats can be removed until 500 seats.",
    evidence={"quotes": [{"source": "SRC-pricing-spec-v3#seg-03", "text": "Seats can be removed until 500 seats."}]},
)


def _claim_issues(ctx):
    return [i for i in quiet(run_gates(ctx, [quote_gate])) if i.code == "claim.number_not_in_requirement"]


def test_claim_number_must_come_from_its_requirement(valid_ctx_with):
    ctx = valid_ctx_with(_REQ_500, set_claim("FEAT-001", 0, claim="Seats are free for the first 90 days.", requirement="REQ-F-001"))
    (issue,) = _claim_issues(ctx)
    assert (issue.severity, issue.message) == ("warning", "FEAT-001.C1: claim numbers not in REQ-F-001: 90")


def test_claim_number_found_in_requirement_quote_passes(valid_ctx_with):
    ctx = valid_ctx_with(_REQ_500, set_claim("FEAT-001", 0, claim="Up to 500 seats.", requirement="REQ-F-001"))
    assert _claim_issues(ctx) == []


def test_claim_without_requirement_uses_the_feature_requirements(valid_ctx_with):
    ctx = valid_ctx_with(_REQ_500, set_field("FEAT-001", requirements=["REQ-F-001"]),
                         set_claim("FEAT-001", 0, claim="Free for 90 days.", requirement=None))
    (issue,) = _claim_issues(ctx)
    assert issue.message == "FEAT-001.C1: claim numbers not in REQ-F-001: 90"
    ctx = valid_ctx_with(set_field("FEAT-001", requirements=[]), set_claim("FEAT-001", 0, claim="Free for 90 days.", requirement=None))
    (issue,) = _claim_issues(ctx)
    assert issue.message == "FEAT-001.C1: claim numbers not in any linked requirement: 90"


def test_feature_body_numbers_checked(valid_ctx_with):
    ctx = valid_ctx_with(_REQ_500, set_field("FEAT-001", requirements=["REQ-F-001"]))
    assert _claim_issues(ctx) == []
    files = load_dir(ctx.root)
    files["kb/features/FEAT-001.md"] = files["kb/features/FEAT-001.md"] + b"\nFree for the first 90 days.\n"
    ctx = GateContext(ctx.root, ctx.config, KbRepo.from_files(files))
    (issue,) = _claim_issues(ctx)
    assert issue.message == "FEAT-001: title and body numbers not in any linked requirement: 90"


def test_ids_in_claims_are_not_numbers(valid_ctx_with):
    ctx = valid_ctx_with(set_claim("FEAT-001", 0, claim="Implements REQ-F-001 in seg-03."))
    assert _claim_issues(ctx) == []


def _quotes(*texts):
    return {"quotes": [{"source": "SRC-pricing-spec-v3#seg-03", "text": t} for t in texts]}


def test_short_quote_rejected(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence=_quotes("s")))
    short = [i for i in quiet(run_gates(ctx, [quote_gate])) if i.code == "quote.too_short"]
    assert [i.severity for i in short] == ["warning"]  # an error only for drafted records, via the reports


def test_iso_number_checked(acme_kb_with):
    ctx = acme_kb_with(req("REQ-NF-004", "Comply with ISO-27001.", quote=(SPEC, "5. Non-functional requirements", "The checkout page must be fast.")))
    assert "statement.number_not_in_quote" in codes(quiet(run_gates(ctx, [quote_gate])))


REAL = {"source": "SRC-pricing-spec-v3#seg-03", "text": "A removed seat stays active until the end of the billing cycle."}
FAKE = {"source": "SRC-pricing-spec-v3#seg-03", "text": "Refunds are always given within 45 days."}


def test_mode_any_allows_one_unfound_quote(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence={"quotes": [REAL, FAKE]}))
    assert [i.severity for i in quiet(run_gates(ctx, [quote_gate])) if i.code == "quote.not_found"] == ["error"]
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence={"mode": "any", "quotes": [REAL, FAKE]}))
    found = [i for i in quiet(run_gates(ctx, [quote_gate])) if i.code == "quote.not_found"]
    assert [i.severity for i in found] == ["warning"]


def test_mode_any_none_found_is_error(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence={"mode": "any", "quotes": [FAKE]}))
    assert [i.severity for i in quiet(run_gates(ctx, [quote_gate])) if i.code == "quote.not_found"] == ["error"]


def test_unfound_quote_cannot_ground_numbers(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", statement="A removed seat stays active for 45 days.", evidence={"mode": "any", "quotes": [REAL, FAKE]}))
    assert "statement.number_not_in_quote" in codes(quiet(run_gates(ctx, [quote_gate])))


def test_too_short_quote_cannot_ground_numbers(valid_ctx_with):
    short = {"source": "SRC-pricing-spec-v3#seg-03", "text": "cycle 45"}
    ctx = valid_ctx_with(set_field("REQ-F-001", statement="A removed seat stays active for 45 days.", evidence={"quotes": [REAL, short]}))
    assert "statement.number_not_in_quote" in codes(quiet(run_gates(ctx, [quote_gate])))


def test_too_short_on_superseded_record_stays_warning(valid_ctx_with):
    ctx = valid_ctx_with(set_field("REQ-F-001", evidence={"quotes": [REAL, {**REAL, "text": "seat"}]}, status="rejected"))
    short = [i for i in quiet(run_gates(ctx, [quote_gate])) if i.code == "quote.too_short"]
    assert [i.severity for i in short] == ["warning"]
