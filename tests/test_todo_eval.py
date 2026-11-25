"""`kb todo` and the extraction evaluation: the eval do items in order, then one blocking ask per open finding."""

import datetime
import json
import re
from pathlib import Path

from helpers import (
    TODO_CONFIG,
    TODO_TRIAGE,
    commit,
    head,
    invoke,
    make_git_repo,
    ok,
    put,
    req_yaml,
    todo_json,
)

from kb.eval.extract import RUN_B
from kb.eval.findings import Finding, fingerprint, summary, write_summary
from kb.eval.judge import JUDGE_ITEMS, VERDICTS
from kb.gates.coverage import coverage_stats
from kb.records.repo import KbRepo
from kb.sources.sentences import sentence_key, sentences
from kb.yamlio import dump_yaml, load_yaml_strict

INVOICES = "Invoices must be paid within 30 days."
LISTED = "Seats are listed on the account page."
DOC = f"# Seats\n\nA seat costs 5 EUR. {INVOICES} {LISTED}\n"
RUN_B_REQ = (
    f"id: B-REQ-F-001\nstatement: Invoices are due in 30 days.\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n"
    f"    text: '{INVOICES}'\nmodules: [MOD-a]\n"
)
EXTRACT_MESSAGE = "follow .kb/cache/eval/extract.md in a fresh session or subagent, then run `kb eval judge`"
VERDICTS_MESSAGE = "follow .kb/cache/eval/judge.md, then run `kb eval report --write`"


def drafted(tmp_path: Path) -> Path:
    """A clean, committed KB: REQ-F-001 quotes the first sentence of docs/n.md; the rule after it is quoted by none."""
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": TODO_CONFIG, "docs/n.md": DOC})
    ok(root, "ingest")
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001]\n")
    commit(root, "drafts")
    return root


def key_of(root: Path, text: str) -> str:
    repo = KbRepo.load(root)
    return next(sentence_key(s) for s in sentences(repo, "SRC-n") if s.text == text)


def evaluate(root: Path, findings: tuple[Finding, ...] = ()) -> None:
    """Write a summary of the knowledge base as it is now (as `kb eval report --write` would), then commit."""
    repo = KbRepo.load(root)
    agr = {"matched": 1, "only_kb": 0, "only_b": 0, "kb_items": 1, "b_items": 1, "unlocated": 0, "span_f1": 1.0,
           "estimated_total": 1, "entity_f1": None, "relation_f1": None}
    write_summary(root, summary(head(root), fingerprint(repo), "0" * 32, coverage_stats(repo), agr, list(findings),
                                datetime.date.today().isoformat()))
    commit(root, "evaluated")


def omission(root: Path, fid: str = "F-0123456789") -> Finding:
    return Finding(fid, f"S:{key_of(root, INVOICES)}", "omission", "SRC-n#seg-01", INVOICES, "high", 3, (), "",
                   "requirement")


def do_ids(t: dict) -> list[str]:
    return [d["id"] for d in t["do"]]


def ask_ids(t: dict) -> list[str]:
    return [a["id"] for a in t["ask"]]


def verdicts_for(items: list[dict]) -> list[dict]:
    """Every record faithful, every gap a missing rule, every entity gap not an entity."""
    out = []
    for i in items:
        v: dict = {"id": i["public"], "span": i["text"]}
        if i["kind"] == "faithful":
            v |= {"same_meaning": "yes", "same_actor": "yes", "same_modality": "yes", "no_added_constraint": "yes"}
        elif i["kind"] in ("uncovered", "only_b"):
            v |= {"verdict": "missing", "kind_hint": "requirement", "severity": "high"}
        elif i["kind"] == "split_merge":
            v |= {"verdict": "ok"}
        elif i["kind"] in ("entity_missing", "relation_missing"):
            v |= {"verdict": "not_an_entity"}
        elif i["kind"] == "entity_faithful":
            v |= {"definition_matches_quote": "yes", "kind_correct": "yes"}
        else:
            v |= {"quote_states_relation": "yes", "direction_correct": "yes"}
        out.append(v)
    return out


