"""The extraction evaluation end to end (0.4.0), run with the real commands and nothing stood in for: `kb eval extract`,
a scripted blind run B, `kb eval judge`, scripted verdicts, `kb eval report --write`, the blocking asks in `kb todo`,
the records drafted for them and a person's `kb eval decide`, until `kb todo` is clean and offers the freeze.

The scripted run B and verdicts stand in for the agents following the two briefs; the person's answers are fixed
strings. Nothing here uses `helpers.evaluated()`.
"""

import json
import re
from pathlib import Path

from helpers import classification, commit, git, invoke, make_git_repo, write

from kb.eval.extract import RUN_B
from kb.eval.judge import JUDGE_ITEMS, VERDICTS
from kb.yamlio import dump_yaml, load_yaml_strict

ACCOUNT = "An account is a customer of Acme."
SEATS = "Each account must hold at least one seat."
INVOICES = "Invoices must be paid within 30 days."
DOC = f"# Accounts\n\n{ACCOUNT} {SEATS} {INVOICES}\n"
CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-accounts\n    owner: "@alice"\n'
TRIAGE = "kb/sources/SRC-spec/triage.yaml"
BY = "@eval-user"


def kb(repo: Path, *args: str, code: int = 0):
    r = invoke(repo, *args)
    assert r.exit_code == code, r.output
    return r


def todo(repo: Path) -> dict:
    return json.loads(kb(repo, "todo", "--format", "json").stdout)


def do_ids(t: dict) -> list[str]:
    return [d["id"] for d in t["do"]]


def quotes(*texts: str, indent: str = "  ") -> str:
    return f"{indent}quotes:\n" + "".join(f"{indent}- source: SRC-spec#seg-01\n{indent}  text: '{t}'\n" for t in texts)


def req(rid: str, statement: str, quote: str) -> str:
    fields = "".join(f"{k}: {v}\n" for k, v in classification(rid).items())
    return (f"drafted_by: agent:test via kb ingest brief v1\nid: {rid}\nstatement: {statement}\n{fields}evidence:\n"
            + quotes(quote) + "modules:\n- MOD-accounts\n")


def term(tid: str, name: str, definition: str, kind: str, quote: str, relations: str = "") -> str:
    return (f"drafted_by: agent:test via kb ingest brief v1\nid: {tid}\nname: {name}\naliases: [{name}s]\n"
            f"definition: {definition}\nkind: {kind}\nevidence:\n" + quotes(quote) + relations)


def relation(target: str, quote: str) -> str:
    return f"relations:\n- type: owns\n  target: {target}\n  quote:\n    source: SRC-spec#seg-01\n    text: '{quote}'\n"


# run B, written blind from the extract brief: both rules, both entities and the relation between them
RUN_B_FILES = {
    "requirements/B-REQ-F-001.yaml": (
        "id: B-REQ-F-001\nstatement: An account holds one seat or more.\nmodality: must\nactor: account\n"
        "origin: stated\nevidence:\n" + quotes(SEATS) + "modules: [MOD-accounts]\n"
    ),
    "requirements/B-REQ-F-002.yaml": (
        "id: B-REQ-F-002\nstatement: Invoices are due within 30 days.\nmodality: must\nactor: customer\n"
        "origin: stated\nevidence:\n" + quotes(INVOICES) + "modules: [MOD-accounts]\n"
    ),
    "glossary/B-TERM-account.yaml": (
        "id: B-TERM-account\nname: account\ndefinition: A customer.\nkind: actor\nevidence:\n" + quotes(ACCOUNT)
        + relation("B-TERM-seat", SEATS)
    ),
    "glossary/B-TERM-seat.yaml": (
        "id: B-TERM-seat\nname: seat\naliases: [seats]\ndefinition: A paid place on an account.\nkind: data\n"
        "evidence:\n" + quotes(SEATS)
    ),
}


