"""Which live claims, requirements and invariants a change set can have invalidated."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from kb.config.schema import KbConfig
from kb.deps import imported_top_levels
from kb.records.lifecycle import live_ids
from kb.records.models import Feature, FeatureClaim, Invariant, Requirement
from kb.records.repo import KbRepo
from kb.verify.changes import ChangeSet
from kb.verify.index import AnchorIndex

CONFIG_FILE = "kb.config.yaml"


@dataclass(frozen=True)
class Affected:
    claims: frozenset[str]
    requirements: frozenset[str]
    invariants: frozenset[str]


def _file_of(ref: str | None, sep: str) -> set[str]:
    return {ref.split(sep, 1)[0]} if ref else set()


def _test_files(refs) -> set[str]:
    return {f for ref in refs for f in _file_of(ref, "::")}


def _marker_files(index: AnchorIndex, mid: str) -> tuple[set[str], set[str]]:
    """(files with an impl marker for `mid`, files of the tests a verify marker links to it)."""
    return set(index.impl_files(mid)), _test_files(index.tests_for(mid))


def _orphaned_requirements(repo: KbRepo, base_repo: KbRepo | None) -> set[str]:
    """Requirements a base claim backed that no live head claim backs any more (the claim was deleted, rejected or
    re-pointed): they may have lost their proof."""
    if base_repo is None:
        return set()
    base_live, live = live_ids(base_repo), live_ids(repo)
    head = {(c.id, c.requirement) for f, c in repo.claims() if f.id in live}
    return {
        c.requirement for f, c in base_repo.claims()
        if f.id in base_live and c.requirement and (c.id, c.requirement) not in head
    }


def affected(
    changes: ChangeSet | None, config: KbConfig, repo: KbRepo, index: AnchorIndex, base_repo: KbRepo | None = None
) -> Affected:
    live = live_ids(repo)
    features = [(f, c) for f, c in repo.claims() if f.id in live]
    reqs = [r for rid, r in sorted(repo.records.items()) if rid in live and isinstance(r, Requirement)]
    invs = [i for rid, i in sorted(repo.records.items()) if rid in live and isinstance(i, Invariant)]

    if changes is None or CONFIG_FILE in changes.files:
        return Affected(
            frozenset(c.id for _, c in features), frozenset(r.id for r in reqs), frozenset(i.id for i in invs)
        )

    files = set(changes.files)
    importing = {p for changed in files for p in index.importers_of(changed)}
    conftest_dirs = {PurePosixPath(f).parent for f in files if PurePosixPath(f).name == "conftest.py"}
    imported = set().union(*(imported_top_levels(list(c.imports)) for c in index.cards.values()))
    hot_modules = {
        m
        for card in index.cards.values()
        if imported_top_levels(list(card.imports)) & changes.packages
        for m in card.modules
    }
    if changes.packages - imported:  # no import matches: re-check every module with Python files
        hot_modules |= {m for p, c in index.cards.items() if p.endswith(".py") for m in c.modules}
    module_ids = {m.id for m in config.modules}

    def touched(code: set[str], tests: set[str]) -> bool:
        """Whether the change set reaches the record's files: one of them changed or imports a changed file (one
        hop), or a changed conftest.py sits at or above one of its test files."""
        pointed = code | tests
        return bool(
            pointed & files
            or pointed & importing
            or any(conftest_dirs.intersection(PurePosixPath(t).parents) for t in tests)
        )

    def record_touched(mid: str, code: set[str] | None = None, tests: set[str] | None = None) -> bool:
        """touched() over the record's own files plus those its impl and verify markers point at."""
        impl, marked = _marker_files(index, mid)
        return touched((code or set()) | impl, (tests or set()) | marked)

    def claim_hit(feat: Feature, claim: FeatureClaim) -> bool:
        return bool(
            record_touched(claim.id, _file_of(claim.code, "#"), _file_of(claim.test, "::"))
            or feat.module in hot_modules
            or repo.paths.get(feat.id) in files
        )

    def requirement_hit(req: Requirement) -> bool:
        modules = set(req.modules) | {
            f.module
            for fid in req.features
            if fid in live and isinstance(f := repo.records.get(fid), Feature)
        }
        return bool(
            record_touched(req.id)
            or modules & hot_modules
            or repo.paths.get(req.id) in files
            or any(q.source in changes.segments for q in req.evidence.quotes)
        )

    def invariant_hit(inv: Invariant) -> bool:
        return bool(
            record_touched(inv.id, tests=_test_files(inv.tests))
            or {g for g in inv.governs if g in module_ids} & hot_modules
            or repo.paths.get(inv.id) in files
            or (inv.evidence and any(q.source in changes.segments for q in inv.evidence.quotes))
        )

    claims = {c.id: c.requirement for f, c in features if claim_hit(f, c)}
    req_ids = {r.id for r in reqs}
    backed = set(claims.values()) | _orphaned_requirements(repo, base_repo)
    requirements = {r.id for r in reqs if requirement_hit(r)} | (backed & req_ids)
    return Affected(
        frozenset(claims),
        frozenset(requirements),
        frozenset(i.id for i in invs if invariant_hit(i)),
    )
