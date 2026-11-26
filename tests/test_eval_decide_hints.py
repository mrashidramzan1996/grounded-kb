"""`kb eval decide` errors that tell the person what to do next."""

from helpers import TODO_TRIAGE, invoke, ok, put, req_yaml
from test_todo_eval import INVOICES, drafted, evaluate, omission


def test_batch_decide_says_one_finding_per_command(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    r = invoke(root, "eval", "decide", "F-0123456789", "F-0123456780", "--as", "ok", "--by", "@alice")
    assert r.exit_code == 2
    assert "decide one finding per command: kb eval decide <F-id> --as <" in r.output + r.stderr
    r = invoke(root, "eval", "decide", "F-0123456789,F-0123456780", "--as", "ok", "--by", "@alice")
    assert r.exit_code == 2 and "decide one finding per command" in r.output + r.stderr


def test_stale_summary_names_the_changed_records_and_the_order(tmp_path):
    root = drafted(tmp_path)
    evaluate(root, (omission(root),))
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001").replace("statement: 'A seat", "statement: 'One seat"))
    put(root, "kb/requirements/REQ-F-002.yaml", (
        "id: REQ-F-002\nstatement: Invoices are paid within 30 days.\nmodality: must\nactor: customer\norigin: stated\n"
        f"evidence:\n  quotes:\n  - source: SRC-n#seg-01\n    text: '{INVOICES}'\nmodules:\n- MOD-a\n"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002]\n")
    r = invoke(root, "eval", "decide", "F-0123456789", "--as", "added", "--record", "REQ-F-002", "--by", "@alice")
    out = r.output + r.stderr
    assert r.exit_code == 2 and "REQ-F-001" in out
    assert "--as fixed" in out and "--as added" in out and "one at a time, right after each edit" in out
