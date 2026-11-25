"""`kb eval decide`: a person's decision on a finding, validated and recorded in kb/eval/decisions.yaml."""

import hashlib
import json
import os
from pathlib import Path

from helpers import git, invoke, make_git_repo
from judge_fixture import ingested_with_run_b, valid

from kb.config.schema import KbConfig
from kb.eval.decisions import DECISIONS, decided_keys, read_decisions
from kb.eval.judge import JUDGE_ITEMS, VERDICTS
from kb.init.scaffold import init_repo
from kb.records.repo import KbRepo
from kb.sources.sentences import sentence_key, sentences
from kb.verify.approvals import no_approver_warnings, required_owners
from kb.verify.changes import ChangeSet
from kb.yamlio import dump_yaml, load_yaml_strict

INVOICES = "Invoices must be paid within 30 days."
REFUNDS = "Refunds are never given for a removed seat."


def evaluated(tmp_path: Path) -> tuple[Path, dict[str, dict]]:
    """The judge fixture through `kb eval report --write`, every gap judged missing, the summary committed:
    (root, {canonical id: open finding})."""
    root = ingested_with_run_b(tmp_path)
    assert invoke(root, "eval", "judge").exit_code == 0
    items = json.loads((root / JUDGE_ITEMS).read_text())
    (root / VERDICTS).write_text(dump_yaml({"verdicts": list(valid(items).values())}))
    r = invoke(root, "eval", "report", "--write")
    assert r.exit_code == 0, r.output
    git(root, "add", "-A")
    git(root, "commit", "-m", "evaluated")
    summary = load_yaml_strict((root / "kb/eval/summary.yaml").read_text())
    return root, {f["canonical"]: f for f in summary["open_findings"]}


def key_of(root: Path, text: str) -> str:
    repo = KbRepo.load(root)
    return next(sentence_key(s) for s in sentences(repo, "SRC-spec") if s.text == text)


