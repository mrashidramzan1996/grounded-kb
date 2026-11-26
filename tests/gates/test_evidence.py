"""C2: decisions and invariants rest on quotes; accepted invariants are immutable."""

import json

from helpers import apply_drafts, codes, edit, git, invoke

from kb.gates.base import run_gates
from kb.gates.decisions import decision_guard
from kb.gates.quotes import quote_gate
from kb.ingest.report import ingest_report

BY = 'drafted_by: "agent:x via kb ingest brief v1"\n'
# the reviewer's reproductions: grounded in nothing
ADR_MONGO = f"---\nid: ADR-0003\n{BY}title: Store invoices in MongoDB\ngoverns:\n- MOD-billing\n---\nWe will store invoices in MongoDB.\n"
INV_500 = f"---\nid: INV-002\n{BY}statement: Refunds above 500 dollars need two approvals.\ngoverns:\n- MOD-billing\n---\n"


def add_ungrounded(root):
    (root / "kb/decisions/ADR-0003.md").write_text(ADR_MONGO)
    (root / "kb/invariants/INV-002.md").write_text(INV_500)


def pairs(issues):
    return [(i.record, i.code) for i in issues]


def test_report_rejects_decisions_and_invariants_without_evidence(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    add_ungrounded(acme_repo_ingested)
    r = ingest_report(acme_repo_ingested, "HEAD")
    assert ("ADR-0003", "evidence.missing") in pairs(r.rejected) and ("INV-002", "evidence.missing") in pairs(r.rejected)
    assert "ADR-0003" not in r.accepted and "INV-002" not in r.accepted


def test_report_rejects_missing_evidence_even_without_drafted_by(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    (acme_repo_ingested / "kb/invariants/INV-002.md").write_text(INV_500.replace(BY, ""))
    assert ("INV-002", "evidence.missing") in pairs(ingest_report(acme_repo_ingested, "HEAD").rejected)


def test_check_errors_on_agent_drafted_decisions_without_evidence(acme_repo_drafted):
    add_ungrounded(acme_repo_drafted)
    r = invoke(acme_repo_drafted, "check", "--format", "json")
    found = {(i["record"], i["code"], i["severity"]) for i in json.loads(r.stdout)["issues"]}
    assert {("ADR-0003", "evidence.missing", "error"), ("INV-002", "evidence.missing", "error")} <= found


def test_human_authored_decision_without_evidence_is_a_warning(valid_ctx_with):
    from helpers import add_adr

    issues = [i for i in run_gates(valid_ctx_with(add_adr("ADR-0050")), [quote_gate]) if i.record == "ADR-0050"]
    assert [(i.code, i.severity) for i in issues] == [("evidence.missing", "warning")]


def test_superseded_decision_needs_no_evidence(valid_ctx_with):
    from helpers import add_adr

    ctx = valid_ctx_with(add_adr("ADR-0050", drafted_by="agent:x"), add_adr("ADR-0051", supersedes="ADR-0050",
                         evidence={"quotes": [{"source": "SRC-pricing-spec-v3#seg-04", "text": "All amounts are stored and calculated in integer cents."}]}))
    assert "ADR-0050" not in {i.record for i in run_gates(ctx, [quote_gate])}


def test_numbers_in_adr_title_and_body_must_be_quoted(valid_ctx_with):
    from helpers import add_adr

    quote = {"quotes": [{"source": "SRC-pricing-spec-v3#seg-04", "text": "All amounts are stored and calculated in integer cents."}]}
    ctx = valid_ctx_with(add_adr("ADR-0050", title="Keep 3 decimals", evidence=quote))
    (issue,) = [i for i in run_gates(ctx, [quote_gate]) if i.record == "ADR-0050" and i.code != "record.duplicate_quote"]  # it reuses ADR-0002's quote
    assert issue.code == "statement.number_not_in_quote" and "3" in issue.message

    def body_number(files):
        files["kb/decisions/ADR-0051.md"] = (
            b"---\nid: ADR-0051\ntitle: Cents\ngoverns: [MOD-billing]\nevidence:\n  quotes:\n"
            b"  - source: SRC-pricing-spec-v3#seg-04\n    text: All amounts are stored and calculated in integer cents.\n"
            b"---\nRounding happens 2 times per invoice.\n"
        )

    assert codes([i for i in run_gates(valid_ctx_with(body_number), [quote_gate]) if i.record == "ADR-0051" and i.code != "record.duplicate_quote"]) == [
        "statement.number_not_in_quote"
    ]


def test_edited_invariant_is_guarded(valid_git_ctx):
    edit(valid_git_ctx, "kb/invariants/INV-001.md", "Also on weekends.\n")
    issues = run_gates(valid_git_ctx.reload(base="HEAD"), [decision_guard])
    assert [(i.code, i.record) for i in issues] == [("decision_guard", "INV-001")]


def test_edited_committed_invariant_fails_check_with_base(valid_git_repo):
    git(valid_git_repo, "checkout", "-q", "-b", "feature")
    path = valid_git_repo / "kb/invariants/INV-001.md"
    path.write_text(path.read_text().replace("30 minutes", "8 hours"))
    git(valid_git_repo, "commit", "-qam", "relax the invariant")
    r = invoke(valid_git_repo, "check", "--base", "main", "--format", "json")
    assert ("INV-001", "decision_guard") in {(i["record"], i["code"]) for i in json.loads(r.stdout)["issues"]}
