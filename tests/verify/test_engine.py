import json

import pytest

from kb.config.schema import KbConfig, ModuleCfg, TestsCfg
from kb.records.io import render_record
from kb.records.models import (
    Evidence,
    Feature,
    FeatureClaim,
    Invariant,
    Quote,
    Requirement,
)
from kb.records.repo import KbRepo
from kb.verify.affected import Affected
from kb.verify.changes import ChangeSet
from kb.verify.engine import EngineInput, Verdict, derive, tests_needed
from kb.verify.index import AnchorIndex, FileCard

PYTEST = TestsCfg(preset="pytest")
CONFIG = KbConfig(version=1, modules=[
    ModuleCfg(id="MOD-billing", paths=["billing/**"], owner="team-a", tests=PYTEST),
    ModuleCfg(id="MOD-sessions", paths=["sessions/**"], owner="team-a"),
    ModuleCfg(id="MOD-qa", paths=["tests/qa/**"], owner="team-a", tests=PYTEST),
])

CODE = "billing/seats.py#add_seat"
TEST = "tests/test_seats.py::test_add_seat"
TEST_B = "tests/test_seats.py::test_remove_seat"
QUOTE = "Seats cost 10 dollars each."
TEXT = f"# Spec\n\n{QUOTE}\n\nRefunds are never given.\n"
SEG = "SRC-spec#seg-01"
FILES = {
    "billing/seats.py": "def add_seat():\n    return 1\n\n\ndef remove_seat():\n    return 0\n",
    "tests/test_seats.py": "def test_add_seat():\n    assert True\n\n\ndef test_remove_seat():\n    assert True\n",
}


# --- builders ------------------------------------------------------------------------------------

def claim(id="FEAT-001.C1", **kw) -> FeatureClaim:
    return FeatureClaim(**({"id": id, "claim": "c", "requirement": "REQ-F-001", "code": CODE, "test": TEST} | kw))


def feature(*claims, id="FEAT-001", module="MOD-billing", **kw) -> Feature:
    return Feature(id=id, title="t", module=module, claims=list(claims or [claim()]), **kw)


def requirement(id="REQ-F-001", quote=QUOTE, seg=SEG, **kw) -> Requirement:
    return Requirement(**({"id": id, "statement": "s", "evidence": Evidence(quotes=[Quote(source=seg, text=quote)]),
                           "features": ["FEAT-001"]} | kw))


def invariant(id="INV-001", tests=(TEST,), **kw) -> Invariant:
    return Invariant(**({"id": id, "statement": "s", "governs": ["MOD-billing"], "tests": list(tests)} | kw))


def source_files(text=TEXT) -> dict[str, bytes]:
    start = text.index(QUOTE) if QUOTE in text else 8
    seg = {"id": "seg-01", "heading": "Spec", "page": 1, "start": start, "end": start + len(QUOTE), "hash": "h1"}
    meta = (
        "id: SRC-spec\noriginal: docs/spec.md\noriginal_sha256: x\nnormaliser: text@1\ntrust: spec\n"
        "visibility: internal\nguard:\n  emails: 0\n  secrets: 0\n  injections: 0\nnext_seg: 2\n"
    )
    return {
        "kb/sources/SRC-spec/source.yaml": meta.encode(),
        "kb/sources/SRC-spec/text.md": text.encode(),
        "kb/sources/SRC-spec/segments.jsonl": (json.dumps(seg) + "\n").encode(),
    }


_DIRS = {Requirement: "requirements", Feature: "features", Invariant: "invariants"}
_EXT = {Requirement: ".yaml", Feature: ".md", Invariant: ".md"}


def kb_repo(records, text=TEXT) -> KbRepo:
    files = source_files(text)
    for r in records:
        files[f"kb/{_DIRS[type(r)]}/{r.id}{_EXT[type(r)]}"] = render_record(r)
    repo = KbRepo.from_files(files)
    assert repo.errors == []
    return repo


def card(path, markers=()):
    return FileCard(path=path, modules=(), symbols=(), imports=(), markers=tuple(markers))


def changes(files=(), fingerprints=None, segments=None):
    return ChangeSet("main", tuple(sorted(files)), {}, fingerprints or {}, segments or {}, frozenset())


