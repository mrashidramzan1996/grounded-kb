"""Derived verification states (A-4): code anchors, test outcomes and cited segments in, verdicts out.

Nothing here is stored: every verdict is recomputed from the head tree, the change set and the test run (A-1).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kb.code.registry import (
    NoAdapter,
    SymbolIndex,
    UnsafePath,
    resolve_anchor,
    resolve_test,
)
from kb.config.schema import KbConfig
from kb.config.validate import modules_for
from kb.ports.tests import Outcome
from kb.quotes.match import find_quote
from kb.records.lifecycle import live_ids
from kb.records.models import Feature, FeatureClaim, Invariant, Requirement
from kb.records.repo import KbRepo
from kb.testrun import runner_for
from kb.verify.affected import Affected
from kb.verify.changes import ChangeSet
from kb.verify.index import AnchorIndex

State = Literal["verified", "stale", "broken", "unverified", "built", "not_built"]
Level = Literal["E0", "E1", "E2"]
Kind = Literal["claim", "requirement", "invariant"]

_KIND_ORDER: dict[str, int] = {"claim": 0, "requirement": 1, "invariant": 2}
NO_RUNNER_HINT = "no test command: set modules[].tests"


@dataclass(frozen=True)
class Verdict:
    id: str
    kind: Kind
    status: State
    level: Level
    reasons: tuple[str, ...]  # sentences, in rule order
    notes: tuple[str, ...]  # sorted subset of {"changed", "co-changed", "flaky"}
    tests: tuple[str, ...]  # sorted test refs considered


@dataclass(frozen=True)
class EngineInput:
    root: Path
    config: KbConfig
    repo: KbRepo  # head
    base_repo: KbRepo | None
    changes: ChangeSet | None
    index: AnchorIndex
    outcomes: dict[str, Outcome]  # every ref in tests_needed(...) for the affected set


@dataclass(frozen=True)
class _TestResult:
    """Claim rules 6-9 over a set of test refs; status None means every test passed."""

    status: State | None
    reasons: tuple[str, ...]
    flaky: bool


class _Engine:
    def __init__(self, inp: EngineInput) -> None:
        self.inp = inp
        self.symbols = SymbolIndex(inp.root)
        live = live_ids(inp.repo)
        self.claims = {c.id: (f, c) for f, c in inp.repo.claims() if f.id in live}
        self.records = {rid: r for rid, r in sorted(inp.repo.records.items()) if rid in live}
        self._claim_verdicts: dict[str, Verdict] = {}

    # --- links ----------------------------------------------------------------------------------

    def claim_tests(self, claim: FeatureClaim) -> list[str]:
        return sorted({*([claim.test] if claim.test else []), *self.inp.index.tests_for(claim.id)})

    def requirement_claims(self, rid: str) -> list[tuple[Feature, FeatureClaim]]:
        return [(f, c) for cid, (f, c) in sorted(self.claims.items()) if c.requirement == rid]

    def requirement_tests(self, rid: str) -> list[str]:
        refs = {t for _, c in self.requirement_claims(rid) for t in self.claim_tests(c)}
        return sorted(refs | set(self.inp.index.tests_for(rid)))

    def invariant_tests(self, inv: Invariant) -> list[str]:
        return sorted(set(inv.tests) | set(self.inp.index.tests_for(inv.id)))

    def test_exists(self, ref: str) -> bool | None:
        """True if the test resolves, False if it is gone (or its path is unsafe), None when no adapter can tell."""
        try:
            return resolve_test(self.inp.root, ref, self.symbols) is not None
        except UnsafePath:
            return False
        except NoAdapter:
            return None  # the runner decides: a test it cannot find reports `missing`

    def anchor_resolves(self, anchor: str | None) -> bool:
        if not anchor:
            return False
        try:
            return resolve_anchor(self.inp.root, anchor, self.symbols) is not None
        except (NoAdapter, UnsafePath):
            return False

    # --- rules 6-9 -------------------------------------------------------------------------------

    def judge_tests(self, refs: list[str]) -> _TestResult:
        deleted = [r for r in refs if self.test_exists(r) is False]
        if deleted:
            return _TestResult("broken", tuple(f"linked test {r} was deleted" for r in deleted), False)
        outcomes = {r: self.inp.outcomes.get(r, "missing") for r in refs}  # absent from the run: not passing
        if "no_runner" in outcomes.values():
            return _TestResult("unverified", (NO_RUNNER_HINT,), False)
        failing = [(r, o) for r, o in outcomes.items() if o in ("failed", "skipped", "missing")]
        if failing:
            return _TestResult("broken", tuple(f"{r} {o}" for r, o in failing), False)
        flaky = [r for r, o in outcomes.items() if o == "flaky"]
        if flaky:
            return _TestResult("unverified", tuple(f"{r} is flaky; quarantined" for r in flaky), True)
        return _TestResult(None, (), False)

    # --- claims ----------------------------------------------------------------------------------

    def claim_verdict(self, cid: str) -> Verdict:
        if cid not in self._claim_verdicts:
            self._claim_verdicts[cid] = self._derive_claim(self.claims[cid][1])
        return self._claim_verdicts[cid]

    def _derive_claim(self, claim: FeatureClaim) -> Verdict:
        changes = self.inp.changes
        tests = self.claim_tests(claim)
        notes: set[str] = set()

        def done(status: State, level: Level, *reasons: str) -> Verdict:
            return Verdict(claim.id, "claim", status, level, reasons, tuple(sorted(notes)), tuple(tests))

        if claim.code:
            path = claim.code.partition("#")[0]
            if changes is not None and path in changes.files and any(t.partition("::")[0] in changes.files for t in tests):
                notes.add("co-changed")
            try:
                resolved = resolve_anchor(self.inp.root, claim.code, self.symbols) is not None
            except NoAdapter:
                return done("unverified", "E0", f"no code adapter for {path}")
            except UnsafePath:
                return done("broken", "E0", f"anchor {claim.code} is not a safe path")
            if not resolved:
                hint = "; file has syntax errors" if self.symbols.has_syntax_errors(path) else ""
                return done("broken", "E0", f"anchor {claim.code} no longer resolves{hint}")
            base_fp, head_fp = changes.fingerprints.get(claim.code, (None, None)) if changes else (None, None)
            if base_fp is not None and head_fp is not None and base_fp != head_fp:
                notes.add("changed")
        elif not self.inp.index.impl_files(claim.id):
            return done("unverified", "E0", "no code anchor")
        # an impl marker without `code` has no anchor to resolve: E1, continue at the tests

        if not tests:
            return done("unverified", "E1", "no linked test")
        result = self.judge_tests(tests)
        if result.flaky:
            notes.add("flaky")
        if result.status is None:
            return done("verified", "E2")
        return done(result.status, "E1", *result.reasons)

    # --- requirements ----------------------------------------------------------------------------

    def _intent(self, req: Requirement) -> tuple[State | None, list[str]]:
        repo, changes = self.inp.repo, self.inp.changes
        broken: list[str] = []
        stale: list[str] = []
        for q in req.evidence.quotes:
            src, _, seg_id = q.source.partition("#")
            seg = next((s for s in repo.segments(src) if s.id == seg_id), None)
            if src not in repo.sources or seg is None:
                broken.append(f"cited segment {q.source} is missing")
            elif find_quote(repo.source_text(src)[seg.start : seg.end], q.text) is None:
                broken.append(f"quote not found in {q.source}")
            elif changes is not None and changes.segments.get(q.source) == "changed":
                stale.append(f"cited segment {q.source} changed; needs intent approvers' review")
        if broken:
            return "broken", _unique(broken)
        if stale:
            return "stale", _unique(stale)
        return None, []

    def _code_side(self, req: Requirement, tests: list[str]) -> tuple[State, list[str], bool]:
        index = self.inp.index
        claims = self.requirement_claims(req.id)
        broken = [c.id for _, c in claims if self.claim_verdict(c.id).status == "broken"]
        if broken:
            return "broken", [f"claim {cid} is broken" for cid in broken], False
        built = (
            bool(index.impl_files(req.id))
            or any(self.anchor_resolves(c.code) or index.impl_files(c.id) for _, c in claims)
        )
        if not built:
            return "not_built", ["no implementation evidence"], False
        if not tests:
            return "built", ["no linked test"], False
        result = self.judge_tests(tests)
        return result.status or "verified", list(result.reasons), result.flaky

    def requirement_verdict(self, req: Requirement) -> Verdict:
        tests = self.requirement_tests(req.id)
        intent, intent_reasons = self._intent(req)
        code, code_reasons, flaky = self._code_side(req, tests)
        status: State = "broken" if "broken" in (intent, code) else intent or code
        level: Level = "E2" if status == "verified" else "E1" if status == "built" else "E0"
        notes = ("flaky",) if flaky else ()
        return Verdict(req.id, "requirement", status, level, tuple(intent_reasons + code_reasons), notes, tuple(tests))

    # --- invariants ------------------------------------------------------------------------------

    def invariant_verdict(self, inv: Invariant) -> Verdict:
        tests = self.invariant_tests(inv)

        def done(status: State, level: Level, reasons: tuple[str, ...], notes: tuple[str, ...] = ()) -> Verdict:
            return Verdict(inv.id, "invariant", status, level, reasons, notes, tuple(tests))

        if not tests:
            return done("broken", "E0", ("invariant lists no tests",))
        result = self.judge_tests(tests)
        if result.status is None:
            return done("verified", "E2", ())
        return done(result.status, "E1", result.reasons, ("flaky",) if result.flaky else ())

    # --- runner modules --------------------------------------------------------------------------

    def configured(self, ids: Iterable[str]) -> list[str]:
        """`ids` that are configured modules, in the given order."""
        known = {m.id for m in self.inp.config.modules}
        return [i for i in ids if i in known]

    def runner_module(self, record_modules: list[str], ref: str) -> str | None:
        config = self.inp.config
        candidates = [*record_modules, *modules_for(ref.partition("::")[0], config)]
        return next((m for m in candidates if runner_for(config, m) is not None), None)

    def requirement_modules(self, req: Requirement) -> list[str]:
        own = self.configured(req.modules)
        if own:
            return own[:1]
        feats = {r.module for fid in req.features if isinstance(r := self.records.get(fid), Feature)}
        return [m.id for m in self.inp.config.modules if m.id in feats]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _selected(engine: _Engine, affected: Affected):
    """The live (kind, record) pairs `affected` names, in output order."""
    for cid in sorted(affected.claims):
        if cid in engine.claims:
            yield "claim", engine.claims[cid]
    for kind, model, ids in (("requirement", Requirement, affected.requirements), ("invariant", Invariant, affected.invariants)):
        for rid in sorted(ids):
            rec = engine.records.get(rid)
            if isinstance(rec, model):
                yield kind, rec


def tests_needed(inp: EngineInput, affected: Affected) -> dict[str | None, list[str]]:
    """The test refs `derive` needs outcomes for, by the module whose runner runs them (None: no runner).

    `inp.outcomes` is ignored. Tests that no longer exist are left out: they break their records without a run.
    """
    engine = _Engine(inp)
    wanted: dict[str | None, set[str]] = {}

    def need(record_modules: list[str], refs: Iterable[str]) -> None:
        for ref in refs:
            if engine.test_exists(ref) is not False:
                wanted.setdefault(engine.runner_module(record_modules, ref), set()).add(ref)

    for kind, item in _selected(engine, affected):
        if kind == "claim":
            feat, claim = item
            need([feat.module], engine.claim_tests(claim))
        elif kind == "requirement":
            for feat, claim in engine.requirement_claims(item.id):
                need([feat.module], engine.claim_tests(claim))
            need(engine.requirement_modules(item), inp.index.tests_for(item.id))
        else:
            need(engine.configured(item.governs)[:1], engine.invariant_tests(item))
    return {k: sorted(v) for k, v in sorted(wanted.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))}


tests_needed.__test__ = False  # type: ignore[attr-defined]  # not a pytest test, despite the name


def derive(inp: EngineInput, affected: Affected) -> list[Verdict]:
    """A verdict for each live id in `affected`, sorted by kind (claim, requirement, invariant) then id."""
    engine = _Engine(inp)
    out: list[Verdict] = []
    for kind, item in _selected(engine, affected):
        if kind == "claim":
            out.append(engine.claim_verdict(item[1].id))
        elif kind == "requirement":
            out.append(engine.requirement_verdict(item))
        else:
            out.append(engine.invariant_verdict(item))
    return sorted(out, key=lambda v: (_KIND_ORDER[v.kind], v.id))
