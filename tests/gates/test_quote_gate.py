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


# --- numbers in feature claims come from their requirements ----------------------------------

from helpers import set_claim  # noqa: E402

_REQ_500 = set_field(
    "REQ-F-001",
    statement="Seats can be removed until 500 seats.",
    evidence={"quotes": [{"source": "SRC-pricing-spec-v3#seg-03", "text": "Seats can be removed until 500 seats."}]},
)


def _claim_issues(ctx):
    return [i for i in run_gates(ctx, [quote_gate]) if i.code == "claim.number_not_in_requirement"]


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
    path = ctx.root / "kb/features/FEAT-001.md"
    from helpers import load_dir
    from kb.gates.base import GateContext
    from kb.records.repo import KbRepo

    files = load_dir(ctx.root)
    files["kb/features/FEAT-001.md"] = files["kb/features/FEAT-001.md"] + b"\nFree for the first 90 days.\n"
    ctx = GateContext(ctx.root, ctx.config, KbRepo.from_files(files))
    (issue,) = _claim_issues(ctx)
    assert issue.message == "FEAT-001: title and body numbers not in any linked requirement: 90"
    assert path.exists()


def test_ids_in_claims_are_not_numbers(valid_ctx_with):
    ctx = valid_ctx_with(set_claim("FEAT-001", 0, claim="Implements REQ-F-001 in seg-03."))
    assert _claim_issues(ctx) == []