def test_todo_eval_sequence(tmp_path):
    root = drafted(tmp_path)
    t = todo_json(root)
    assert do_ids(t) == ["eval:extract"] and t["do"][0]["command"] == "kb eval extract"
    assert not t["state"]["clean"] and not any(a.startswith("freeze:") for a in ask_ids(t))
    ok(root, "eval", "extract")
    t = todo_json(root)
    assert t["do"] == [{"id": "eval:run-b", "code": "kb.eval", "message": EXTRACT_MESSAGE, "command": None,
                        "path": None, "record": None}]
    put(root, f"{RUN_B}/requirements/B-REQ-F-001.yaml", RUN_B_REQ)
    t = todo_json(root)
    assert do_ids(t) == ["eval:judge"] and t["do"][0]["command"] == "kb eval judge"
    ok(root, "eval", "judge")
    t = todo_json(root)
    assert t["do"] == [{"id": "eval:verdicts", "code": "kb.eval", "message": VERDICTS_MESSAGE, "command": None,
                        "path": None, "record": None}]
    items = json.loads((root / JUDGE_ITEMS).read_text())
    (root / VERDICTS).write_text(dump_yaml({"verdicts": verdicts_for(items)}))
    ok(root, "eval", "report", "--write")
    assert do_ids(todo_json(root)) == ["commit"]  # the summary is the agent's to commit
    commit(root, "evaluated")
    findings = load_yaml_strict((root / "kb/eval/summary.yaml").read_text())["open_findings"]
    assert [f["canonical"] for f in findings] == [f"S:{key_of(root, INVOICES)}"]  # run B's item folded into it
    fid = findings[0]["id"]
    t = todo_json(root)
    assert do_ids(t) == [] and f"finding:{fid}" in ask_ids(t)
    assert not t["state"]["clean"] and t["state"]["blocking_asks"] == 1
    ask = next(a for a in t["ask"] if a["id"] == f"finding:{fid}")
    assert ask["kind"] == "omission"
    assert ask["evidence"]["quote"] == INVOICES and ask["evidence"]["document"] == "docs/n.md"
    assert [(o["label"], o["then"]) for o in ask["options"]] == [
        ("Add it as a new record", ("write a record quoting this sentence, run `kb ingest --report`, then "
                                    f"`kb eval decide {fid} --as added --record <new id> --by <handle>`")),
        ("Already covered by a record", f"`kb eval decide {fid} --as covered --record <id> --by <handle>`"),
        ("Not a rule", f"`kb eval decide {fid} --as not_a_rule --reason <why> --by <handle>`"),
        ("Needs a product decision", "record the user's answer with `kb clarify`, draft from it, then decide `--as added`"),
    ]
    # canonical ids never appear: not in the JSON, not in the text form
    text = ok(root, "todo").stdout
    for out in (text, json.dumps(t)):
        assert findings[0]["canonical"] not in out and not re.search(r"\bS:[0-9a-f]{12}", out)
    assert f"`{INVOICES}`" in text  # the sentence, as inline code in the evidence
    # the person says it is not a rule: decided, committed, clean, and the freeze is offered
    ok(root, "eval", "decide", fid, "--as", "not_a_rule", "--reason", "Payment terms live in the contract.", "--by", "@alice")
    assert do_ids(todo_json(root)) == ["commit"]
    commit(root, "decided")
    t = todo_json(root)
    assert do_ids(t) == [] and t["state"]["clean"] and t["state"]["blocking_asks"] == 0
    assert f"finding:{fid}" not in ask_ids(t) and "freeze:v1" in ask_ids(t)