def all_of(records) -> Affected:
    claims = {c.id for r in records if isinstance(r, Feature) for c in r.claims}
    return Affected(
        frozenset(claims),
        frozenset(r.id for r in records if isinstance(r, Requirement)),
        frozenset(r.id for r in records if isinstance(r, Invariant)),
    )


def make_input(tmp_path, records, *, outcomes=None, chg=None, markers=None, files=None, text=TEXT, config=CONFIG):
    for rel, content in (FILES if files is None else files).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    cards = {}
    for path, entries in (markers or {}).items():
        cards[path] = card(path, entries)
    return EngineInput(
        root=tmp_path, config=config, repo=kb_repo(records, text), base_repo=None, changes=chg,
        index=AnchorIndex(cards), outcomes=dict(outcomes or {}),
    )


def verdicts(tmp_path, records, affected=None, **kw) -> dict[str, Verdict]:
    inp = make_input(tmp_path, records, **kw)
    return {v.id: v for v in derive(inp, affected or all_of(records))}


def one(tmp_path, records, vid, **kw) -> Verdict:
    return verdicts(tmp_path, records, **kw)[vid]


def state(v: Verdict) -> tuple:
    return v.status, v.level, v.reasons, v.notes


# --- claim rules ---------------------------------------------------------------------------------

def test_no_code_anchor_is_unverified_e0(tmp_path):
    v = one(tmp_path, [feature(claim(code=None))], "FEAT-001.C1", outcomes={TEST: "passed"})
    assert state(v) == ("unverified", "E0", ("no code anchor",), ())
    assert v.kind == "claim"


def test_impl_marker_without_code_continues_at_linked_tests(tmp_path):
    marks = {"billing/seats.py": [("impl", "FEAT-001.C1", 1, None)]}
    v = one(tmp_path, [feature(claim(code=None))], "FEAT-001.C1", outcomes={TEST: "passed"}, markers=marks)
    assert state(v) == ("verified", "E2", (), ())
    v = one(tmp_path, [feature(claim(code=None, test=None))], "FEAT-001.C1", markers=marks)
    assert state(v) == ("unverified", "E1", ("no linked test",), ())


def test_no_adapter_is_unverified(tmp_path):
    v = one(tmp_path, [feature(claim(code="web/app.ts#addSeat"))], "FEAT-001.C1", outcomes={TEST: "passed"})
    assert state(v) == ("unverified", "E0", ("no code adapter for web/app.ts",), ())


def test_unsafe_anchor_path_is_broken(tmp_path):
    v = one(tmp_path, [feature(claim(code="../outside.py#f"))], "FEAT-001.C1", outcomes={TEST: "passed"})
    assert v.status == "broken" and v.level == "E0"
    assert v.reasons == ("anchor ../outside.py#f is not a safe path",)


def test_unresolved_anchor_is_broken_e0(tmp_path):
    v = one(tmp_path, [feature(claim(code="billing/seats.py#gone"))], "FEAT-001.C1", outcomes={TEST: "passed"})
    assert state(v) == ("broken", "E0", ("anchor billing/seats.py#gone no longer resolves",), ())


def test_syntax_error_file_reports_hint(tmp_path):
    files = dict(FILES) | {"billing/seats.py": "def add_seat():\n    return 1\n\n\ndef broken(:\n    pass\n"}
    gone = one(tmp_path, [feature(claim(code="billing/seats.py#remove_seat"))], "FEAT-001.C1",
               outcomes={TEST: "passed"}, files=files)
    assert state(gone) == (
        "broken", "E0", ("anchor billing/seats.py#remove_seat no longer resolves; file has syntax errors",), ()
    )
    # what still resolves in the broken file is judged on its tests (no crash)
    ok = one(tmp_path, [feature(claim())], "FEAT-001.C1", outcomes={TEST: "passed"}, files=files)
    assert ok.status == "verified"


def test_changed_fingerprint_passing_tests_is_verified_with_note(tmp_path):
    chg = changes(["billing/seats.py"], fingerprints={CODE: ("a", "b")})
    v = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: "passed"}, chg=chg)
    assert state(v) == ("verified", "E2", (), ("changed",))


