import pytest
from helpers import add_adr, codes, rename_id, set_field

from kb.gates.base import run_gates
from kb.gates.decisions import decision_guard
from kb.gates.ids import id_gate
from kb.records.lifecycle import live_ids, superseded_by


def test_valid_fixture_is_clean(valid_ctx):
    assert run_gates(valid_ctx, [id_gate, decision_guard]) == []


@pytest.mark.parametrize(
    "mutations,code",
    [
        ([rename_id("REQ-F-001", "REQ-F-002")], "id.filename_mismatch"),
        ([rename_id("REQ-F-001", "REQ-F-002")], "id.duplicate"),
        ([set_field("REQ-F-001", modules=["MOD-ghost"])], "ref.missing"),
        ([set_field("REQ-F-001", features=["FEAT-999"])], "ref.missing"),
        ([set_field("REQ-F-001", evidence={"quotes": [{"source": "SRC-nope#seg-01", "text": "x"}]})], "ref.missing"),
        ([set_field("REQ-F-001", evidence={"quotes": [{"source": "SRC-pricing-spec-v3#seg-99", "text": "x"}]})], "ref.missing"),
        ([set_field("FEAT-001", module="MOD-ghost")], "ref.missing"),
        ([set_field("FEAT-001", requirements=["REQ-F-999"])], "ref.missing"),
        ([set_field("INV-001", governs=["MOD-ghost"])], "ref.missing"),
        ([set_field("INV-001", governs=["FEAT-999"])], "ref.missing"),
        ([set_field("ADR-0003", supersedes="ADR-0999")], "ref.missing"),
        ([set_field("ADR-0003", supersedes="INV-001")], "supersede.type_mismatch"),
        ([set_field("FEAT-001", owner="team-ghost")], "owner.unknown"),
        ([add_adr("ADR-0004", supersedes="ADR-0001")], "supersede.conflict"),
        (
            [set_field("ADR-0001", supersedes="ADR-0003")],
            "supersede.cycle",
        ),
        ([set_field("FEAT-001", claims=[{"id": "FEAT-002.C1", "claim": "c"}])], "id.claim_prefix"),
    ],
)
def test_id_gate(valid_ctx_with, mutations, code):
    assert code in codes(run_gates(valid_ctx_with(*mutations), [id_gate]))


def test_triage_items_must_exist(valid_ctx):
    from kb.records.models import Triage

    valid_ctx.repo._triage["SRC-pricing-spec-v3"] = Triage(segments={"seg-02": ["REQ-F-777"]})
    issues = run_gates(valid_ctx, [id_gate])
    assert [(i.code, i.record) for i in issues] == [("ref.missing", "SRC-pricing-spec-v3")]


def test_owner_may_be_team_handle_or_module(valid_ctx_with):
    ctx = valid_ctx_with(
        set_field("FEAT-001", owner="team-billing"), set_field("INV-001", owner="@alice"), set_field("ADR-0001", owner="MOD-billing")
    )
    assert run_gates(ctx, [id_gate]) == []


def test_conflict_names_both_and_is_deterministic(valid_ctx_with):
    ctx = valid_ctx_with(add_adr("ADR-0004", supersedes="ADR-0001"))
    (issue,) = [i for i in run_gates(ctx, [id_gate]) if i.code == "supersede.conflict"]
    assert "ADR-0003" in issue.message and "ADR-0004" in issue.message
    assert superseded_by(ctx.repo) == {"ADR-0001": "ADR-0003"}


def test_superseded_is_derived(valid_ctx):
    assert superseded_by(valid_ctx.repo) == {"ADR-0001": "ADR-0003"}
    assert "ADR-0001" not in live_ids(valid_ctx.repo)
    assert "ADR-0003" in live_ids(valid_ctx.repo)


def test_rejected_record_does_not_supersede(valid_ctx_with):
    ctx = valid_ctx_with(set_field("ADR-0003", status="rejected"))
    assert superseded_by(ctx.repo) == {}
    assert "ADR-0001" in live_ids(ctx.repo) and "ADR-0003" not in live_ids(ctx.repo)
