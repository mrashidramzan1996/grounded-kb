from helpers import apply_drafts, git, invoke

from kb.ingest.report import ingest_report

DRAFTED = ["ADR-0001", "ADR-0002", "INV-001", "REQ-F-001", "REQ-F-002", "REQ-F-003", "REQ-F-004", "REQ-NF-001", "REQ-NF-002"]


def test_report_on_simulated_agent_drafts(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert [i.record for i in r.rejected] == ["REQ-F-005"] and r.rejected[0].code == "quote.not_found"
    assert [i.record for i in r.flagged if i.code == "requirement.vague"] == ["REQ-NF-001"]
    assert len(r.untriaged) == 1 and "Contact" in r.untriaged[0]
    assert r.untriaged == ["SRC-product-brief#seg-06 · Acme Billing - Product Brief > 5. Contact"]
    assert sorted(r.proposed_modules) == ["MOD-billing", "MOD-sessions", "MOD-web"]
    assert {"REQ-F-001", "REQ-F-002", "ADR-0001", "INV-001"} <= set(r.accepted)
    assert r.accepted == DRAFTED
    assert all(i.severity == "warning" for i in r.flagged) and all(i.severity == "error" for i in r.rejected)


def test_default_base_is_head(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    assert ingest_report(acme_repo_ingested, None) == ingest_report(acme_repo_ingested, "HEAD")


def test_unchanged_records_are_not_reported(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    git(acme_repo_ingested, "add", "-A")
    git(acme_repo_ingested, "commit", "-m", "drafts")
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert (r.accepted, r.rejected, r.flagged, r.proposed_modules) == ([], [], [], [])
    assert len(r.untriaged) == 1  # coverage is reported whatever changed
    assert [i.record for i in ingest_report(acme_repo_ingested, base="HEAD~1").rejected] == ["REQ-F-005"]


def test_changed_record_is_reported_again(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    git(acme_repo_ingested, "add", "-A")
    git(acme_repo_ingested, "commit", "-m", "drafts")
    path = acme_repo_ingested / "kb/requirements/REQ-F-002.yaml"
    path.write_text(path.read_text().replace("No refund is given.", "No refund is ever given."))
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert r.accepted == [] and [(i.record, i.code) for i in r.rejected] == [("REQ-F-002", "quote.not_found")]


def test_unparseable_new_file_is_rejected(acme_repo_ingested):
    (acme_repo_ingested / "kb/requirements").mkdir(parents=True)
    (acme_repo_ingested / "kb/requirements/REQ-F-009.yaml").write_text("id: REQ-F-009\nstatement: no evidence\n")
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert [(i.code, i.path) for i in r.rejected] == [("schema", "kb/requirements/REQ-F-009.yaml")]


def test_triage_naming_missing_record_is_rejected(acme_repo_ingested):
    (acme_repo_ingested / "kb/sources/SRC-product-brief/triage.yaml").write_text("segments:\n  seg-03: [REQ-F-404]\n")
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert [(i.code, i.path) for i in r.rejected] == [("ref.missing", "kb/sources/SRC-product-brief/triage.yaml")]


def test_cli_report_prints_markdown(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    out = invoke(acme_repo_ingested, "ingest", "--report")
    assert out.exit_code == 0, out.output
    text = out.stdout
    for needle in ("# Ingest report", "## Accepted (9)", "## Rejected (1)", "REQ-F-005", "quote.not_found",
                   "requirement.vague", "## Untriaged segments (1)", "5. Contact", "## Proposed modules (3)", "MOD-web"):
        assert needle in text, needle
    assert invoke(acme_repo_ingested, "ingest", "--report", "--strict").exit_code == 1


def test_cli_strict_passes_without_rejections(acme_repo_ingested):
    out = invoke(acme_repo_ingested, "ingest", "--report", "--strict")
    assert out.exit_code == 0, out.output


def test_cli_report_base_option(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    git(acme_repo_ingested, "add", "-A")
    git(acme_repo_ingested, "commit", "-m", "drafts")
    assert invoke(acme_repo_ingested, "ingest", "--report", "--strict").exit_code == 0
    assert invoke(acme_repo_ingested, "ingest", "--report", "--strict", "--base", "HEAD~1").exit_code == 1


def test_cli_bad_base_exits_2(acme_repo_ingested):
    out = invoke(acme_repo_ingested, "ingest", "--report", "--base", "no-such-ref")
    assert out.exit_code == 2 and "no-such-ref" in out.stderr


def test_cli_report_with_paths_is_usage_error(acme_repo_ingested):
    assert invoke(acme_repo_ingested, "ingest", "--report", "docs/").exit_code == 2