@pytest.mark.parametrize("fp", [("a", "a"), (None, "b"), ("a", None)])
def test_same_or_one_sided_fingerprint_has_no_changed_note(tmp_path, fp):
    chg = changes(["billing/seats.py"], fingerprints={CODE: fp})
    assert one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: "passed"}, chg=chg).notes == ()


def test_no_linked_test_is_unverified_e1(tmp_path):
    v = one(tmp_path, [feature(claim(test=None))], "FEAT-001.C1")
    assert state(v) == ("unverified", "E1", ("no linked test",), ())
    assert v.tests == ()


def test_marker_linked_tests_join_claim_test(tmp_path):
    marks = {"tests/test_seats.py": [("verify", "FEAT-001.C1", 5, TEST_B), ("verify", "FEAT-001.C1", 2, TEST)]}
    v = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: "passed", TEST_B: "passed"}, markers=marks)
    assert v.status == "verified" and v.tests == (TEST, TEST_B)


def test_deleted_test_is_broken_never_lower_level(tmp_path):
    gone = "tests/test_seats.py::test_deleted"
    v = one(tmp_path, [feature(claim(test=gone))], "FEAT-001.C1", outcomes={gone: "missing"})
    assert state(v) == ("broken", "E1", (f"linked test {gone} was deleted",), ())
    v = one(tmp_path, [feature(claim(test="tests/test_gone.py::test_x"))], "FEAT-001.C1")
    assert v.reasons == ("linked test tests/test_gone.py::test_x was deleted",)


def test_no_runner_is_unverified_with_hint(tmp_path):
    v = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: "no_runner"})
    assert state(v) == ("unverified", "E1", ("no test command: set modules[].tests",), ())


def test_failure_beats_no_runner(tmp_path):
    marks = {"tests/test_seats.py": [("verify", "FEAT-001.C1", 5, TEST_B), ("verify", "INV-001", 5, TEST_B)]}
    recs = [feature(), requirement(), invariant()]
    got = verdicts(tmp_path, recs, outcomes={TEST: "no_runner", TEST_B: "failed"}, markers=marks)
    assert state(got["FEAT-001.C1"]) == ("broken", "E1", (f"{TEST_B} failed",), ())
    assert state(got["INV-001"]) == ("broken", "E1", (f"{TEST_B} failed",), ())
    assert got["REQ-F-001"].status == "broken"


def test_unsafe_linked_test_reason(tmp_path):
    unsafe = "../outside.py::test_x"
    recs = [feature(claim(test=unsafe))]
    v = one(tmp_path, recs, "FEAT-001.C1", outcomes={unsafe: "passed"})
    assert state(v) == ("broken", "E1", (f"linked test {unsafe} is not a safe path",), ())
    assert tests_needed(make_input(tmp_path, recs), all_of(recs)) == {}


@pytest.mark.parametrize("outcome", ["failed", "skipped", "missing"])
def test_failed_skipped_or_missing_test_is_broken(tmp_path, outcome):
    marks = {"tests/test_seats.py": [("verify", "FEAT-001.C1", 5, TEST_B)]}
    v = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: outcome, TEST_B: "failed"}, markers=marks)
    assert state(v) == ("broken", "E1", (f"{TEST} {outcome}", f"{TEST_B} failed"), ())


def test_outcome_absent_counts_as_missing(tmp_path):
    v = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={})
    assert state(v) == ("broken", "E1", (f"{TEST} missing",), ())


def test_flaky_quarantined_e1(tmp_path):
    v = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: "flaky"})
    assert state(v) == ("unverified", "E1", (f"{TEST} is flaky; quarantined",), ("flaky",))


def test_passing_tests_is_verified_e2(tmp_path):
    v = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: "passed"})
    assert state(v) == ("verified", "E2", (), ())
    assert v.tests == (TEST,)


def test_code_and_test_changed_together_is_co_changed(tmp_path):
    chg = changes(["billing/seats.py", "tests/test_seats.py"], fingerprints={CODE: ("a", "b")})
    v = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: "passed"}, chg=chg)
    assert state(v) == ("verified", "E2", (), ("changed", "co-changed"))
    only_code = one(tmp_path, [feature()], "FEAT-001.C1", outcomes={TEST: "passed"}, chg=changes(["billing/seats.py"]))
    assert only_code.notes == ()


