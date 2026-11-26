"""The judge alone never settles a rule-like sentence kb flagged: a `covered` verdict must name a record whose found
quotes show it, and every item the judge dismisses (covered, not a rule, not an entity) waits for a person in one
batched `dismissed` ask, pinned to the exact list; `--as reopen` turns one back into an open omission."""

import json
from pathlib import Path

from helpers import QUOTE, commit, invoke, ok, put, stored_repo, todo_json
from judge_fixture import FILES, SPEC, write_run_b
from test_todo_eval import INVOICES, drafted

from kb.eval.extract import RUN_B
from kb.eval.judge import JUDGE_ITEMS, VERDICTS, validate_verdicts, write_judge
from kb.yamlio import dump_yaml, load_yaml_strict

# run B copies run A: it extracts the recorded sentence only, so nothing but the coverage gate flags INVOICES
RUN_B_COPY = ("id: B-REQ-F-001\nstatement: A seat costs 5 EUR.\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n"
              f"    text: '{QUOTE}'\nmodules: [MOD-a]\n")


def verdicts(items: list[dict], gap: dict) -> list[dict]:
    out = []
    for i in items:
        v: dict = {"id": i["public"], "span": i["passages"][0]}
        if i["kind"] == "faithful":
            v |= {"same_meaning": "yes", "same_actor": "yes", "same_modality": "yes", "no_added_constraint": "yes"}
        elif i["kind"] in ("uncovered", "only_b"):
            v |= gap
        elif i["kind"] in ("entity_missing", "relation_missing"):
            v |= {"verdict": "not_an_entity"}
        out.append(v)
    return out


def judge(root: Path, gap: dict, code: int = 0) -> list[dict]:
    """One evaluation cycle with run B copying run A and the judge answering every rule gap with `gap`."""
    ok(root, "eval", "extract")
    put(root, f"{RUN_B}/requirements/B-REQ-F-001.yaml", RUN_B_COPY)
    ok(root, "eval", "judge")
    items = json.loads((root / JUDGE_ITEMS).read_text())
    (root / VERDICTS).write_text(dump_yaml({"verdicts": verdicts(items, gap)}))
    r = invoke(root, "eval", "report", "--write")
    assert r.exit_code == code, r.output
    return items


def summary_of(root: Path) -> dict:
    return load_yaml_strict((root / "kb/eval/summary.yaml").read_text())


def ask_ids(t: dict) -> list[str]:
    return [a["id"] for a in t["ask"]]


NOT_A_RULE = {"verdict": "not_a_rule", "note": "A payment term."}


def test_covered_with_a_record_that_does_not_quote_the_sentence_is_invalid(tmp_path):
    root = drafted(tmp_path)
    judge(root, {"verdict": "covered", "related": ["REQ-F-001"]}, code=1)  # REQ-F-001 quotes another sentence
    assert not (root / "kb/eval/summary.yaml").exists()
    out = invoke(root, "eval", "report").output
    assert "eval.verdict_invalid" in out or "Rejected verdicts (1)" in out
    assert "names no record that quotes this text" in out


def test_covered_entity_and_relation_need_a_term_that_shows_them(tmp_path):
    repo = stored_repo(SPEC, FILES)
    write_run_b(tmp_path)
    items = {i["id"]: i for i in write_judge(tmp_path, repo).items}
    seat = items["E:seat"]
    rel = next(i for i in items.values() if i["kind"] == "relation_missing")
    entries = [{"id": seat["public"], "span": seat["passages"][0], "verdict": "covered", "related": ["TERM-account"]},
               {"id": rel["public"], "span": rel["passages"][0], "verdict": "covered", "related": ["TERM-invoice"]}]
    (tmp_path / VERDICTS).write_text(dump_yaml({"verdicts": entries}))
    invalid = [i.message for i in validate_verdicts(tmp_path, repo)[1] if i.code == "eval.verdict_invalid"]
    assert len(invalid) == 2
    assert any(seat["public"] in m and "carries" in m for m in invalid)
    assert any(rel["public"] in m and "holds" in m for m in invalid)


