"""Which live claims, requirements and invariants a change set can have invalidated."""

from dataclasses import dataclass

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


def _marker_files(index: AnchorIndex, *ids: str) -> set[str]:
    files: set[str] = set()
    for mid in ids:
        files.update(index.impl_files(mid))
        for ref in index.tests_for(mid):
            files |= _file_of(ref, "::")
    return files


def affected(changes: ChangeSet | None, config: KbConfig, repo: KbRepo, index: AnchorIndex) -> Affected:
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
    hot_modules = {
        m
        for card in index.cards.values()
        if imported_top_levels(list(card.imports)) & changes.packages
        for m in card.modules
    }
    module_ids = {m.id for m in config.modules}

    def claim_hit(feat: Feature, claim: FeatureClaim) -> bool:
        anchor = _file_of(claim.code, "#")
        pointed = anchor | _file_of(claim.test, "::") | _marker_files(index, claim.id)
        return bool(
            pointed & files
            or anchor & importing
            or feat.module in hot_modules
            or repo.paths.get(feat.id) in files
        )

    def requirement_hit(req: Requirement) -> bool:
        modules = set(req.modules) | {
            f.module for fid in req.features if isinstance(f := repo.records.get(fid), Feature)
        }
        return bool(
            _marker_files(index, req.id) & files
            or modules & hot_modules
            or repo.paths.get(req.id) in files
            or any(q.source in changes.segments for q in req.evidence.quotes)
        )

    def invariant_hit(inv: Invariant) -> bool:
        pointed = {f for t in inv.tests for f in _file_of(t, "::")} | _marker_files(index, inv.id)
        return bool(
            pointed & files
            or {g for g in inv.governs if g in module_ids} & hot_modules
            or repo.paths.get(inv.id) in files
            or (inv.evidence and any(q.source in changes.segments for q in inv.evidence.quotes))
        )

    claims = {c.id: c.requirement for f, c in features if claim_hit(f, c)}
    requirements = {r.id for r in reqs if requirement_hit(r)} | {rid for rid in claims.values() if rid in {r.id for r in reqs}}
    return Affected(
        frozenset(claims),
        frozenset(requirements),
        frozenset(i.id for i in invs if invariant_hit(i)),
    )
