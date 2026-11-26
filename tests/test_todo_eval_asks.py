"""The evaluation's questions in `kb todo`: the freeze waits for every one of them, and each option's `then` can be
followed as written."""

import re

from helpers import TODO_TRIAGE, commit, invoke, ok, put, todo_json
from test_todo_eval import INVOICES, ask_ids, do_ids, drafted, evaluate, omission

ANSWER = "Invoices are paid by bank transfer within 30 days of issue."


def test_freeze_is_not_offered_while_an_evaluation_question_is_open(tmp_path):
    root = drafted(tmp_path)
    evaluate(root)  # nothing open, but no live REQ-NF has security, privacy or availability
    t = todo_json(root)
    assert "categories" in ask_ids(t) and t["state"]["clean"]  # optional, so the KB is clean
    # answering both in one go would fail (the decision dirties kb/, and the freeze refuses a dirty kb/)
    assert not any(a.startswith("freeze:") for a in ask_ids(t))
    ok(root, "eval", "decide", "categories", "--as", "not_a_rule", "--reason", "An internal tool.", "--by", "@alice")
    assert not any(a.startswith("freeze:") for a in ask_ids(todo_json(root)))  # uncommitted
    commit(root, "categories")
    t = todo_json(root)
    freeze = next(a for a in t["ask"] if a["id"] == "freeze:v1")
    assert freeze["options"][0]["then"].startswith(
        "commit pending changes and run `kb todo` again, then `kb baseline freeze v1`, then commit")


def test_needs_a_product_decision_can_be_followed_as_written(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    ask = next(a for a in todo_json(root)["ask"] if a["id"] == "finding:F-0123456789")
    product = next(o for o in ask["options"] if o["key"] == "product")
    assert product["then"] == (
        "record the user's answer with `kb clarify`, draft a record that quotes both the document sentence and the "
        "clarification, run `kb ingest --report`, then "
        "`kb eval decide F-0123456789 --as added --record <new id> --by <handle>`")
    # the agent follows it: the user's answer, then a record quoting the sentence and the answer
    out = ok(root, "clarify", "--topic", "Invoice payment", "--text", ANSWER, "--by", "@alice",
             "--date", "2026-11-26").stdout
    ref = re.search(r"SRC-clarifications#seg-\d+", out).group(0)
    put(root, "kb/requirements/REQ-F-002.yaml", (
        "id: REQ-F-002\nstatement: Invoices are paid by bank transfer within 30 days.\nmodality: must\n"
        f"actor: customer\norigin: stated\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: '{INVOICES}'\n"
        f"  - source: {ref}\n    text: '{ANSWER}'\nmodules:\n- MOD-a\n"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    put(root, "kb/sources/SRC-clarifications/triage.yaml",
        f"segments:\n  seg-01: 'none: heading: the clarifications file'\n  {ref.split('#')[1]}: [REQ-F-002]\n")
    assert "## Rejected (0)" in ok(root, "ingest", "--report").stdout
    ok(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    commit(root, "the user's answer, recorded")
    t = todo_json(root)
    assert do_ids(t) == [] and t["state"]["clean"] and "finding:F-0123456789" not in ask_ids(t)
    assert "up to date" in ok(root, "eval", "status").stdout
    assert "eval.decisions_invalid" not in invoke(root, "check").output


def test_a_clarification_no_decided_record_quotes_needs_a_new_evaluation(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    ok(root, "clarify", "--topic", "Invoice payment", "--text", ANSWER, "--by", "@alice", "--date", "2026-11-26")
    # the record quotes the document sentence only: the clarification is new document text no decision names
    put(root, "kb/requirements/REQ-F-002.yaml", (
        "id: REQ-F-002\nstatement: Invoices are paid within 30 days.\nmodality: must\nactor: customer\norigin: stated\n"
        f"evidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: '{INVOICES}'\nmodules:\n- MOD-a\n"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    r = invoke(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    assert r.exit_code == 2 and "run the evaluation again" in r.output