def test_open_findings_block_freeze(tmp_path):
    root = drafted(tmp_path)
    evaluate(root)
    t = todo_json(root)
    assert t["state"]["clean"] and "freeze:v1" in ask_ids(t)  # evaluated, nothing open
    evaluate(root, (omission(root),))
    t = todo_json(root)
    assert do_ids(t) == [] and not t["state"]["clean"] and t["state"]["blocking_asks"] == 1
    assert "finding:F-0123456789" in ask_ids(t) and not any(a.startswith("freeze:") for a in ask_ids(t))
    # added: a record quoting the sentence, then the decision; the summary stays current (the change is decided)
    put(root, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002", INVOICES))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    ok(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    commit(root, "added")
    t = todo_json(root)
    assert do_ids(t) == [] and t["state"]["clean"] and "freeze:v1" in ask_ids(t)
    # any other change needs a new evaluation
    put(root, "kb/requirements/REQ-F-003.yaml", req_yaml("REQ-F-003"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002, REQ-F-003]\n")
    commit(root, "another")
    assert do_ids(todo_json(root)) == ["eval:extract"]


def test_implied_requirement_ask(tmp_path):
    root = drafted(tmp_path)
    put(root, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002").replace("origin: stated", "origin: implied"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    commit(root, "implied")
    evaluate(root)
    t = todo_json(root)
    assert "implied:REQ-F-002" in ask_ids(t) and not any(a.startswith("freeze:") for a in ask_ids(t))
    assert not t["state"]["clean"] and t["state"]["blocking_asks"] == 1
    ask = next(a for a in t["ask"] if a["id"] == "implied:REQ-F-002")
    assert ask["kind"] == "implied" and ask["evidence"]["record"] == "REQ-F-002"
    assert [(o["key"], o["label"], o["then"]) for o in ask["options"]] == [
        ("confirm", "Confirm", "`kb clarify` with the user's confirmation, add it as a quote, `kb redraft REQ-F-002`"),
        ("retire", "Not a requirement", "set `status: rejected` on REQ-F-002; never delete the file"),
    ]
    put(root, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002").replace("origin: stated", "origin: implied")
        + "status: rejected\n")
    commit(root, "retired")
    evaluate(root)
    t = todo_json(root)
    assert "implied:REQ-F-002" not in ask_ids(t) and t["state"]["clean"] and "freeze:v1" in ask_ids(t)


def test_categories_ask_optional(tmp_path):
    root = drafted(tmp_path)
    assert "categories" not in ask_ids(todo_json(root))  # not before an evaluation
    evaluate(root)
    t = todo_json(root)
    ask = next(a for a in t["ask"] if a["id"] == "categories")
    assert ask["kind"] == "categories"
    assert ask["question"] == "None of the documents state security, privacy or availability requirements. Is that right?"
    assert [(o["label"], o["then"]) for o in ask["options"]] == [
        ("Correct, none apply", "`kb eval decide categories --as not_a_rule --reason <why> --by <handle>`"),
        ("I will give them", "record each with `kb clarify`, then draft a REQ-NF requirement quoting it"),
    ]
    # optional: the KB is clean and the freeze is offered all the same
    assert t["state"]["clean"] and t["state"]["blocking_asks"] == 0 and "freeze:v1" in ask_ids(t)
    nf = req_yaml("REQ-NF-001").replace("category: performance", "category: security")
    put(root, "kb/requirements/REQ-NF-001.yaml", nf)
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-NF-001]\n")
    commit(root, "security")
    evaluate(root)
    ask = next(a for a in todo_json(root)["ask"] if a["id"] == "categories")
    assert ask["question"] == "None of the documents state privacy or availability requirements. Is that right?"
    ok(root, "eval", "decide", "categories", "--as", "not_a_rule", "--reason", "An internal tool.", "--by", "@alice")
    assert "categories" not in ask_ids(todo_json(root))
    assert invoke(root, "eval", "decide", "categories", "--as", "added", "--by", "@alice").exit_code == 2


def test_unusable_decisions_file_is_a_fix_item(tmp_path):
    root = drafted(tmp_path)
    evaluate(root)
    put(root, "kb/eval/decisions.yaml", "decisions: [{finding: 'S:abc'}]\n")
    commit(root, "broken")
    t = todo_json(root)
    assert do_ids(t) == ["fix:eval.decisions_invalid:kb/eval/decisions.yaml"] and not t["state"]["clean"]
    assert "freeze:v1" not in ask_ids(t)
    assert "error eval.decisions_invalid" in invoke(root, "check").output
    assert invoke(root, "check", "--strict").exit_code == 1


# --- fix round 1 --------------------------------------------------------------------------------------------------

def b_finding(fid: str, text: str) -> Finding:
    return Finding(fid, "B:B-REQ-F-001", "omission", "SRC-n#seg-01", text, "medium", 2, (), "", "requirement")


def unfaithful(fid: str, canonical: str = "R:REQ-F-001") -> Finding:
    return Finding(fid, canonical, "unfaithful", "SRC-n#seg-01", "A seat costs 5 EUR.", "high", 1, (), "no: same_actor")


def decide(root: Path, *args: str):
    return invoke(root, "eval", "decide", *args)


def test_run_b_decision_does_not_carry_to_another_sentence(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (b_finding("F-1111111111", LISTED),))
    ok(root, "eval", "decide", "F-1111111111", "--as", "not_a_rule", "--reason", "Navigation, not a rule.", "--by", "@alice")
    commit(root, "decided")
    # a second cycle: run B numbers from 001 again, and B-REQ-F-001 is now another sentence
    evaluate(root, (b_finding("F-2222222222", INVOICES),))
    t = todo_json(root)
    assert "finding:F-2222222222" in ask_ids(t) and t["state"]["blocking_asks"] == 1 and not t["state"]["clean"]
    # the same sentence again, under whatever id: settled by its sentence key
    evaluate(root, (b_finding("F-3333333333", LISTED),))
    assert "finding:F-3333333333" not in ask_ids(todo_json(root))


def test_correct_stops_applying_when_the_record_changes(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (unfaithful("F-1111111111"),))
    ok(root, "eval", "decide", "F-1111111111", "--as", "correct", "--by", "@alice")
    commit(root, "decided")
    evaluate(root, (unfaithful("F-2222222222"),))  # the record is as it was: the decision still applies
    assert "finding:F-2222222222" not in ask_ids(todo_json(root))
    reworded = req_yaml("REQ-F-001").replace("statement: 'A seat costs 5 EUR.'", "statement: 'Seats cost 5 EUR each.'")
    put(root, "kb/requirements/REQ-F-001.yaml", reworded)
    commit(root, "reworded")
    evaluate(root, (unfaithful("F-3333333333"),))
    t = todo_json(root)
    assert "finding:F-3333333333" in ask_ids(t) and t["state"]["blocking_asks"] == 1


def test_inconsistent_decisions_are_dropped(tmp_path):
    root = drafted(tmp_path)
    evaluate(root)
    key = key_of(root, INVOICES)
    base = {"canonical": f"S:{key}", "sentence_key": key, "record": None, "reason": "A note.", "by": "@alice",
            "date": "2026-11-26"}
    entries = [
        {"finding": "categories", "canonical": "categories", "sentence_key": key, "decision": "not_a_rule",
         "record": None, "reason": "None apply.", "by": "@alice", "date": "2026-11-26"},  # categories carries no key
        base | {"finding": "F-aaaaaaaaaa", "decision": "correct", "reason": None},  # not a decision for an omission
        base | {"finding": "F-bbbbbbbbbb", "decision": "added", "record": "REQ-F-404"},  # no such record
        base | {"finding": "F-cccccccccc", "decision": "not_a_rule", "by": "@jöhn"},  # not an ASCII handle
        base | {"finding": "F-dddddddddd", "decision": "not_a_rule", "reason": "# A heading"},  # not plain text
        base | {"finding": "F-eeeeeeeeee", "decision": "not_a_rule", "sentence_key": "0" * 12},  # not the S finding's key
    ]
    put(root, "kb/eval/decisions.yaml", dump_yaml({"decisions": entries}))
    commit(root, "hand-written")
    out = invoke(root, "check").output
    assert out.count("error eval.decisions_invalid") == len(entries)
    assert "Invoices must be paid" in out and "sentence.uncovered" in out  # silenced by none of them
    assert f"S:{key}" not in out and key not in out  # canonical ids and keys are not echoed
    from kb.eval.decisions import decided_keys

    assert decided_keys(root) == set()
    assert "categories" in ask_ids(todo_json(root))


def test_decided_record_edit_makes_the_summary_stale(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    put(root, "kb/requirements/REQ-F-002.yaml", req_yaml("REQ-F-002", INVOICES))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    ok(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    commit(root, "added")
    assert do_ids(todo_json(root)) == []
    put(root, "kb/requirements/REQ-F-002.yaml",
        req_yaml("REQ-F-002", INVOICES).replace(f"statement: '{INVOICES}'", "statement: 'Invoices are due in 30 days.'"))
    commit(root, "edited after the decision")
    assert do_ids(todo_json(root)) == ["eval:extract"]


def test_added_needs_a_new_record(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    both = req_yaml("REQ-F-001").replace("modules:", f"  - source: SRC-n#seg-01\n    text: '{INVOICES}'\nmodules:")
    put(root, "kb/requirements/REQ-F-001.yaml", both)  # an existing record made to quote the sentence too
    r = decide(root, "F-0123456789", "--as", "added", "--record", "REQ-F-001", "--by", "@alice")
    assert r.exit_code == 2, r.output
    assert "use a new record, or redraft the existing one and decide --as fixed on its finding" in r.output
    assert not (root / "kb/eval/decisions.yaml").exists()


def test_decide_refuses_a_stale_summary_and_needs_reasons(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    r = decide(root, "F-0123456789", "--as", "product_decision", "--by", "@alice")
    assert r.exit_code == 2 and "--reason" in r.output
    put(root, "kb/requirements/REQ-F-003.yaml", req_yaml("REQ-F-003"))  # a change nobody decided
    r = decide(root, "F-0123456789", "--as", "not_a_rule", "--reason", "Not one.", "--by", "@alice")
    assert r.exit_code == 2 and "run the evaluation again (`kb todo`)" in r.output
    assert not (root / "kb/eval/decisions.yaml").exists()


def test_fixed_needs_a_finding_about_records(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (unfaithful("F-1111111111", canonical="R:not-a-record"),))
    reworded = req_yaml("REQ-F-001").replace("statement: 'A seat costs 5 EUR.'", "statement: 'Seats cost 5 EUR each.'")
    put(root, "kb/requirements/REQ-F-001.yaml", reworded)
    r = decide(root, "F-1111111111", "--as", "fixed", "--record", "REQ-F-001", "--by", "@alice")
    assert r.exit_code == 2, r.output