def test_co_changed_only_on_resolved_anchor(tmp_path):
    chg = changes(["billing/seats.py", "tests/test_seats.py"])
    gone = one(tmp_path, [feature(claim(code="billing/seats.py#gone"))], "FEAT-001.C1", outcomes={TEST: "passed"}, chg=chg)
    assert gone.status == "broken" and gone.notes == ()
    chg = changes(["web/app.ts", "tests/test_seats.py"])
    no_adapter = one(tmp_path, [feature(claim(code="web/app.ts#addSeat"))], "FEAT-001.C1", outcomes={TEST: "passed"},
                     chg=chg)
    assert no_adapter.status == "unverified" and no_adapter.notes == ()


# --- requirement rules ---------------------------------------------------------------------------

def test_requirement_tests_pass_is_verified_e2(tmp_path):
    v = one(tmp_path, [feature(), requirement()], "REQ-F-001", outcomes={TEST: "passed"})
    assert state(v) == ("verified", "E2", (), ())
    assert v.kind == "requirement" and v.tests == (TEST,)


def test_quote_gone_is_broken(tmp_path):
    text = TEXT.replace(QUOTE, "Seats cost 12 dollars each.")
    v = one(tmp_path, [feature(), requirement()], "REQ-F-001", outcomes={TEST: "passed"}, text=text)
    assert v.status == "broken" and v.level == "E2"  # the level is the code side's
    assert v.reasons == (f"quote not found in {SEG}",)


def test_cited_segment_missing_is_broken(tmp_path):
    v = one(tmp_path, [feature(), requirement(seg="SRC-spec#seg-09")], "REQ-F-001", outcomes={TEST: "passed"})
    assert state(v) == ("broken", "E2", ("cited segment SRC-spec#seg-09 is missing",), ())


def test_segment_changed_quote_found_is_stale(tmp_path):
    chg = changes(segments={SEG: "changed"})
    v = one(tmp_path, [feature(), requirement()], "REQ-F-001", outcomes={TEST: "passed"}, chg=chg)
    assert state(v) == ("stale", "E2", (f"cited segment {SEG} changed; needs intent approvers' review",), ())


def test_requirement_with_broken_claim_is_broken(tmp_path):
    v = one(tmp_path, [feature(claim(code="billing/seats.py#gone")), requirement()], "REQ-F-001",
            outcomes={TEST: "passed"})
    assert state(v) == ("broken", "E0", ("claim FEAT-001.C1 is broken",), ())


def test_requirement_without_impl_evidence_is_not_built(tmp_path):
    v = one(tmp_path, [feature(claim(code=None)), requirement()], "REQ-F-001", outcomes={TEST: "passed"})
    assert state(v) == ("not_built", "E0", ("no implementation evidence",), ())


def test_requirement_impl_marker_without_tests_is_built(tmp_path):
    marks = {"billing/seats.py": [("impl", "REQ-F-001", 1, None)]}
    v = one(tmp_path, [requirement(features=[], modules=["MOD-billing"])], "REQ-F-001", markers=marks)
    assert state(v) == ("built", "E1", ("no linked test",), ())


def test_requirement_own_tests_follow_claim_rules(tmp_path):
    marks = {"billing/seats.py": [("impl", "REQ-F-001", 1, None)],
             "tests/test_seats.py": [("verify", "REQ-F-001", 5, TEST_B)]}
    recs = [feature(), requirement()]
    v = one(tmp_path, recs, "REQ-F-001", outcomes={TEST: "passed", TEST_B: "failed"}, markers=marks)
    assert state(v) == ("broken", "E1", (f"{TEST_B} failed",), ())
    assert v.tests == (TEST, TEST_B)
    v = one(tmp_path, recs, "REQ-F-001", outcomes={TEST: "passed", TEST_B: "flaky"}, markers=marks)
    assert state(v) == ("unverified", "E1", (f"{TEST_B} is flaky; quarantined",), ("flaky",))