def put(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


def req(rid: str, text: str) -> str:
    return (f"id: {rid}\nstatement: {text}\nevidence:\n  quotes:\n  - source: SRC-spec#seg-01\n    text: '{text}'\n"
            "modules: [MOD-a]\nmodality: must\nactor: the customer\n")


def decide(root: Path, fid: str, *args: str):
    return invoke(root, "eval", "decide", fid, *args)


def test_decide_added_requires_quoting_record(tmp_path):
    root, findings = evaluated(tmp_path)
    fid = findings[f"S:{key_of(root, INVOICES)}"]["id"]
    for record in ("REQ-F-001", "REQ-F-404", "REQ-F-002", "not an id"):  # quotes another sentence; missing; rejected
        r = decide(root, fid, "--as", "added", "--record", record, "--by", "@pm")
        assert r.exit_code == 2, (record, r.output)
        assert not (root / DECISIONS).exists()
    assert decide(root, fid, "--as", "added", "--by", "@pm").exit_code == 2  # no --record
    put(root, "kb/requirements/REQ-F-003.yaml", req("REQ-F-003", INVOICES))
    assert decide(root, fid, "--as", "added", "--record", "REQ-F-003", "--by", "not-a-handle").exit_code == 2
    assert decide(root, fid, "--as", "correct", "--record", "REQ-F-003", "--by", "@pm").exit_code == 2  # not for an omission
    r = decide(root, fid, "--as", "added", "--record", "REQ-F-003", "--by", "@pm", "--date", "2026-11-26")
    assert r.exit_code == 0, r.output
    assert fid in r.stdout and "S:" not in r.output
    data = load_yaml_strict((root / DECISIONS).read_text())
    assert data == {"decisions": [{
        "finding": fid, "canonical": f"S:{key_of(root, INVOICES)}", "sentence_key": key_of(root, INVOICES),
        "decision": "added", "record": "REQ-F-003",
        "record_sha256": hashlib.sha256((root / "kb/requirements/REQ-F-003.yaml").read_bytes()).hexdigest(),
        "reason": None, "by": "@pm", "date": "2026-11-26",
    }]}
    # one decision per finding
    assert decide(root, fid, "--as", "not_a_rule", "--reason", "A note.", "--by", "@pm").exit_code == 2
    assert len(read_decisions(root)) == 1


def test_decide_unknown_finding_exits_2(tmp_path):
    root, findings = evaluated(tmp_path)
    canonical = next(c for c in findings if c.startswith("E:"))
    for fid in ("F-0000000000", canonical, f"S:{key_of(root, INVOICES)}", "F-XYZ"):
        r = decide(root, fid, "--as", "not_a_rule", "--reason", "Not one.", "--by", "@pm")
        assert r.exit_code == 2, (fid, r.output)
        assert canonical not in r.output and "E:" not in r.output  # canonical ids (document text) are never echoed
    assert not (root / DECISIONS).exists()
    # a decisions file that is a symlink is refused, and nothing is written through it
    target = tmp_path / "elsewhere.yaml"
    target.write_text("decisions: []\n")
    (root / "kb/eval").mkdir(exist_ok=True)
    os.symlink(target, root / DECISIONS)
    fid = findings[canonical]["id"]
    assert decide(root, fid, "--as", "not_a_rule", "--reason", "Not one.", "--by", "@pm").exit_code == 2
    assert target.read_text() == "decisions: []\n"
    (root / DECISIONS).unlink()
    # no summary: nothing to decide
    (root / "kb/eval/summary.yaml").unlink()
    assert decide(root, fid, "--as", "not_a_rule", "--reason", "Not one.", "--by", "@pm").exit_code == 2
    assert not (root / DECISIONS).exists()


def test_decided_sentence_stops_warning(tmp_path):
    root, findings = evaluated(tmp_path)
    key = key_of(root, REFUNDS)

    def warned() -> bool:
        r = invoke(root, "check")
        return "sentence.uncovered" in r.output and "Refunds are never given" in r.output

    assert warned()
    fid = findings[f"S:{key}"]["id"]
    r = decide(root, fid, "--as", "not_a_rule", "--reason", "A product note, not a rule.", "--by", "@pm")
    assert r.exit_code == 0, r.output
    assert decided_keys(root) == {key}
    assert not warned()
    assert "Invoices must be paid" in invoke(root, "check").output  # the undecided one still warns
    git(root, "add", "-A")
    git(root, "commit", "-m", "decided")
    # a later cycle: new public ids, and the decided sentence is not asked again
    assert invoke(root, "eval", "extract").exit_code == 0
    from judge_fixture import write_run_b

    write_run_b(root)
    assert invoke(root, "eval", "judge").exit_code == 0
    items = json.loads((root / JUDGE_ITEMS).read_text())
    assert f"S:{key}" not in {i["id"] for i in items}
    assert f"S:{key_of(root, INVOICES)}" in {i["id"] for i in items}
    assert not warned()


def test_entity_omission_decision_validated(tmp_path):
    root, findings = evaluated(tmp_path)
    seat = findings["E:seat"]["id"]
    relation = next(f["id"] for c, f in findings.items() if c.startswith("N:"))
    # an entity decision names a TERM carrying the entity's name
    assert decide(root, seat, "--as", "added", "--record", "TERM-account", "--by", "@pm").exit_code == 2
    assert decide(root, seat, "--as", "added", "--record", "REQ-F-001", "--by", "@pm").exit_code == 2
    assert decide(root, seat, "--as", "fixed", "--by", "@pm").exit_code == 2  # not for an entity
    put(root, "kb/glossary/TERM-seat.yaml",
        "id: TERM-seat\nname: seat\naliases: [seats]\ndefinition: A paid place.\nkind: concept\nevidence:\n  quotes:\n"
        "  - source: SRC-spec#seg-02\n    text: Each account buys seats for its users.\n")
    r = decide(root, seat, "--as", "added", "--record", "TERM-seat", "--by", "@pm")
    assert r.exit_code == 0, r.output
    # a relation decision: the source TERM must hold a relation of that type to that target, quoting the passage
    assert decide(root, relation, "--as", "added", "--record", "TERM-account", "--by", "@pm").exit_code == 2
    account = (root / "kb/glossary/TERM-account.yaml").read_text()
    wrong_type = account + ("- type: owns\n  target: TERM-seat\n  quote:\n    source: SRC-spec#seg-02\n"
                            "    text: Each account buys seats for its users.\n")
    (root / "kb/glossary/TERM-account.yaml").write_text(wrong_type)
    assert decide(root, relation, "--as", "added", "--record", "TERM-account", "--by", "@pm").exit_code == 2
    (root / "kb/glossary/TERM-account.yaml").write_text(wrong_type.replace("- type: owns\n  target: TERM-seat", "- type: uses\n  target: TERM-seat"))
    r = decide(root, relation, "--as", "added", "--record", "TERM-account", "--by", "@pm")
    assert r.exit_code == 0, r.output
    assert [d.decision for d in read_decisions(root)] == ["added", "added"]


def test_decide_fixed_needs_a_changed_record(tmp_path):
    root = ingested_with_run_b(tmp_path)
    assert invoke(root, "eval", "judge").exit_code == 0
    items = json.loads((root / JUDGE_ITEMS).read_text())
    verdicts = valid(items)
    verdicts["R:REQ-F-001"] |= {"same_actor": "no", "note": "binds the customer"}
    (root / VERDICTS).write_text(dump_yaml({"verdicts": list(verdicts.values())}))
    assert invoke(root, "eval", "report", "--write").exit_code == 0
    git(root, "add", "-A")
    git(root, "commit", "-m", "evaluated")
    summary = load_yaml_strict((root / "kb/eval/summary.yaml").read_text())
    fid = next(f["id"] for f in summary["open_findings"] if f["canonical"] == "R:REQ-F-001")
    assert decide(root, fid, "--as", "fixed", "--by", "@pm").exit_code == 2  # unchanged
    assert decide(root, fid, "--as", "fixed", "--record", "EXCL-001", "--by", "@pm").exit_code == 2  # another record
    path = root / "kb/requirements/REQ-F-001.yaml"
    path.write_text(path.read_text().replace("actor: the billing system", "actor: the customer"))
    r = decide(root, fid, "--as", "fixed", "--by", "@pm")
    assert r.exit_code == 0, r.output
    assert read_decisions(root)[0].record == "REQ-F-001"


def test_eval_status_prints_the_summary(tmp_path):
    root, findings = evaluated(tmp_path)
    r = invoke(root, "eval", "status")
    assert r.exit_code == 0, r.output
    assert f"{len(findings)} open, 0 decided" in r.stdout and "up to date" in r.stdout
    assert not any(c in r.output for c in findings)  # canonical ids never shown
    fid = findings["E:seat"]["id"]
    assert decide(root, fid, "--as", "not_a_rule", "--reason", "A count, not a thing.", "--by", "@pm").exit_code == 0
    assert f"{len(findings) - 1} open, 1 decided" in invoke(root, "eval", "status").stdout
    put(root, "kb/requirements/REQ-F-009.yaml", req("REQ-F-009", INVOICES))
    assert "stale" in invoke(root, "eval", "status").stdout


# --- approvals ---------------------------------------------------------------------------------------------------

def _cfg(intent=("@pm",)) -> KbConfig:
    return KbConfig.model_validate({
        "version": 1, "modules": [{"id": "MOD-a", "paths": ["src/a/**"], "owner": "@alice"}],
        "policy": {"approvers": {"intent": list(intent), "decisions": ["@arch"]}},
    })


def _changes(*files: str) -> ChangeSet:
    return ChangeSet("main", tuple(files), {}, {}, {}, frozenset())


def test_eval_dir_changes_need_intent_approvers(tmp_path):
    path = "kb/eval/decisions.yaml"
    old, new = b"decisions: []\n", b"decisions: [x]\n"
    head, base = KbRepo.from_files({path: new}), KbRepo.from_files({path: old})
    assert required_owners(tmp_path, _cfg(), head, base, _changes(path), []) == {"@pm": ["evaluation decisions changed"]}
    assert required_owners(tmp_path, _cfg(), head, None, _changes(), []) == {"@pm": ["evaluation decisions changed"]}
    assert required_owners(tmp_path, _cfg(), head, KbRepo.from_files({path: new}), _changes(), []) == {}
    summary = "kb/eval/summary.yaml"  # the summary too, and a deletion
    assert required_owners(tmp_path, _cfg(), KbRepo.from_files({}), KbRepo.from_files({summary: b"x"}), _changes(summary),
                           []) == {"@pm": ["evaluation decisions changed"]}
    (w,) = no_approver_warnings(_cfg(intent=()), head, base, _changes(path), [])
    assert w.code == "owner_approval.no_approvers" and "evaluation decisions" in w.message
    # kb init's CODEOWNERS block routes kb/eval/ too
    (tmp_path / "init").mkdir()
    repo = make_git_repo(None, tmp_path / "init")
    init_repo(repo)
    assert "# /kb/eval/ " in (repo / "CODEOWNERS").read_text()


def test_relation_added_must_join_the_findings_ends(tmp_path):
    root, findings = evaluated(tmp_path)
    seat, relation = findings["E:seat"]["id"], next(f["id"] for c, f in findings.items() if c.startswith("N:"))
    put(root, "kb/glossary/TERM-seat.yaml",
        "id: TERM-seat\nname: seat\naliases: [seats]\ndefinition: A paid place.\nkind: concept\nevidence:\n  quotes:\n"
        "  - source: SRC-spec#seg-02\n    text: Each account buys seats for its users.\n")
    put(root, "kb/glossary/TERM-user.yaml",
        "id: TERM-user\nname: user\naliases: [users]\ndefinition: A person.\nkind: actor\nevidence:\n  quotes:\n"
        "  - source: SRC-spec#seg-02\n    text: Each account buys seats for its users.\n")
    account = (root / "kb/glossary/TERM-account.yaml").read_text()
    rel = "- type: uses\n  target: {}\n  quote:\n    source: SRC-spec#seg-02\n    text: Each account buys seats for its users.\n"
    # named in the passage, but not the finding's target (seat): not this relation
    (root / "kb/glossary/TERM-account.yaml").write_text(account + rel.format("TERM-user"))
    r = decide(root, relation, "--as", "added", "--record", "TERM-account", "--by", "@pm")
    assert r.exit_code == 2 and "join" in r.output, r.output
    # the entity first (an undecided edit to TERM-account would make the evaluation stale), then its relation
    (root / "kb/glossary/TERM-user.yaml").unlink()
    (root / "kb/glossary/TERM-account.yaml").write_text(account)
    assert decide(root, seat, "--as", "added", "--record", "TERM-seat", "--by", "@pm").exit_code == 0
    (root / "kb/glossary/TERM-account.yaml").write_text(account + rel.format("TERM-seat"))
    r = decide(root, relation, "--as", "added", "--record", "TERM-account", "--by", "@pm")
    assert r.exit_code == 0, r.output


def test_entity_added_needs_a_quote_naming_it(tmp_path):
    root, findings = evaluated(tmp_path)
    seat = findings["E:seat"]["id"]
    term = ("id: TERM-seat\nname: seat\naliases: [seats]\ndefinition: A paid place.\nkind: concept\nevidence:\n"
            "  quotes:\n  - source: SRC-spec#seg-02\n    text: {}\n")
    put(root, "kb/glossary/TERM-seat.yaml", term.format("An account is a customer of Acme."))
    r = decide(root, seat, "--as", "added", "--record", "TERM-seat", "--by", "@pm")
    assert r.exit_code == 2 and "quote" in r.output, r.output
    put(root, "kb/glossary/TERM-seat.yaml", term.format("Each account buys seats for its users."))
    assert decide(root, seat, "--as", "added", "--record", "TERM-seat", "--by", "@pm").exit_code == 0
