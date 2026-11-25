"""Findings from validated verdicts: ranked, counted by supporters; the fingerprint and `kb eval report`."""

import dataclasses
import datetime
import json
import re

from helpers import invoke, stored_repo
from judge_fixture import FILES, SPEC, ingested_with_run_b, valid

from kb.eval.align import Alignment, Item
from kb.eval.extract import EVAL_BASE
from kb.eval.findings import Finding, build_findings, fingerprint
from kb.eval.judge import JUDGE_ITEMS, VERDICTS, judge_state, public_id
from kb.records.repo import KbRepo
from kb.yamlio import dump_yaml, load_yaml_strict


def item(iid: str, kind: str, spans=(), **extra) -> dict:
    return {"id": iid, "kind": kind, "ref": "SRC-a#seg-01", "text": f"text of {iid}", "context": "", "spans": [list(s) for s in spans],
            **extra}


def gap(iid: str, verdict: str = "missing", severity: str | None = "medium", **extra) -> dict:
    v = {"id": iid, "verdict": verdict, "span": f"text of {iid}", "related": [], "note": ""}
    if severity:
        v["severity"] = severity
    if verdict == "missing":
        v["kind_hint"] = "requirement"
    return v | extra


EMPTY = Alignment(matched=[], only_kb=[], only_b=[], splits=[], merges=[])


def test_findings_ranked():
    items = [
        item("S:aaaaaaaaaaaa", "uncovered", [("SRC-a", 0, 10)]),
        item("S:bbbbbbbbbbbb", "uncovered", [("SRC-a", 20, 30)]),
        item("S:cccccccccccc", "uncovered", [("SRC-a", 40, 50)]),
        item("S:dddddddddddd", "uncovered", [("SRC-a", 60, 70)]),
        item("B:B-REQ-F-001", "only_b", [("SRC-a", 22, 28)]),  # the same sentence as S:bbb…
        item("B:B-REQ-F-002", "only_b", [("SRC-a", 100, 110)]),
        item("M:REQ-F-001", "split_merge", [("SRC-a", 200, 210)]),
        item("S:eeeeeeeeeeee", "uncovered", [("SRC-a", 80, 90)]),
    ]
    verdicts = [
        gap("S:aaaaaaaaaaaa", severity="low"),
        gap("S:bbbbbbbbbbbb", severity="medium"),
        gap("S:cccccccccccc", severity="high"),
        gap("S:dddddddddddd", verdict="not_a_rule", severity=None),  # not a finding
        gap("B:B-REQ-F-001", severity="medium"),
        gap("B:B-REQ-F-002", severity="medium"),
        {"id": "M:REQ-F-001", "verdict": "should_split", "span": "text of M:REQ-F-001", "note": ""},
        gap("S:eeeeeeeeeeee", verdict="unknown", severity=None),
    ]
    b1 = Item("B-REQ-F-001", "requirement", "x", (("SRC-a", 22, 28),))
    b2 = Item("B-REQ-F-002", "requirement", "y", (("SRC-a", 100, 110),))
    al = dataclasses.replace(EMPTY, only_b=[b1, b2], splits=[("REQ-F-001", ["B-REQ-F-007", "B-REQ-F-008"])])
    findings = build_findings(items, verdicts, al)
    # shown by opaque public id; ranked (and ties broken) by the canonical id
    assert all(re.fullmatch(r"F-[0-9a-f]{10}", f.id) and f.id == public_id(f.canonical) for f in findings)
    assert [(f.canonical, f.kind, f.severity, f.supporters) for f in findings] == [
        ("S:cccccccccccc", "omission", "high", 2),  # high first
        # run B also extracted it: the coverage gate, run B and the judge (B:B-REQ-F-001 is the same gap, asked once)
        ("S:bbbbbbbbbbbb", "omission", "medium", 3),
        ("B:B-REQ-F-002", "omission", "medium", 2),  # ties broken by id
        ("S:eeeeeeeeeeee", "omission", "medium", 1),  # unknown: the judge did not flag it, the gate did
        ("M:REQ-F-001", "split", "low", 2),
        ("S:aaaaaaaaaaaa", "omission", "low", 2),
    ]
    f = findings[0]
    assert (f.ref, f.text, f.kind_hint) == ("SRC-a#seg-01", "text of S:cccccccccccc", "requirement")
    # judged not a rule as a sentence, but missing as run B drafted it: that B item stands, with every supporter
    flipped = [gap("S:bbbbbbbbbbbb", verdict="not_a_rule", severity=None) if v["id"] == "S:bbbbbbbbbbbb" else v for v in verdicts]
    got = {x.canonical: x for x in build_findings(items, flipped, al)}
    assert "S:bbbbbbbbbbbb" not in got and got["B:B-REQ-F-001"].supporters == 3
    # unknown as a sentence, missing (high) as run B drafted it: not folded, the judge's B verdict stands
    flipped = [gap("S:bbbbbbbbbbbb", verdict="unknown", severity=None) if v["id"] == "S:bbbbbbbbbbbb"
               else gap("B:B-REQ-F-001", severity="high") if v["id"] == "B:B-REQ-F-001" else v for v in verdicts]
    got = {x.canonical: x for x in build_findings(items, flipped, al)}
    assert got["B:B-REQ-F-001"].severity == "high" and got["S:bbbbbbbbbbbb"].severity == "medium"
    # both missing, the sentence low and run B's high: folded into the sentence's finding, which becomes high
    flipped = [gap("S:bbbbbbbbbbbb", severity="low") | {"kind_hint": None} if v["id"] == "S:bbbbbbbbbbbb"
               else gap("B:B-REQ-F-001", severity="high", kind_hint="exclusion") if v["id"] == "B:B-REQ-F-001" else v
               for v in verdicts]
    got = {x.canonical: x for x in build_findings(items, flipped, al)}
    assert "B:B-REQ-F-001" not in got
    assert (got["S:bbbbbbbbbbbb"].severity, got["S:bbbbbbbbbbbb"].kind_hint) == ("high", "exclusion")
    # a split, merge or unknown granularity verdict
    for verdict, kind in (("ok", None), ("should_merge", "merge"), ("unknown", "unknown")):
        got = build_findings(items[6:7], [verdicts[6] | {"verdict": verdict}], al)
        assert [x.kind for x in got] == ([kind] if kind else [])