def test_stale_requirement_keeps_code_level(tmp_path):
    chg = changes(segments={SEG: "changed"})
    passing = one(tmp_path, [feature(), requirement()], "REQ-F-001", outcomes={TEST: "passed"}, chg=chg)
    assert (passing.status, passing.level) == ("stale", "E2")
    marks = {"billing/seats.py": [("impl", "REQ-F-001", 1, None)]}
    built = one(tmp_path, [requirement(features=[], modules=["MOD-billing"])], "REQ-F-001", markers=marks, chg=chg)
    assert (built.status, built.level) == ("stale", "E1")
    unbuilt = one(tmp_path, [feature(claim(code=None)), requirement()], "REQ-F-001", outcomes={TEST: "passed"}, chg=chg)
    assert (unbuilt.status, unbuilt.level) == ("stale", "E0")


def test_requirement_with_unverified_claim_is_not_verified(tmp_path):
    recs = [feature(claim(), claim("FEAT-001.C2", code="web/app.ts#addSeat", test=TEST_B)), requirement()]
    got = verdicts(tmp_path, recs, outcomes={TEST: "passed", TEST_B: "passed"})
    assert got["FEAT-001.C1"].status == "verified"
    assert got["FEAT-001.C2"].status == "unverified"
    assert state(got["REQ-F-001"]) == ("unverified", "E1", ("claim FEAT-001.C2 is unverified",), ())


def test_broken_beats_stale(tmp_path):
    chg = changes(segments={SEG: "changed"})
    v = one(tmp_path, [feature(claim(code="billing/seats.py#gone")), requirement()], "REQ-F-001",
            outcomes={TEST: "passed"}, chg=chg)
    assert v.status == "broken"
    assert v.reasons == (f"cited segment {SEG} changed; needs intent approvers' review", "claim FEAT-001.C1 is broken")


def test_stale_beats_unverified_code_side(tmp_path):
    chg = changes(segments={SEG: "changed"})
    v = one(tmp_path, [feature(), requirement()], "REQ-F-001", outcomes={TEST: "flaky"}, chg=chg)
    assert v.status == "stale" and v.notes == ("flaky",)


def test_requirement_ignores_rejected_features_claims(tmp_path):
    recs = [feature(claim(code="billing/seats.py#gone"), status="rejected"), requirement()]
    v = one(tmp_path, recs, "REQ-F-001", affected=Affected(frozenset(), frozenset({"REQ-F-001"}), frozenset()))
    assert v.status == "not_built"


# --- invariant rules -----------------------------------------------------------------------------

def test_invariant_without_tests_broken(tmp_path):
    v = one(tmp_path, [invariant(tests=())], "INV-001")
    assert state(v) == ("broken", "E0", ("invariant lists no tests",), ())
    assert v.kind == "invariant"


@pytest.mark.parametrize(("outcome", "expected"), [
    ("passed", ("verified", "E2", (), ())),
    ("failed", ("broken", "E1", (f"{TEST} failed",), ())),
    ("flaky", ("unverified", "E1", (f"{TEST} is flaky; quarantined",), ("flaky",))),
    ("no_runner", ("unverified", "E1", ("no test command: set modules[].tests",), ())),
])
def test_invariant_tests_follow_claim_rules(tmp_path, outcome, expected):
    assert state(one(tmp_path, [invariant()], "INV-001", outcomes={TEST: outcome})) == expected


def test_invariant_with_deleted_test_is_broken(tmp_path):
    v = one(tmp_path, [invariant(tests=("tests/test_seats.py::test_gone",))], "INV-001")
    assert state(v) == ("broken", "E1", ("linked test tests/test_seats.py::test_gone was deleted",), ())


def test_invariant_marker_tests_count(tmp_path):
    marks = {"tests/test_seats.py": [("verify", "INV-001", 5, TEST_B)]}
    v = one(tmp_path, [invariant(tests=())], "INV-001", outcomes={TEST_B: "passed"}, markers=marks)
    assert v.status == "verified" and v.tests == (TEST_B,)


# --- selection and ordering ----------------------------------------------------------------------

def test_only_affected_ids_get_verdicts(tmp_path):
    recs = [feature(), requirement(), invariant()]
    got = verdicts(tmp_path, recs, affected=Affected(frozenset(), frozenset({"REQ-F-001"}), frozenset()),
                   outcomes={TEST: "passed"})
    assert list(got) == ["REQ-F-001"]


