"""Requirement classification fields against the quote."""

import json

from helpers import apply_drafts, codes, commit, git, invoke, make_git_repo, req

from kb.gates.base import run_gates
from kb.gates.quotes import quote_gate
from kb.ingest.report import ingest_report

SPEC = "pricing-spec-v3"
NF = "5. Non-functional requirements"
MUST = (SPEC, NF, "The checkout page must be fast.")
FIELDS = {"modality": "must", "actor": "the checkout page", "category": "performance"}


def gate(acme_kb_with, *records):
    return run_gates(acme_kb_with(*records), [quote_gate])


def test_fields_missing_is_warning_in_check_error_in_report(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    path = acme_repo_ingested / "kb/requirements/REQ-NF-001.yaml"
    path.write_text("".join(l for l in path.read_text().splitlines(keepends=True) if not l.startswith(("modality", "actor", "category"))))
    data = json.loads(invoke(acme_repo_ingested, "check", "--format", "json").stdout)
    missing = [i for i in data["issues"] if i["code"] == "requirement.fields_missing"]
    assert [(i["record"], i["severity"]) for i in missing] == [("REQ-NF-001", "warning")]
    assert "modality" in missing[0]["message"] and "category" in missing[0]["message"]
    r = ingest_report(acme_repo_ingested, "HEAD")
    assert ("REQ-NF-001", "requirement.fields_missing") in [(i.record, i.code) for i in r.rejected]
    assert "REQ-NF-001" not in r.accepted


def test_category_missing_on_nf_only(acme_kb_with):
    issues = gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, modality="must", actor="the checkout page", category=None))
    assert codes(issues) == ["requirement.fields_missing"] and "category" in issues[0].message
    assert gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS)) == []


def test_modality_mismatch(acme_kb_with):
    issues = gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS | {"modality": "should"}))
    assert codes(issues) == ["requirement.modality_mismatch"] and issues[0].severity == "warning"
    assert gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS)) == []


def test_modality_mismatch_any_cue_matches(acme_kb_with, monkeypatch):
    import kb.gates.quotes as q

    monkeypatch.setattr(q, "cues", lambda text: frozenset({"must", "may"}))
    assert gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS | {"modality": "may"})) == []


def test_implied_needs_clarification(acme_kb_with):
    issues = gate(acme_kb_with, req("REQ-NF-001", "The checkout page must be fast.", MUST, **FIELDS, origin="implied"))
    assert codes(issues) == ["requirement.implied_unconfirmed"] and issues[0].severity == "warning"


# --- real quotes, no monkeypatching -----------------------------------------------------------

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-a\n    owner: "@alice"\n'


def _issues(root):
    from kb.config.load import load_config
    from kb.gates.base import GateContext
    from kb.records.repo import KbRepo

    return run_gates(GateContext(root, load_config(root / "kb.config.yaml"), KbRepo.load(root)), [quote_gate])