def test_covered_by_a_record_whose_quote_shows_the_sentence_is_valid(tmp_path):
    refunds = "Refunds are never given for a removed seat."  # REQ-F-002 quoting it is rejected: still uncovered
    files = dict(FILES) | {"kb/glossary/TERM-refund.yaml": (
        "id: TERM-refund\nname: refund\ndefinition: Money paid back.\nevidence:\n  quotes:\n  - source: SRC-spec#seg-01\n"
        f"    text: '{refunds}'\n")}
    repo = stored_repo(SPEC, files)
    write_run_b(tmp_path)
    item = next(i for i in write_judge(tmp_path, repo).items if i["kind"] == "uncovered" and i["text"] == refunds)
    entry = {"id": item["public"], "span": refunds, "verdict": "covered", "related": ["TERM-refund"]}
    (tmp_path / VERDICTS).write_text(dump_yaml({"verdicts": [entry]}))
    assert not [i for i in validate_verdicts(tmp_path, repo)[1] if i.code == "eval.verdict_invalid"]


def test_a_judge_dismissal_keeps_the_kb_unclean_until_a_person_agrees(tmp_path):
    # the probe: the sentence is left unrecorded, run B copies run A, the judge dismisses it
    root = drafted(tmp_path)
    items = judge(root, NOT_A_RULE)
    commit(root, "evaluated")
    s = summary_of(root)
    gap = next(i for i in items if i["kind"] == "uncovered")
    assert s["open_findings"] == []
    assert [(d["id"], d["canonical"], d["kind"], d["text"], d["verdict"]) for d in s["dismissed"]] == [
        (gap["public"], gap["id"], "omission", INVOICES, "not_a_rule")]
    t = todo_json(root)
    assert not t["state"]["clean"] and t["state"]["blocking_asks"] == 1
    assert "dismissed" in ask_ids(t) and not any(a.startswith("freeze:") for a in ask_ids(t))
    ask = next(a for a in t["ask"] if a["id"] == "dismissed")
    assert ask["kind"] == "dismissed"
    assert "judged these 1 items as not needing a record" in ask["question"] and INVOICES in ask["question"]
    assert [o["then"] for o in ask["options"]] == [
        "`kb eval decide dismissed --as ok --by <handle>`",
        "for each one you disagree with, `kb eval decide <F-id> --as reopen --by <handle>`",
    ]
    text = ok(root, "todo").stdout
    assert f"`{INVOICES}`" in text and gap["public"] in text and gap["id"] not in text
    # a person agrees: the ask goes, and the freeze question shows how many were dismissed
    ok(root, "eval", "decide", "dismissed", "--as", "ok", "--by", "@alice")
    commit(root, "agreed")
    t = todo_json(root)
    assert t["state"]["clean"] and "dismissed" not in ask_ids(t)
    ok(root, "eval", "decide", "categories", "--as", "not_a_rule", "--reason", "None apply.", "--by", "@alice")
    commit(root, "categories")
    t = todo_json(root)
    freeze = next(a for a in t["ask"] if a["id"] == "freeze:v1")
    assert "agreed with the 1 item the evaluation agent judged as not needing a record" in freeze["question"]
    status = ok(root, "eval", "status").stdout
    assert "dismissed by the evaluation agent: 1" in status and f"`{gap['public']}` not a rule (agreed)" in status


def test_the_dismissed_decision_is_pinned_to_the_list(tmp_path):
    root = drafted(tmp_path)
    judge(root, NOT_A_RULE)
    commit(root, "evaluated")
    ok(root, "eval", "decide", "dismissed", "--as", "ok", "--by", "@alice")
    commit(root, "agreed")
    assert "dismissed" not in ask_ids(todo_json(root))
    # a new judge cycle gives the same item a new public id: the person is asked again
    judge(root, NOT_A_RULE)
    commit(root, "evaluated again")
    t = todo_json(root)
    assert "dismissed" in ask_ids(t) and not t["state"]["clean"]
    # a second `dismissed` decision on the new list stands
    ok(root, "eval", "decide", "dismissed", "--as", "ok", "--by", "@alice")
    commit(root, "agreed again")
    t = todo_json(root)
    assert "dismissed" not in ask_ids(t) and t["state"]["clean"]
    assert "eval.decisions_invalid" not in invoke(root, "check").output


