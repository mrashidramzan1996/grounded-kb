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
    (md_repo_ingested / "kb/sources/SRC-n/triage.yaml").write_text("segments:\n  seg-01: 'none: price only'\n")
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


# --- owner and approver answers committed before the drafts (playbook step 8) ---------------------------------

TODO_OWNERS = (
    "version: 1\n"
    "teams:\n  product: {git_team: '@acme/product'}\n"
    "sources:\n  - paths: ['docs/**']\n"
    "modules:\n  - {id: MOD-a, owner: TODO}\n  - {id: MOD-b, owner: '@acme/b'}\n"
    "policy:\n  approvers:\n    intent: [TODO]\n    decisions: [TODO]\n"
)


def _todo_owners(md_repo_ingested):
    """md_repo_ingested whose committed config still has TODO approvers and a TODO module owner."""
    root = md_repo_ingested
    (root / "kb.config.yaml").write_text(TODO_OWNERS)
    git(root, "commit", "-qam", "config with TODOs")
    return root


def _config_codes(root, config: str) -> list[str]:
    (root / "kb.config.yaml").write_text(config)
    return [i.code for i in ingest_report(root, "HEAD").rejected if i.path == "kb.config.yaml"]


def test_filling_todo_approvers_and_owners_is_allowed(md_repo_ingested):
    root = _todo_owners(md_repo_ingested)
    filled = (TODO_OWNERS.replace("intent: [TODO]", "intent: ['@acme/product', '@jane']")
              .replace("decisions: [TODO]", "decisions: [product]")
              .replace("{id: MOD-a, owner: TODO}", "{id: MOD-a, owner: '@acme/a'}"))
    assert _config_codes(root, filled) == []
    added = filled.replace("  - {id: MOD-b, owner: '@acme/b'}\n",
                           "  - {id: MOD-b, owner: '@acme/b'}\n  - {id: MOD-new, owner: '@acme/new'}\n")
    assert _config_codes(root, added) == []  # a module added in the range may have any owner
    r = ingest_report(root, "HEAD")
    assert r.rejected == [] and r.proposed_modules == ["MOD-new"]
    # the CLI agrees: the approvers committed on their own do not fail the report
    assert "ingest.config_changed" not in invoke(root, "ingest", "--report", "--base", "HEAD").stdout


def test_changing_a_set_approver_is_still_rejected(md_repo_ingested):
    root = _todo_owners(md_repo_ingested)
    set_once = TODO_OWNERS.replace("intent: [TODO]", "intent: ['@acme/product']")
    (root / "kb.config.yaml").write_text(set_once)
    git(root, "commit", "-qam", "approver set")
    assert _config_codes(root, set_once.replace("intent: ['@acme/product']", "intent: ['@acme/other']")) == ["ingest.config_changed"]
    assert _config_codes(root, set_once.replace("intent: ['@acme/product']",
                                            "intent: ['@acme/product', '@acme/more']")) == [
        "ingest.config_changed"]
    # a set module owner cannot change either
    assert _config_codes(root, set_once.replace("owner: '@acme/b'", "owner: '@acme/c'")) == ["ingest.config_changed"]


def test_other_config_change_still_rejected(md_repo_ingested):
    root = _todo_owners(md_repo_ingested)
    rejected = ["ingest.config_changed"]
    # a TODO filled with something that is neither a handle nor a team key
    assert _config_codes(root, TODO_OWNERS.replace("intent: [TODO]", "intent: [nobody]")) == rejected
    assert _config_codes(root, TODO_OWNERS.replace("{id: MOD-a, owner: TODO}", "{id: MOD-a, owner: nobody}")) == rejected
    # an emptied approver list, or a TODO left next to a handle, is not a filled-in TODO
    assert _config_codes(root, TODO_OWNERS.replace("intent: [TODO]", "intent: []")) == rejected
    assert _config_codes(root, TODO_OWNERS.replace("intent: [TODO]", "intent: [TODO, '@x']")) == rejected
    # filling a TODO together with any other change
    filled = TODO_OWNERS.replace("intent: [TODO]", "intent: ['@acme/product']")
    assert _config_codes(root, filled.replace("product: {git_team: '@acme/product'}",
                                              "product: {git_team: '@acme/p2'}")) == rejected
    assert _config_codes(root, filled + "  mode: enforce\n") == rejected
    assert _config_codes(root, filled.replace("{id: MOD-a, owner: TODO}", "{id: MOD-a, owner: TODO, risk: high}")) == rejected


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


def test_deleted_source_store_is_tampered(md_repo_ingested):
    import shutil

    shutil.rmtree(md_repo_ingested / "kb/sources/SRC-n")
    assert ("SRC-n", "source.tampered") in _pairs(ingest_report(md_repo_ingested, "HEAD").rejected)


# --- I1: proposed modules carry no paths --------------------------------------------------------