def test_unfaithful_from_any_no():
    yes = {"same_meaning": "yes", "same_actor": "yes", "same_modality": "yes", "no_added_constraint": "yes"}
    items = [item("R:REQ-F-001", "faithful"), item("R:REQ-F-002", "faithful"), item("T:TERM-a", "entity_faithful"),
             item("L:TERM-a:owns:TERM-b", "relation_faithful"), item("T:TERM-b", "entity_faithful")]
    verdicts = [
        {"id": "R:REQ-F-001", "span": "text of R:REQ-F-001", **yes},
        {"id": "R:REQ-F-002", "span": "text of R:REQ-F-002", **yes, "same_modality": "no", "note": "should, not must"},
        {"id": "T:TERM-a", "span": "text of T:TERM-a", "definition_matches_quote": "yes", "kind_correct": "no"},
        {"id": "L:TERM-a:owns:TERM-b", "span": "text of L:TERM-a:owns:TERM-b", "quote_states_relation": "yes",
         "direction_correct": "no"},
        {"id": "T:TERM-b", "span": "text of T:TERM-b", "definition_matches_quote": "yes", "kind_correct": "yes"},
    ]
    findings = build_findings(items, verdicts, EMPTY)
    assert [(f.canonical, f.kind, f.supporters) for f in findings] == [
        ("L:TERM-a:owns:TERM-b", "unfaithful", 1), ("R:REQ-F-002", "unfaithful", 1), ("T:TERM-a", "unfaithful", 1)]
    r2 = next(f for f in findings if f.canonical == "R:REQ-F-002")
    assert "same_modality" in r2.note and "should, not must" in r2.note and r2.severity == "high"
    assert "kind_correct" in next(f for f in findings if f.canonical == "T:TERM-a").note


def test_fingerprint_follows_records_and_segments_not_eval_files():
    repo = stored_repo(SPEC, FILES)
    fp = fingerprint(repo)
    assert len(fp) == 16 and int(fp, 16) >= 0
    eval_files = {**FILES, "kb/eval/summary.yaml": "commit: x\n", "kb/eval/decisions.yaml": "decisions: []\n"}
    assert fingerprint(stored_repo(SPEC, eval_files)) == fp
    # a rejected record is not live: it does not count
    rejected = {**FILES, "kb/requirements/REQ-F-002.yaml": FILES["kb/requirements/REQ-F-002.yaml"].replace("never given", "not given")}
    assert fingerprint(stored_repo(SPEC, rejected)) == fp
    changed = {**FILES, "kb/requirements/REQ-F-001.yaml": FILES["kb/requirements/REQ-F-001.yaml"].replace("must", "should")}
    assert fingerprint(stored_repo(SPEC, changed)) != fp
    assert fingerprint(stored_repo(SPEC.replace("30 days", "31 days"), FILES)) != fp