def test_reopen_turns_a_dismissed_item_into_an_omission_finding(tmp_path):
    root = drafted(tmp_path)
    items = judge(root, NOT_A_RULE)
    commit(root, "evaluated")
    fid = next(i for i in items if i["kind"] == "uncovered")["public"]
    ok(root, "eval", "decide", fid, "--as", "reopen", "--by", "@alice")
    commit(root, "reopened")
    t = todo_json(root)
    asks = {a["id"]: a for a in t["ask"]}
    assert f"finding:{fid}" in asks and asks[f"finding:{fid}"]["kind"] == "omission"
    assert "dismissed" not in asks  # nothing left to agree with: its only item is reopened
    assert not t["state"]["clean"] and t["state"]["blocking_asks"] == 1
    assert "1 open" in ok(root, "eval", "status").stdout
    r = invoke(root, "eval", "decide", fid, "--as", "reopen", "--by", "@alice")
    assert r.exit_code == 2 and "reopened already" in r.output
    # the reopened finding is decided like any omission
    ok(root, "eval", "decide", fid, "--as", "not_a_rule", "--reason", "A contract term.", "--by", "@alice")
    commit(root, "decided")
    t = todo_json(root)
    assert t["state"]["clean"] and f"finding:{fid}" not in ask_ids(t)
    assert "eval.decisions_invalid" not in invoke(root, "check").output


def test_reopen_takes_only_a_dismissed_item(tmp_path):
    root = drafted(tmp_path)
    judge(root, {"verdict": "missing", "kind_hint": "requirement", "severity": "high"})
    commit(root, "evaluated")
    fid = summary_of(root)["open_findings"][0]["id"]
    r = invoke(root, "eval", "decide", fid, "--as", "reopen", "--by", "@alice")
    assert r.exit_code == 2 and "dismissed" in r.output
    r = invoke(root, "eval", "decide", "dismissed", "--as", "ok", "--by", "@alice")
    assert r.exit_code == 2 and "nothing" in r.output  # the evaluation dismissed nothing


def test_hand_written_dismissed_and_reopen_entries_are_checked(tmp_path):
    root = drafted(tmp_path)
    items = judge(root, NOT_A_RULE)
    commit(root, "evaluated")
    gap = next(i for i in items if i["kind"] == "uncovered")
    base = {"sentences": [], "record": None, "record_sha256": None, "reason": None, "by": "@x", "date": "2026-11-26"}
    for entry, why in (
        ({"finding": "dismissed", "canonical": "dismissed", "decision": "ok"}, "no pin"),
        ({"finding": "dismissed", "canonical": "categories", "decision": "ok", "record_sha256": "0" * 64}, "canonical"),
        ({"finding": gap["public"], "canonical": gap["id"], "decision": "reopen", "record": "REQ-F-001"}, "record"),
        ({"finding": gap["public"], "canonical": "S:000000000000", "decision": "reopen"}, "canonical"),
        ({"finding": "dismissed", "canonical": "dismissed", "decision": "not_a_rule", "record_sha256": "0" * 64,
          "reason": "x"}, "decision"),
    ):
        fields = base | entry
        doc = {"decisions": [{k: fields[k] for k in ("finding", "canonical", "sentences", "decision", "record",
                                                     "record_sha256", "reason", "by", "date")}]}
        put(root, "kb/eval/decisions.yaml", dump_yaml(doc))
        out = invoke(root, "check").output
        assert "eval.decisions_invalid" in out, why
        t = todo_json(root)
        assert "dismissed" in ask_ids(t) and f"finding:{gap['public']}" not in ask_ids(t), why
