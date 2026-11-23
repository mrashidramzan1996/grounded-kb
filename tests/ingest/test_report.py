import json

import pytest
from helpers import adr_bytes, apply_drafts, git, invoke

from kb.ingest.report import IngestReport, ingest_report, render_report
from kb.issues import Issue

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


def test_cli_strict_passes_when_clean(md_repo_ingested):
    out = invoke(md_repo_ingested, "ingest", "--report", "--strict")
    assert out.exit_code == 0, out.output


def test_cli_report_base_option(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    git(acme_repo_ingested, "add", "-A")
    git(acme_repo_ingested, "commit", "-m", "drafts")
    assert "## Rejected (0)" in invoke(acme_repo_ingested, "ingest", "--report").stdout
    assert "## Rejected (1)" in invoke(acme_repo_ingested, "ingest", "--report", "--base", "HEAD~1").stdout


def test_cli_bad_base_exits_2(acme_repo_ingested):
    out = invoke(acme_repo_ingested, "ingest", "--report", "--base", "no-such-ref")
    assert out.exit_code == 2 and "no-such-ref" in out.stderr


def test_cli_report_with_paths_is_usage_error(acme_repo_ingested):
    assert invoke(acme_repo_ingested, "ingest", "--report", "docs/").exit_code == 2


def _committed_drafts(repo, fixtures):
    apply_drafts(repo, fixtures / "drafts/acme-billing")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "drafts")
    return repo


def _pairs(issues):
    return [(i.record, i.code) for i in issues]


# --- finding 1: accepted decisions, deletions, config ------------------------------------------

def test_edited_adr_is_rejected(acme_repo_ingested, fixtures):
    root = _committed_drafts(acme_repo_ingested, fixtures)
    p = root / "kb/decisions/ADR-0001.md"
    p.write_text(p.read_text().replace("Redis and", "Redis, Mongo and"))
    r = ingest_report(root, "HEAD")
    assert _pairs(r.rejected) == [("ADR-0001", "decision_guard")] and "ADR-0001" not in r.accepted
    assert invoke(root, "ingest", "--report", "--strict").exit_code == 1


def test_deleted_adr_is_rejected(acme_repo_ingested, fixtures):
    root = _committed_drafts(acme_repo_ingested, fixtures)
    (root / "kb/decisions/ADR-0002.md").unlink()
    r = ingest_report(root, "HEAD")
    assert ("ADR-0002", "decision_guard") in _pairs(r.rejected)


def test_deleted_requirement_still_named_by_triage_is_rejected(acme_repo_ingested, fixtures):
    root = _committed_drafts(acme_repo_ingested, fixtures)
    (root / "kb/requirements/REQ-F-003.yaml").unlink()
    r = ingest_report(root, "HEAD")
    assert _pairs(r.rejected) == [("SRC-pricing-spec-v3", "ref.missing")] and r.caused == r.rejected
    assert "REQ-F-003" in r.rejected[0].message


def test_config_change_outside_modules_is_rejected(acme_repo_ingested, fixtures):
    root = _committed_drafts(acme_repo_ingested, fixtures)
    cfg = root / "kb.config.yaml"
    text = cfg.read_text()
    cfg.write_text(text.replace("intent: [product-owners]", "intent: [tech-leads]"))
    assert [(i.code, i.path) for i in ingest_report(root, "HEAD").rejected] == [("ingest.config_changed", "kb.config.yaml")]
    cfg.write_text(text.replace('owner: "@acme/web"', 'owner: "@acme/other"'))
    assert [i.code for i in ingest_report(root, "HEAD").rejected] == ["ingest.config_changed"]
    cfg.write_text(text + "  - {id: MOD-tax, owner: TODO}\n")
    r = ingest_report(root, "HEAD")
    assert r.rejected == [] and r.proposed_modules == ["MOD-tax"]


# --- finding 2: errors a draft causes on unchanged records -------------------------------------

def test_conflict_caused_on_unchanged_target_is_rejected(acme_repo_ingested, fixtures):
    root = _committed_drafts(acme_repo_ingested, fixtures)
    (root / "kb/decisions/ADR-0003.md").write_bytes(adr_bytes("ADR-0003", supersedes="ADR-0001"))
    git(root, "add", "-A")
    git(root, "commit", "-m", "ADR-0003 supersedes ADR-0001")
    (root / "kb/decisions/ADR-0004.md").write_bytes(adr_bytes("ADR-0004", supersedes="ADR-0001"))
    r = ingest_report(root, "HEAD")
    assert _pairs(r.caused) == [("ADR-0001", "supersede.conflict")] and r.caused[0] in r.rejected
    assert "### Caused by this change (1)" in render_report(r)
    assert invoke(root, "ingest", "--report", "--strict").exit_code == 1


# --- finding 5: tampered source store ----------------------------------------------------------

def test_hand_edited_source_text_is_tampered(acme_repo_ingested, fixtures):
    root = acme_repo_ingested
    text = root / "kb/sources/SRC-pricing-spec-v3/text.md"
    old = "Annual billing is available"
    text.write_text(text.read_text().replace(old, "Annual plans get two months free. " + old))
    apply_drafts(root, fixtures / "drafts/acme-billing")
    r = ingest_report(root, "HEAD")
    assert ("SRC-pricing-spec-v3", "source.tampered") in _pairs(r.rejected)
    assert ("REQ-F-005", "source.tampered") in _pairs(r.rejected)
    assert ("REQ-F-005", "quote.not_found") not in _pairs(r.rejected)  # the edit made the quote "match"
    assert "REQ-F-005" not in r.accepted and "REQ-F-001" not in r.accepted  # every citer of the source
    assert ("SRC-pricing-spec-v3", "source.tampered") in _pairs(r.sources)
    assert invoke(root, "ingest", "--report", "--strict").exit_code == 1