def _check(tmp_path, quote, modality, rid="REQ-F-001", origin="stated", extra_quotes="", mode=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/n.md": f"# Rules\n\n{quote}\n"})
    assert invoke(root, "ingest").exit_code == 0
    mode_line = f"  mode: {mode}\n" if mode else ""
    (root / "kb/requirements").mkdir(parents=True, exist_ok=True)
    (root / f"kb/requirements/{rid}.yaml").write_text(
        f"id: {rid}\nstatement: {quote!r}\nmodality: {modality}\nactor: the system\norigin: {origin}\n"
        f"evidence:\n{mode_line}  quotes:\n  - source: SRC-n#seg-01\n    text: {quote!r}\n{extra_quotes}modules:\n- MOD-a\n")
    return _issues(root), root


def _mismatch(issues):
    return [i.code for i in issues if i.code.startswith("requirement.")]


def test_must_vs_should(tmp_path):
    q = "Seats must be removed at the end of the cycle."
    assert _mismatch(_check(tmp_path / "a", q, "must")[0]) == []
    assert _mismatch(_check(tmp_path / "b", q, "should")[0]) == ["requirement.modality_mismatch"]


def test_must_not_is_not_must(tmp_path):
    q = "Seats must not be removed."
    assert _mismatch(_check(tmp_path / "a", q, "must")[0]) == ["requirement.modality_mismatch"]
    assert _mismatch(_check(tmp_path / "b", q, "must_not")[0]) == []


def test_shall_not_is_not_must(tmp_path):
    assert _mismatch(_check(tmp_path, "Seats shall not be removed.", "must")[0]) == ["requirement.modality_mismatch"]


def test_should_and_may(tmp_path):
    assert _mismatch(_check(tmp_path / "a", "Users should export data.", "should")[0]) == []
    assert _mismatch(_check(tmp_path / "b", "Users may export data.", "may")[0]) == []


def test_several_cues_any_matches(tmp_path):
    q = "Admins must approve and users may comment."
    assert _mismatch(_check(tmp_path / "a", q, "may")[0]) == []
    assert _mismatch(_check(tmp_path / "b", q, "must")[0]) == []


def _clarified(tmp_path, found: bool):
    from kb.clarify import clarify

    q = "Users must export data."
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/n.md": f"# Rules\n\n{q}\n"})
    _, ref = clarify(root, "Export", "Users must export data on request.", "@alice", "2026-11-25")
    assert invoke(root, "ingest").exit_code == 0
    text = "Users must export data on request." if found else "Users must never export data."
    (root / "kb/requirements").mkdir(parents=True, exist_ok=True)
    (root / "kb/requirements/REQ-F-001.yaml").write_text(
        f"id: REQ-F-001\nstatement: {q!r}\nmodality: must\nactor: the system\norigin: implied\n"
        f"evidence:\n  mode: any\n  quotes:\n  - source: SRC-n#seg-01\n    text: {q!r}\n"
        f"  - source: {ref}\n    text: {text!r}\nmodules:\n- MOD-a\n")
    return [i.code for i in _issues(root) if i.code == "requirement.implied_unconfirmed"]


def test_found_clarification_quote_confirms_implied(tmp_path):
    assert _clarified(tmp_path, found=True) == []


def test_unfound_clarification_quote_does_not_confirm_implied(tmp_path):
    assert _clarified(tmp_path, found=False) == ["requirement.implied_unconfirmed"]


def _strip(path, *prefixes):
    path.write_text("".join(l for l in path.read_text().splitlines(keepends=True) if not l.startswith(prefixes)))


def test_modality_mismatch_is_error_in_report_warning_in_check(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    path = acme_repo_ingested / "kb/requirements/REQ-NF-001.yaml"
    path.write_text(path.read_text().replace("modality: must", "modality: may"))
    data = json.loads(invoke(acme_repo_ingested, "check", "--format", "json").stdout)
    assert [(i["record"], i["severity"]) for i in data["issues"] if i["code"] == "requirement.modality_mismatch"] == [("REQ-NF-001", "warning")]
    r = ingest_report(acme_repo_ingested, "HEAD")
    assert ("REQ-NF-001", "requirement.modality_mismatch") in [(i.record, i.code) for i in r.rejected]
    assert "REQ-NF-001" not in r.accepted


def test_records_outside_the_drafted_range_keep_warnings(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    _strip(acme_repo_ingested / "kb/requirements/REQ-NF-002.yaml", "modality", "actor", "category")
    path = acme_repo_ingested / "kb/requirements/REQ-NF-001.yaml"
    path.write_text(path.read_text().replace("modality: must", "modality: may"))
    commit(acme_repo_ingested, "0.3-style drafts")
    r = ingest_report(acme_repo_ingested, "HEAD")  # nothing changed since HEAD
    assert not [i for i in r.rejected if i.code.startswith("requirement.")]
    data = json.loads(invoke(acme_repo_ingested, "check", "--format", "json").stdout)
    sev = {(i["record"], i["code"]): i["severity"] for i in data["issues"] if i["code"].startswith("requirement.")}
    assert sev[("REQ-NF-002", "requirement.fields_missing")] == "warning"
    assert sev[("REQ-NF-001", "requirement.modality_mismatch")] == "warning"


def test_must_never_is_not_must(tmp_path):
    q = "Seats must never be removed."
    assert _mismatch(_check(tmp_path / "a", q, "must")[0]) == ["requirement.modality_mismatch"]
    assert _mismatch(_check(tmp_path / "b", q, "must_not")[0]) == []
