import pytest
from helpers import adr_md, git, invoke, put, req_yaml

from kb.ingest.redraft import REDRAFT_BASE
from kb.ingest.redraft_report import RedraftReport, redraft_report, render_redraft_report
from kb.issues import Issue

DOC = "# Seats\n\nA seat costs 5 EUR.\n\n# Speed\n\nPages load fast.\n"
VAGUE = "Pages load fast."
TARGET = "Pages load in 2 seconds."
CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-a\n    owner: TODO\n'


def commit(root, message: str = "change") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def sync(root, text: str) -> None:
    put(root, "docs/n.md", text)
    assert invoke(root, "source", "sync").exit_code == 0


@pytest.fixture
def kb(md_repo_ingested):
    """REQ-F-001 (a price), REQ-NF-001 (vague), ADR-0001, all quoting the doc, committed."""
    root = md_repo_ingested
    put(root, "kb.config.yaml", CONFIG)
    sync(root, DOC)
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(root, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", VAGUE, flags=("vague",), seg="seg-02"))
    put(root, "kb/decisions/ADR-0001.md", adr_md("ADR-0001"))
    commit(root, "drafts")
    return root


def redraft(root, *args: str) -> None:
    r = invoke(root, "redraft", *args)
    assert r.exit_code == 0, r.stderr


def rejected(r: RedraftReport) -> list[tuple[str | None, str]]:
    return [(i.record, i.code) for i in r.rejected]


def test_good_redraft_accepted_and_vague_cleared(kb):
    sync(kb, DOC.replace(VAGUE, TARGET))
    redraft(kb, "--changed")
    put(kb, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", TARGET, seg="seg-02"))
    r = redraft_report(kb, None)
    assert r.base == git(kb, "rev-parse", "HEAD").strip()
    assert (r.selected, r.redrafted, r.rejected, r.still_flagged, r.unchanged) == (["REQ-NF-001"], ["REQ-NF-001"], [], [], [])
    assert r.flagged == [] and r.superseding == []


def test_vague_cleared_without_target_rejected(kb):
    redraft(kb, "REQ-NF-001")
    put(kb, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", VAGUE, seg="seg-02"))
    r = redraft_report(kb, None)
    assert rejected(r) == [("REQ-NF-001", "vague.cleared_without_target")]
    assert r.rejected[0].message == "REQ-NF-001: vague was removed but no quote states a measurable target"
    assert r.redrafted == []


def test_vague_removed_on_rejected_requirement_is_fine(kb):
    redraft(kb, "REQ-NF-001")
    put(kb, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", VAGUE, seg="seg-02") + "status: rejected\n")
    r = redraft_report(kb, None)
    assert (r.rejected, r.redrafted) == ([], ["REQ-NF-001"])


def test_vague_kept_when_doc_still_vague_is_still_flagged_not_rejected(kb):
    redraft(kb, "--flagged")
    r = redraft_report(kb, None)
    assert (r.rejected, r.redrafted, r.still_flagged, r.unchanged) == ([], [], ["REQ-NF-001"], ["REQ-NF-001"])


def test_other_flag_removed_is_a_warning(kb):
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001", flags=("conflicting",)))
    commit(kb)
    redraft(kb, "REQ-F-001")
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    r = redraft_report(kb, None)
    assert (r.rejected, r.redrafted) == ([], ["REQ-F-001"])
    assert [(i.record, i.code, i.severity, i.message) for i in r.flagged] == [
        ("REQ-F-001", "flag.cleared", "warning", "REQ-F-001: conflicting was removed; reviewer to confirm")
    ]


def test_out_of_scope_edit_rejected(kb):
    redraft(kb, "REQ-NF-001")
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001").replace("statement: 'A seat", "statement: 'One seat"))
    put(kb, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002"))
    r = redraft_report(kb, None)
    assert rejected(r) == [("REQ-F-001", "redraft.out_of_scope"), ("REQ-F-002", "redraft.out_of_scope")]
    assert r.rejected[0].message == "REQ-F-001 is not in this redraft; redraft only the listed records"
    assert r.unchanged == ["REQ-NF-001"]


def test_deleted_selected_record_rejected(kb):
    redraft(kb, "REQ-F-001")
    (kb / "kb/requirements/REQ-F-001.yaml").unlink()
    r = redraft_report(kb, None)
    assert rejected(r) == [("REQ-F-001", "redraft.id_changed")]
    assert r.rejected[0].message == "REQ-F-001 was deleted or renamed; a redraft keeps ids (set status: rejected instead)"
    assert (r.redrafted, r.unchanged) == ([], [])


def test_renamed_selected_record_rejected(kb):
    redraft(kb, "REQ-F-001")
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-009"))
    assert ("REQ-F-001", "redraft.id_changed") in rejected(redraft_report(kb, None))


def test_adr_edited_in_place_rejected(kb):
    redraft(kb, "ADR-0001")
    put(kb, "kb/decisions/ADR-0001.md", adr_md("ADR-0001").replace("Body.", "Another body."))
    r = redraft_report(kb, None)
    assert rejected(r) == [("ADR-0001", "decision_guard")]
    assert r.redrafted == []


def test_superseding_adr_accepted(kb):
    redraft(kb, "ADR-0001")
    put(kb, "kb/decisions/ADR-0002.md", adr_md("ADR-0002", supersedes="ADR-0001"))
    r = redraft_report(kb, None)
    assert (r.rejected, r.superseding, r.redrafted, r.unchanged) == ([], ["ADR-0002"], [], [])
    assert "- ADR-0002 (supersedes a selected record)" in render_redraft_report(r)


def test_invented_quote_rejected(kb):
    redraft(kb, "REQ-F-001")
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001", "A seat costs 6 EUR."))
    r = redraft_report(kb, None)
    assert ("REQ-F-001", "quote.not_found") in rejected(r) and r.redrafted == []


def test_config_change_rejected(kb):
    redraft(kb, "REQ-F-001")
    put(kb, "kb.config.yaml", CONFIG + "  - id: MOD-b\n    owner: TODO\n")
    r = redraft_report(kb, None)
    assert [(i.code, i.path, i.message) for i in r.rejected] == [
        ("ingest.config_changed", "kb.config.yaml", "kb.config.yaml changed; a redraft does not change the config")
    ]


def test_store_files_from_sync_are_not_drafted(kb):
    sync(kb, DOC.replace(VAGUE, TARGET))
    redraft(kb, "--changed")
    r = redraft_report(kb, None)
    assert (r.rejected, r.redrafted, r.unchanged) == ([], [], ["REQ-NF-001"])


def test_tampered_store_text_rejected(kb):
    redraft(kb, "REQ-F-001")
    text = kb / "kb/sources/SRC-n/text.md"
    text.write_text(text.read_text().replace("A seat costs 5 EUR.", "A seat costs 6 EUR."))
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001", "A seat costs 6 EUR."))
    r = redraft_report(kb, None)
    assert ("SRC-n", "source.tampered") in rejected(r)
    assert ("REQ-F-001", "source.tampered") in rejected(r) and r.redrafted == []
    assert any(i.code == "source.tampered" for i in r.sources)
    assert "## Sources (" in render_redraft_report(r) and "source.tampered" in render_redraft_report(r)


def test_sync_from_original_is_not_tampering(kb):
    sync(kb, DOC.replace(VAGUE, TARGET))
    redraft(kb, "--changed")
    put(kb, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", TARGET, seg="seg-02"))
    r = redraft_report(kb, None)
    assert (r.rejected, r.sources, r.redrafted) == ([], [], ["REQ-NF-001"])


def test_original_edited_without_sync_rejected(kb):
    redraft(kb, "REQ-NF-001")
    put(kb, "docs/n.md", DOC.replace(VAGUE, TARGET))
    r = redraft_report(kb, None)
    assert ("SRC-n", "source.outdated") in rejected(r) and ("REQ-NF-001", "source.outdated") in rejected(r)


def test_default_base_is_the_recorded_one(kb):
    redraft(kb, "REQ-NF-001")
    recorded = (kb / REDRAFT_BASE).read_text().strip()
    put(kb, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", VAGUE, flags=("vague",), seg="seg-02") + "drafted_by: agent:x via kb redraft brief v1\n")
    commit(kb, "redraft")
    r = redraft_report(kb, None)
    assert r.base == recorded and r.redrafted == ["REQ-NF-001"]
    assert redraft_report(kb, "HEAD").redrafted == []
    (kb / REDRAFT_BASE).write_text("0" * 40 + "\n")
    assert redraft_report(kb, None).base == git(kb, "rev-parse", "HEAD").strip()


def test_render_sections_in_order():
    r = RedraftReport(
        base="abc", selected=["REQ-F-001", "REQ-F-002", "REQ-NF-001"], redrafted=["REQ-F-001"], superseding=[],
        rejected=[Issue("quote.not_found", "bad `quote`", path="kb/requirements/REQ-F-002.yaml", record="REQ-F-002")],
        flagged=[], unchanged=["REQ-NF-001"], still_flagged=["REQ-NF-001"],
    )
    text = render_redraft_report(r)
    assert text.startswith("# Redraft report\n")
    heads = [line for line in text.splitlines() if line.startswith("## ")]
    assert heads == [
        "## Redrafted (1)", "## Rejected (1)", "## Flagged (0)", "## Sources (0)", "## Still flagged (1)", "## Not redrafted (1)",
    ]
    assert "- `REQ-F-002` · `quote.not_found` · `` bad `quote` ``" in text
    assert render_redraft_report(r) == text


def test_strict_exit_1_on_rejection(kb):
    redraft(kb, "REQ-F-001")
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001", "A seat costs 6 EUR."))
    out = invoke(kb, "redraft", "--report")
    assert out.exit_code == 0 and out.stdout.startswith("# Redraft report") and "quote.not_found" in out.stdout
    assert invoke(kb, "redraft", "--report", "--strict").exit_code == 1
    put(kb, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    assert invoke(kb, "redraft", "--report", "--strict").exit_code == 0


def test_report_without_prior_redraft_exits_2(kb):
    out = invoke(kb, "redraft", "--report")
    assert out.exit_code == 2 and "run `kb redraft` first" in out.stderr


@pytest.mark.parametrize("args", [["--report", "REQ-F-001"], ["--report", "--flagged"], ["--report", "--changed"], ["--strict", "REQ-F-001"]])
def test_report_usage_errors(kb, args):
    out = invoke(kb, "redraft", *args)
    assert out.exit_code == 2 and out.stderr


def test_report_with_unknown_base_exits_2(kb):
    redraft(kb, "REQ-F-001")
    out = invoke(kb, "redraft", "--report", "--base", "no-such-ref")
    assert out.exit_code == 2 and "no-such-ref" in out.stderr


def test_report_ignores_malformed_ids_file(kb):
    redraft(kb, "REQ-F-001")
    (kb / ".kb/cache/redraft/ids").write_text("REQ-F-001\n`rm -rf`\n")
    out = invoke(kb, "redraft", "--report")
    assert out.exit_code == 2 and "run `kb redraft` first" in out.stderr