def test_edited_segments_are_tampered(md_repo_ingested):
    seg = md_repo_ingested / "kb/sources/SRC-n/segments.jsonl"
    seg.write_text(seg.read_text().replace('"seg-01"', '"seg-07"'))
    assert ("SRC-n", "source.tampered") in _pairs(ingest_report(md_repo_ingested, "HEAD").rejected)


ORIGINAL_CHANGED = "the source document changed in the same change as the drafts; commit document updates separately, before kb ingest"


def test_edited_original_plus_sync_is_rejected(md_repo_ingested):
    """The store matches the edited original, so it is not tampered, but the original itself moved."""
    (md_repo_ingested / "docs/n.md").write_text("# Seats\n\nA seat costs 5 EUR. Annual plans get two months free.\n")
    assert invoke(md_repo_ingested, "source", "sync").exit_code == 0
    r = ingest_report(md_repo_ingested, "HEAD")
    assert [(i.record, i.code, i.message) for i in r.sources] == [("SRC-n", "ingest.original_changed", ORIGINAL_CHANGED)]
    assert r.failed and invoke(md_repo_ingested, "ingest", "--report", "--strict").exit_code == 1


def test_original_committed_with_its_sync_is_fine(md_repo_ingested):
    (md_repo_ingested / "docs/n.md").write_text("# Seats\n\nA seat costs 6 EUR.\n")
    assert invoke(md_repo_ingested, "source", "sync").exit_code == 0
    git(md_repo_ingested, "add", "-A")
    git(md_repo_ingested, "commit", "-m", "doc update")
    r = ingest_report(md_repo_ingested, "HEAD")
    assert (r.rejected, r.sources) == ([], [])


def test_tamper_message_says_how_to_recover(md_repo_ingested):
    text = md_repo_ingested / "kb/sources/SRC-n/text.md"
    text.write_text(text.read_text() + "Extra.\n")
    (issue,) = [i for i in ingest_report(md_repo_ingested, "HEAD").rejected if i.code == "source.tampered"]
    assert "restore the store files from HEAD and run `kb source sync` once" in issue.message


@pytest.mark.parametrize("bad_id", ["seg-01\n## Accepted (99)\n- 1", "seg-xx", "seg-1"])
def test_invalid_segment_ids_are_tampered_not_printed(acme_repo_ingested, fixtures, bad_id):
    seg = acme_repo_ingested / "kb/sources/SRC-product-brief/segments.jsonl"
    lines = seg.read_text().splitlines()
    obj = json.loads(lines[-1])
    obj["id"] = bad_id
    seg.write_text("\n".join(lines[:-1] + [json.dumps(obj)]) + "\n")
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    r = ingest_report(acme_repo_ingested, "HEAD")
    assert ("SRC-product-brief", "source.tampered") in _pairs(r.rejected)
    assert "REQ-F-004" not in r.accepted  # cites the tampered source
    assert not any(u.startswith("SRC-product-brief") for u in r.untriaged)
    assert "\n## Accepted (99)" not in render_report(r)


def test_new_source_from_kb_ingest_is_not_tampering(md_repo_ingested):
    (md_repo_ingested / "docs/m.md").write_text("# Other\n\nText.\n")
    assert invoke(md_repo_ingested, "ingest").exit_code == 0
    r = ingest_report(md_repo_ingested, "HEAD")
    assert (r.rejected, r.sources) == ([], [])


# --- finding 6: source issues ------------------------------------------------------------------

def test_source_issues_are_reported_and_fail_strict(acme_repo_ingested):
    r = ingest_report(acme_repo_ingested, "HEAD")
    assert ("SRC-architecture-notes", "source.secret_in_original") in _pairs(r.sources) and r.rejected == []
    out = invoke(acme_repo_ingested, "ingest", "--report", "--strict")
    assert out.exit_code == 1 and "## Sources (1)" in out.stdout


def test_outdated_source_fails_strict(md_repo_ingested):
    (md_repo_ingested / "docs/n.md").write_text("# Seats\n\nChanged.\n")
    assert ("SRC-n", "source.outdated") in _pairs(ingest_report(md_repo_ingested, "HEAD").sources)
    assert invoke(md_repo_ingested, "ingest", "--report", "--strict").exit_code == 1


# --- finding 4: untrusted text in the report stays inline code ---------------------------------

def test_report_wraps_untrusted_text_in_inline_code():
    evil = "x ```\n## Accepted (99)\n- REQ-F-999"
    r = IngestReport([], [Issue("quote.not_found", evil, path="kb/requirements/REQ-F-1.yaml", record="REQ-F-001")], [],
                     ["SRC-a#seg-01 · `odd` > heading", "SRC-a#seg-02 · Top > `odd` heading"], [])
    out = render_report(r)
    assert "\n## Accepted (99)" not in out and "\n- REQ-F-999" not in out
    assert "````x ``` ## Accepted (99) - REQ-F-999````" in out
    assert "- `SRC-a#seg-01` · `` `odd` > heading ``" in out and "- `SRC-a#seg-02` · ``Top > `odd` heading``" in out


def test_inline_code_of_empty_text():
    from kb.ingest.brief import inline_code

    assert inline_code("") == "(empty)"
