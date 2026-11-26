"""The evaluation's questions in `kb todo`: the freeze waits for every one of them, and each option's `then` can be
followed as written."""

import re

import pytest

from kb.eval.decisions import _answer_segments, read_summary
from kb.records.repo import KbRepo
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
        "`kb eval decide F-0123456789 --as added --record <new id> --by <handle>`"
        "; edit, then decide this finding immediately, before editing the next record")
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


EXTRA = " Admins must approve every refund over 500 EUR. Users must change their password every 90 days."


def _answered(root, text):
    """The omission answered with `kb clarify` (the given text), a record quoting only ANSWER, decided as added and
    committed: the same steps as the option-(d) path above."""
    out = ok(root, "clarify", "--topic", "Invoice payment", "--text", text, "--by", "@alice",
             "--date", "2026-11-26").stdout
    ref = re.search(r"SRC-clarifications#seg-\d+", out).group(0)
    put(root, "kb/requirements/REQ-F-002.yaml", (
        "id: REQ-F-002\nstatement: Invoices are paid by bank transfer within 30 days.\nmodality: must\n"
        f"actor: customer\norigin: stated\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: '{INVOICES}'\n"
        f"  - source: {ref}\n    text: '{ANSWER}'\nmodules:\n- MOD-a\n"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    put(root, "kb/sources/SRC-clarifications/triage.yaml",
        f"segments:\n  seg-01: 'none: heading: the clarifications file'\n  {ref.split('#')[1]}: [REQ-F-002]\n")
    ok(root, "ingest", "--report")
    r = invoke(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    if text == ANSWER:
        assert r.exit_code == 0, r.output
    else:  # the answer's section holds rules the record does not quote: no decision can settle it
        assert r.exit_code == 2 and "run the evaluation again" in r.output
    commit(root, "the user's answer, recorded")
    return ref


def test_clarification_with_extra_rules_makes_eval_stale(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    _answered(root, ANSWER + EXTRA)
    # two rules in the answer's section were never evaluated nor quoted: the summary is stale
    assert "up to date" not in ok(root, "eval", "status").stdout
    t = todo_json(root)
    assert any(i.startswith("eval:extract") for i in do_ids(t))
    r = invoke(root, "baseline", "freeze", "v1")
    assert r.exit_code == 1


def test_rewritten_clarification_section_makes_eval_stale(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    ok(root, "clarify", "--topic", "Invoice payment", "--text", "Payments are made by card.", "--by", "@alice",
       "--date", "2026-11-26")
    commit(root, "an earlier answer")
    evaluate(root, (omission(root),))
    # the earlier section is rewritten (not new), and a record quotes the rewritten text
    path = root / "docs/clarifications.md"
    path.write_text(path.read_text().replace("Payments are made by card.", ANSWER + EXTRA))
    ref = "SRC-clarifications#seg-02"
    ok(root, "ingest", "docs/clarifications.md")
    put(root, "kb/requirements/REQ-F-002.yaml", (
        "id: REQ-F-002\nstatement: Invoices are paid by bank transfer within 30 days.\nmodality: must\n"
        f"actor: customer\norigin: stated\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: '{INVOICES}'\n"
        f"  - source: {ref}\n    text: '{ANSWER}'\nmodules:\n- MOD-a\n"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    put(root, "kb/sources/SRC-clarifications/triage.yaml",
        f"segments:\n  seg-01: 'none: heading: the clarifications file'\n  seg-02: [REQ-F-002]\n")
    ok(root, "ingest", "--report")
    # a rewritten section is never one of the person's new answers, whatever a decided record quotes of it
    base = KbRepo.at_ref(root, read_summary(root).commit)
    assert _answer_segments(KbRepo.load(root), base, {"REQ-F-002"}) == set()
    r = invoke(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    assert r.exit_code == 2 and "run the evaluation again" in r.output


STEM = ANSWER.rstrip(".")
BYPASSES = {
    "same_sentence": (f"{STEM}; admins must approve every refund over 500 EUR.", STEM),
    "line_break": (f"{STEM}\nand admins must approve every refund over 500 EUR.", STEM),
    "crosses_boundary": (f"{ANSWER} Admins approve every refund.", f"{ANSWER} Admins"),
    "fenced_rule": (f"{ANSWER}\n\n```\nAdmins must approve every refund over 500 EUR.\n```", ANSWER),
    "no_cue_words": (f"{ANSWER} Admins approve every refund.", ANSWER),
}


@pytest.mark.parametrize("case", sorted(BYPASSES))
def test_clarification_text_no_quote_covers_makes_eval_stale(tmp_path, case):
    body, quote = BYPASSES[case]
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    ok(root, "clarify", "--topic", "Invoice payment", "--text", ANSWER, "--by", "@alice", "--date", "2026-11-26")
    path = root / "docs/clarifications.md"
    path.write_text(path.read_text().replace(f"\n{ANSWER}\n", f"\n{body}\n"))
    ok(root, "ingest", "docs/clarifications.md")
    ref = "SRC-clarifications#seg-02"
    put(root, "kb/requirements/REQ-F-002.yaml", (
        "id: REQ-F-002\nstatement: Invoices are paid by bank transfer within 30 days.\nmodality: must\n"
        f"actor: customer\norigin: stated\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: '{INVOICES}'\n"
        f"  - source: {ref}\n    text: '{quote}'\nmodules:\n- MOD-a\n"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    put(root, "kb/sources/SRC-clarifications/triage.yaml",
        "segments:\n  seg-01: 'none: heading: the clarifications file'\n  seg-02: [REQ-F-002]\n")
    ok(root, "ingest", "--report")
    r = invoke(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    assert r.exit_code == 2 and "run the evaluation again" in r.output
    commit(root, "answer with text no quote covers")
    assert "up to date" not in ok(root, "eval", "status").stdout
    assert any(i.startswith("eval:extract") for i in do_ids(todo_json(root)))
    assert invoke(root, "baseline", "freeze", "v1").exit_code == 1


@pytest.mark.parametrize("case", ["rule_in_heading", "paragraph_before_decided_line"])
def test_clarification_rule_outside_the_answer_makes_eval_stale(tmp_path, case):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    topic = "Admins must approve every refund over 500 EUR" if case == "rule_in_heading" else "Invoice payment"
    out = ok(root, "clarify", "--topic", topic, "--text", ANSWER, "--by", "@alice", "--date", "2026-11-26").stdout
    ref = re.search(r"SRC-clarifications#seg-\d+", out).group(0)
    if case == "paragraph_before_decided_line":
        path = root / "docs/clarifications.md"
        path.write_text(path.read_text().replace(
            "\nDecided by", "\nAdmins must approve every refund over 500 EUR.\n\nDecided by"))
        ok(root, "ingest", "docs/clarifications.md")
    put(root, "kb/requirements/REQ-F-002.yaml", (
        "id: REQ-F-002\nstatement: Invoices are paid by bank transfer within 30 days.\nmodality: must\n"
        f"actor: customer\norigin: stated\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: '{INVOICES}'\n"
        f"  - source: {ref}\n    text: '{ANSWER}'\nmodules:\n- MOD-a\n"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    put(root, "kb/sources/SRC-clarifications/triage.yaml",
        f"segments:\n  seg-01: 'none: heading: the clarifications file'\n  {ref.split('#')[1]}: [REQ-F-002]\n")
    ok(root, "ingest", "--report")
    r = invoke(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    assert r.exit_code == 2 and "run the evaluation again" in r.output
    commit(root, "answer with a rule outside it")
    assert "up to date" not in ok(root, "eval", "status").stdout
    assert any(i.startswith("eval:extract") for i in do_ids(todo_json(root)))
    assert invoke(root, "baseline", "freeze", "v1").exit_code == 1