def test_eval_files_are_not_unknown_records():
    files = {"kb/eval/summary.yaml": b"commit: x\n", "kb/eval/decisions.yaml": b"decisions: []\n", "kb/eval/other.yaml": b"x: 1\n"}
    repo = KbRepo.from_files(files)
    assert [(i.code, i.path) for i in repo.errors] == [("schema.unknown_file", "kb/eval/other.yaml")]
    assert repo.records == {}


def test_report_write_produces_summary(tmp_path):
    root = ingested_with_run_b(tmp_path)
    assert invoke(root, "eval", "judge").exit_code == 0
    items = json.loads((root / JUDGE_ITEMS).read_text())
    good = valid(items)
    # one record judged unfaithful, with untrusted Markdown in its note
    good["R:REQ-F-001"] |= {"same_actor": "no", "note": "binds `the admin` ## not the system"}
    for iid, v in good.items():
        if iid.startswith(("S:", "B:")):
            v.update(verdict="not_a_rule", related=[])
            v.pop("kind_hint")
            v.pop("severity")
    (root / VERDICTS).write_text(dump_yaml({"verdicts": [v for k, v in good.items() if k != "T:TERM-invoice"]}))
    # a verdict is missing: the report says so, exits 1 and writes nothing
    r = invoke(root, "eval", "report", "--write")
    assert r.exit_code == 1 and f"`{public_id('T:TERM-invoice')}`: no verdict" in r.stdout
    assert not (root / "kb/eval/summary.yaml").exists() and "not written" in r.stderr
    (root / VERDICTS).write_text(dump_yaml({"verdicts": list(good.values())}))
    r = invoke(root, "eval", "report")
    assert r.exit_code == 0, r.output
    out = r.stdout
    assert "## Coverage" in out and "upper bound" in out and "## Rejected verdicts (0)" in out
    # escaped, on one line: the note cannot open code, a heading or a new line
    assert "note ``no: same_actor; binds `the admin` ## not the system``" in out
    assert "\n## not" not in out
    assert not (root / "kb/eval/summary.yaml").exists()
    r = invoke(root, "eval", "report", "--write")
    assert r.exit_code == 0, r.output
    assert "kb/eval/summary.yaml" in r.stdout
    summary = load_yaml_strict((root / "kb/eval/summary.yaml").read_text())
    repo = KbRepo.load(root)
    state = judge_state(root, repo)
    from kb.eval.judge import validate_verdicts

    verdicts, _ = validate_verdicts(root, repo)
    expected = [dataclasses.asdict(f) | {"related": list(f.related)} for f in build_findings(state.items, verdicts, state.alignment)]
    assert list(summary) == ["commit", "fingerprint", "coverage", "agreement", "open_findings", "judged_at"]
    assert summary["commit"] == (root / EVAL_BASE).read_text().strip()
    assert summary["fingerprint"] == fingerprint(repo)
    assert summary["coverage"]["normative"] == 3 and set(summary["agreement"]) >= {"matched", "span_f1", "estimated_total"}
    assert summary["open_findings"] == expected
    # the unfaithful record (high) first; then the entity found by run B and kb's detector, and the rest by id
    assert [(f["canonical"], f["kind"], f["supporters"]) for f in expected] == [
        ("R:REQ-F-001", "unfaithful", 1), ("E:billing admin", "omission", 3), ("E:seat", "omission", 2),
        ("N:B-TERM-account:uses:B-TERM-seat", "omission", 2)]
    assert all(f["id"] == public_id(f["canonical"]) for f in summary["open_findings"])
    assert list(summary["open_findings"][0])[:2] == ["id", "canonical"]
    # the report names findings by public id only
    assert "R:REQ-F-001" not in out and f"`{public_id('R:REQ-F-001')}`" in out
    assert Finding(**{**expected[0], "related": tuple(expected[0]["related"])}) in build_findings(state.items, verdicts, state.alignment)
    datetime.date.fromisoformat(summary["judged_at"])
    # the summary is a known kb file, and writing it does not change the fingerprint
    assert not [i for i in repo.errors if i.code == "schema.unknown_file"]
    assert fingerprint(KbRepo.load(root)) == summary["fingerprint"]