def test_verdicts_sorted_and_deterministic(tmp_path):
    recs = [
        invariant("INV-002"), invariant("INV-001"),
        requirement("REQ-F-002", features=["FEAT-002"]), requirement(),
        feature(claim("FEAT-002.C1", requirement="REQ-F-002"), id="FEAT-002"),
        feature(claim("FEAT-001.C2"), claim("FEAT-001.C1")),
    ]
    inp = make_input(tmp_path, recs, outcomes={TEST: "passed"})
    first = derive(inp, all_of(recs))
    assert [(v.kind, v.id) for v in first] == [
        ("claim", "FEAT-001.C1"), ("claim", "FEAT-001.C2"), ("claim", "FEAT-002.C1"),
        ("requirement", "REQ-F-001"), ("requirement", "REQ-F-002"),
        ("invariant", "INV-001"), ("invariant", "INV-002"),
    ]
    assert derive(inp, all_of(recs)) == first


# --- tests_needed --------------------------------------------------------------------------------

def test_tests_needed_groups_refs_by_runner_module(tmp_path):
    files = dict(FILES) | {
        "tests/qa/test_s.py": "def test_s():\n    assert True\n",
        "tests/test_inv.py": "def test_i():\n    assert True\n",
    }
    recs = [
        feature(),  # MOD-billing has a runner
        feature(claim("FEAT-002.C1", requirement=None, code="sessions/s.py#g", test="tests/qa/test_s.py::test_s"),
                id="FEAT-002", module="MOD-sessions"),  # no runner: falls back to the test file's module
        invariant(tests=("tests/test_inv.py::test_i", "tests/test_seats.py::test_gone"),
                  governs=["sessions/**", "MOD-sessions"]),  # no runner anywhere: None; deleted test skipped
        requirement(features=[], modules=["MOD-billing"]),
    ]
    marks = {"tests/test_seats.py": [("verify", "REQ-F-001", 5, TEST_B)]}
    inp = make_input(tmp_path, recs, markers=marks, files=files)
    assert tests_needed(inp, all_of(recs)) == {
        "MOD-billing": [TEST, TEST_B],
        "MOD-qa": ["tests/qa/test_s.py::test_s"],
        None: ["tests/test_inv.py::test_i"],
    }


def test_requirement_runner_falls_back_to_feature_module(tmp_path):
    recs = [Feature(id="FEAT-002", title="t", module="MOD-billing"), requirement(features=["FEAT-002"], modules=[])]
    marks = {"tests/test_seats.py": [("verify", "REQ-F-001", 5, TEST_B)]}
    inp = make_input(tmp_path, recs, markers=marks)
    assert tests_needed(inp, all_of(recs)) == {"MOD-billing": [TEST_B]}


def test_tests_needed_covers_requirement_claims_outside_affected(tmp_path):
    recs = [feature(), requirement()]
    inp = make_input(tmp_path, recs)
    assert tests_needed(inp, Affected(frozenset(), frozenset({"REQ-F-001"}), frozenset())) == {"MOD-billing": [TEST]}


def test_snapshot_parses_head_files_before_the_test_run(tmp_path):
    """After `snapshot`, a test run that rewrites the anchor file or deletes a linked test changes no verdict."""
    from dataclasses import replace

    from kb.verify.engine import snapshot

    records = [feature(claim(code="billing/seats.py#gone"))]
    inp = snapshot(make_input(tmp_path, records), all_of(records))
    (tmp_path / "billing/seats.py").write_text("def gone():\n    return 1\n")  # what a malicious test could do
    (tmp_path / "tests/test_seats.py").unlink()
    v = {x.id: x for x in derive(replace(inp, outcomes={TEST: "passed"}), all_of(records))}["FEAT-001.C1"]
    assert state(v) == ("broken", "E0", ("anchor billing/seats.py#gone no longer resolves",), ())
    records = [feature(claim())]
    inp = snapshot(make_input(tmp_path, records), all_of(records))
    (tmp_path / "tests/test_seats.py").unlink()
    assert derive(replace(inp, outcomes={TEST: "passed"}), all_of(records))[0].status == "verified"