def verdicts(items: list[dict]) -> list[dict]:
    """The judge, item by item: every record and term faithful, every rule gap missing, the seat entity and its
    relation missing, any other entity candidate not one."""
    out = []
    for i in items:
        v: dict = {"id": i["public"], "span": i["passages"][0]}
        if i["kind"] == "faithful":
            v |= {"same_meaning": "yes", "same_actor": "yes", "same_modality": "yes", "no_added_constraint": "yes"}
        elif i["kind"] == "entity_faithful":
            v |= {"definition_matches_quote": "yes", "kind_correct": "yes"}
        elif i["kind"] == "relation_faithful":
            v |= {"quote_states_relation": "yes", "direction_correct": "yes"}
        elif i["kind"] in ("uncovered", "only_b"):
            v |= {"verdict": "missing", "kind_hint": "requirement", "severity": "high"}
        elif i["kind"] == "split_merge":
            v |= {"verdict": "ok"}
        elif i["kind"] == "entity_missing":
            v |= {"verdict": "missing" if "seat" in i["names"] else "not_an_entity"}
        else:  # relation_missing
            v |= {"verdict": "missing", "related": ["TERM-account"]}
        out.append(v)
    return out


def test_full_eval_loop(tmp_path):
    # the main extraction: the seat rule and the account term; the invoice rule, the seat and the relation are missed
    repo = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/spec.md": DOC})
    kb(repo, "ingest")
    write(repo, "kb/requirements/REQ-F-001.yaml", req("REQ-F-001", SEATS, SEATS))
    write(repo, "kb/glossary/TERM-account.yaml", term("TERM-account", "account", "A customer of Acme.", "actor", ACCOUNT))
    write(repo, TRIAGE, "segments:\n  seg-01: [REQ-F-001, TERM-account]\n")
    out = kb(repo, "ingest", "--report", "--strict").stdout
    assert "## Rejected (0)" in out
    commit(repo, "drafts")
    assert "sentence.uncovered" in kb(repo, "check").output

    # the evaluation, one do item at a time, in order
    t = todo(repo)
    assert do_ids(t) == ["eval:extract"] and t["do"][0]["command"] == "kb eval extract"
    assert not t["state"]["clean"] and not any(a["id"].startswith("freeze:") for a in t["ask"])
    kb(repo, "eval", "extract")
    brief = (repo / ".kb/cache/eval/extract.md").read_text()
    assert SEATS in brief and INVOICES in brief
    assert not re.search(r"(?<![\w-])(?:REQ-F-001|TERM-account)\b", brief)  # blind: no kb id (only B- ones)
    assert do_ids(todo(repo)) == ["eval:run-b"]
    for rel, text in RUN_B_FILES.items():  # a fresh session follows the brief
        write(repo, f"{RUN_B}/{rel}", text)
    assert do_ids(todo(repo)) == ["eval:judge"]
    r = kb(repo, "eval", "judge")
    assert "run B item(s) set aside" not in r.output  # every run B file verifies
    assert do_ids(todo(repo)) == ["eval:verdicts"]
    items = json.loads((repo / JUDGE_ITEMS).read_text())
    kinds = {i["kind"] for i in items}
    assert {"faithful", "uncovered", "entity_faithful", "entity_missing", "relation_missing"} <= kinds
    judge_brief = (repo / ".kb/cache/eval/judge.md").read_text()
    assert all(i["public"] in judge_brief for i in items)
    assert "B-REQ" not in judge_brief and "S:" not in judge_brief  # never which extraction an item came from
    (repo / VERDICTS).write_text(dump_yaml({"verdicts": verdicts(items)}))
    report = kb(repo, "eval", "report", "--write").stdout
    assert "wrote kb/eval/summary.yaml" in report and "## Rejected verdicts (0)" in report
    assert do_ids(todo(repo)) == ["commit"]
    commit(repo, "evaluated")

    # three omission findings: the invoice rule (run B's item folded into the coverage gate's), the seat, the relation
    findings = load_yaml_strict((repo / "kb/eval/summary.yaml").read_text())["open_findings"]
    assert sorted((f["kind"], f["kind_hint"]) for f in findings) == [
        ("omission", "entity"), ("omission", "relation"), ("omission", "requirement")]
    rule, = (f for f in findings if f["kind_hint"] == "requirement")
    seat, = (f for f in findings if f["kind_hint"] == "entity")
    owns, = (f for f in findings if f["kind_hint"] == "relation")
    assert rule["text"] == INVOICES and rule["supporters"] == 3
    t = todo(repo)
    assert t["do"] == [] and not t["state"]["clean"] and t["state"]["blocking_asks"] == 3
    asks = {a["id"]: a for a in t["ask"]}
    omissions = [a for a in t["ask"] if a["kind"] == "omission"]
    assert [a["id"] for a in omissions] == [f"finding:{rule['id']}"]  # one blocking omission ask
    assert omissions[0]["evidence"]["quote"] == INVOICES and omissions[0]["evidence"]["document"] == "docs/spec.md"
    assert asks[f"finding:{seat['id']}"]["kind"] == "entity" and asks[f"finding:{owns['id']}"]["kind"] == "relation"
    assert not any(a.startswith("freeze:") for a in asks)
    text = kb(repo, "todo").stdout
    for f in findings:
        assert f["id"] in text and f["canonical"] not in text

    # the person: "add all three". Each is drafted, gated by the ingest report, then decided as the option says
    write(repo, "kb/requirements/REQ-F-002.yaml", req("REQ-F-002", INVOICES, INVOICES))
    write(repo, TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002, TERM-account]\n")
    assert "## Rejected (0)" in kb(repo, "ingest", "--report", "--strict").stdout
    kb(repo, "eval", "decide", rule["id"], "--as", "added", "--record", "REQ-F-002", "--by", BY)
    write(repo, "kb/glossary/TERM-seat.yaml", term("TERM-seat", "seat", "A paid place on an account.", "data", SEATS))
    write(repo, TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-F-002, TERM-account, TERM-seat]\n")
    assert "## Rejected (0)" in kb(repo, "ingest", "--report", "--strict").stdout
    kb(repo, "eval", "decide", seat["id"], "--as", "added", "--record", "TERM-seat", "--by", BY)
    write(repo, "kb/glossary/TERM-account.yaml",
          term("TERM-account", "account", "A customer of Acme.", "actor", ACCOUNT, relation("TERM-seat", SEATS)))
    assert "## Rejected (0)" in kb(repo, "ingest", "--report", "--strict").stdout
    kb(repo, "eval", "decide", owns["id"], "--as", "added", "--record", "TERM-account", "--by", BY)
    decisions = load_yaml_strict((repo / "kb/eval/decisions.yaml").read_text())["decisions"]
    assert [(d["finding"], d["decision"], d["record"], d["by"]) for d in decisions] == [
        (rule["id"], "added", "REQ-F-002", BY), (seat["id"], "added", "TERM-seat", BY),
        (owns["id"], "added", "TERM-account", BY)]
    assert do_ids(todo(repo)) == ["commit"]
    commit(repo, "added what the evaluation found")

    # nothing open, the summary still current (only decided records changed): clean; the optional categories question
    # comes first (the freeze waits for it: its answer dirties kb/), then the freeze is offered
    t = todo(repo)
    assert t["do"] == [] and t["state"]["clean"] and t["state"]["blocking_asks"] == 0 and t["state"]["errors"] == 0
    assert [a["id"] for a in t["ask"]] == ["categories"]
    kb(repo, "eval", "decide", "categories", "--as", "not_a_rule", "--reason", "An internal tool.", "--by", BY)
    commit(repo, "categories")
    t = todo(repo)
    assert "freeze:v1" in [a["id"] for a in t["ask"]]
    assert not any(a["id"].startswith("finding:") for a in t["ask"])
    out = kb(repo, "check").output
    assert "sentence.uncovered" not in out and "eval.decisions_invalid" not in out
    status = kb(repo, "eval", "status").stdout
    assert "findings: 0 open, 3 decided" in status and "up to date" in status
    sample = kb(repo, "eval", "sample").stdout  # for the pull-request description after the baseline
    assert "REQ-F-002" in sample or "REQ-F-001" in sample
    assert git(repo, "status", "--porcelain") == ""