def test_proposed_module_with_guessed_paths_is_rejected(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    cfg = acme_repo_ingested / "kb.config.yaml"
    cfg.write_text(cfg.read_text().replace('{id: MOD-web, owner: "@acme/web", risk: low}',
                                           '{id: MOD-web, owner: "@acme/web", risk: low, paths: ["web/**"]}'))
    r = ingest_report(acme_repo_ingested, "HEAD")
    (issue,) = [i for i in r.rejected if i.code == "ingest.module_paths"]
    assert issue.record == "MOD-web" and "kb module paths" in issue.message and r.failed


# --- I7: the report compares against the commit kb ingest ran on ---------------------------------

def test_ingest_records_its_base_and_the_brief_names_it(md_repo_ingested):
    head = git(md_repo_ingested, "rev-parse", "HEAD").strip()
    assert invoke(md_repo_ingested, "ingest").exit_code == 0
    assert (md_repo_ingested / ".kb/cache/ingest/base").read_text().strip() == head
    brief = (md_repo_ingested / ".kb/cache/ingest/brief.md").read_text()
    assert f"run `kb ingest --report` before committing; it compares against {head}" in brief


def test_report_still_sees_drafts_after_they_are_committed(md_repo_ingested):
    assert invoke(md_repo_ingested, "ingest").exit_code == 0
    (md_repo_ingested / "kb/requirements").mkdir()
    (md_repo_ingested / "kb/requirements/REQ-F-001.yaml").write_text(
        "id: REQ-F-001\nstatement: A seat costs 5 EUR.\nmodules: [MOD-seats]\n"
        "evidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: A seat costs 5 EUR.\n"
    )
    git(md_repo_ingested, "add", "-A")
    git(md_repo_ingested, "commit", "-m", "drafts")
    out = invoke(md_repo_ingested, "ingest", "--report")
    assert out.exit_code == 0 and "nothing drafted" not in out.stderr, out.output
    assert "REQ-F-001" in out.stdout  # rejected (MOD-seats is not configured), but reported


def test_nothing_drafted_warns_and_fails_strict(md_repo_ingested):
    assert invoke(md_repo_ingested, "ingest").exit_code == 0
    head = git(md_repo_ingested, "rev-parse", "HEAD").strip()
    out = invoke(md_repo_ingested, "ingest", "--report")
    assert out.exit_code == 0 and f"nothing drafted since {head}" in out.stderr
    assert invoke(md_repo_ingested, "ingest", "--report", "--strict").exit_code == 1


@pytest.mark.parametrize("content", ["0" * 40, "not a sha\n", ""])
def test_unusable_recorded_base_falls_back_to_head(md_repo_ingested, content):
    assert invoke(md_repo_ingested, "ingest").exit_code == 0
    (md_repo_ingested / ".kb/cache/ingest/base").write_text(content)
    head = git(md_repo_ingested, "rev-parse", "HEAD").strip()
    assert f"nothing drafted since {head}" in invoke(md_repo_ingested, "ingest", "--report").stderr


def test_recorded_base_that_is_not_an_ancestor_is_ignored(md_repo_ingested):
    git(md_repo_ingested, "checkout", "-q", "-b", "other")
    git(md_repo_ingested, "commit", "--allow-empty", "-qm", "elsewhere")
    assert invoke(md_repo_ingested, "ingest").exit_code == 0
    git(md_repo_ingested, "checkout", "-q", "main")
    head = git(md_repo_ingested, "rev-parse", "HEAD").strip()
    assert f"nothing drafted since {head}" in invoke(md_repo_ingested, "ingest", "--report").stderr


def test_drafted_number_mismatch_is_rejected_in_report(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    p = acme_repo_ingested / "kb/invariants/INV-001.md"
    p.write_text(p.read_text().replace("A session expires after 30 minutes without activity.\n", "A session expires after 45 minutes without activity.\n", 1))
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert ("INV-001", "statement.number_not_in_quote") in _pairs(r.rejected) and "INV-001" not in r.accepted


def _strip_drafted_by(path):
    path.write_text("\n".join(l for l in path.read_text().splitlines() if not l.startswith("drafted_by")) + "\n")


def test_number_mismatch_without_drafted_by_stays_warning(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    p = acme_repo_ingested / "kb/invariants/INV-001.md"
    p.write_text(p.read_text().replace("A session expires after 30 minutes without activity.\n", "A session expires after 45 minutes without activity.\n", 1))
    _strip_drafted_by(p)
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert ("INV-001", "statement.number_not_in_quote") in _pairs(r.flagged) and "INV-001" in r.accepted


def _shorten_req_f_002(root):
    p = root / "kb/requirements/REQ-F-002.yaml"
    p.write_text(p.read_text().replace('text: "No refund is given."', 'text: "No refund."'))
    return p


def test_drafted_too_short_quote_is_rejected_in_report(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    _shorten_req_f_002(acme_repo_ingested)
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert ("REQ-F-002", "quote.too_short") in _pairs(r.rejected)


def test_too_short_quote_without_drafted_by_stays_warning(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    _strip_drafted_by(_shorten_req_f_002(acme_repo_ingested))
    r = ingest_report(acme_repo_ingested, base="HEAD")
    assert ("REQ-F-002", "quote.too_short") in _pairs(r.flagged)
