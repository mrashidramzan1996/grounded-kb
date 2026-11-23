"""Requirement status from code evidence: not_built -> built -> tested. ("verified" needs a test run: Phase 2.)"""

from pathlib import Path
from typing import Literal

from kb.code.registry import NoAdapter, SymbolIndex, UnsafePath, resolve_anchor, resolve_test
from kb.config.schema import KbConfig
from kb.gitfiles import repo_files
from kb.markers import evidence_files, scan_markers
from kb.records.lifecycle import live_ids
from kb.records.models import Feature, Requirement
from kb.records.repo import KbRepo

Status = Literal["not_built", "built", "tested"]


def _resolves(resolve, root: Path, index: SymbolIndex, ref: str | None) -> bool:
    if not ref:
        return False
    try:
        return resolve(root, ref, index) is not None
    except (NoAdapter, UnsafePath):
        return False


def requirement_status(root: Path, config: KbConfig, repo: KbRepo) -> dict[str, Status]:
    root = Path(root)
    markers = scan_markers(root, evidence_files(repo_files(root), config))
    impl = {m.id for m in markers if m.kind == "impl"}
    verify = {m.id for m in markers if m.kind == "verify"}
    index = SymbolIndex(root)

    built: set[str] = set()
    tested: set[str] = set()
    live = live_ids(repo)
    for feature, claim in repo.claims():
        if feature.id not in live:
            continue
        req = claim.requirement
        if not req:
            continue
        if claim.id in impl or _resolves(resolve_anchor, root, index, claim.code):
            built.add(req)
        if claim.id in verify or _resolves(resolve_test, root, index, claim.test):
            tested.add(req)
    built |= impl
    tested |= verify

    out: dict[str, Status] = {}
    for rid in sorted(live):
        if isinstance(repo.records[rid], Requirement):
            out[rid] = ("tested" if rid in tested else "built") if rid in built else "not_built"
    return out


def requirement_modules(config: KbConfig, repo: KbRepo) -> dict[str, list[str]]:
    """Configured modules per requirement (its own plus its features'), in config order."""
    configured = [m.id for m in config.modules]
    features = {r.id: r for r in repo.records.values() if isinstance(r, Feature)}
    out: dict[str, list[str]] = {}
    for rid, rec in repo.records.items():
        if isinstance(rec, Requirement):
            mods = set(rec.modules) | {features[f].module for f in rec.features if f in features}
            out[rid] = [m for m in configured if m in mods]
    return out


def summarize(config: KbConfig, repo: KbRepo, status: dict[str, Status]) -> dict:
    """{"requirements", "modules", "total"} for the CLI; "(no module)" appears only when it has requirements."""
    owners = requirement_modules(config, repo)

    def count(ids: list[str]) -> dict[str, int]:
        return {
            "built": sum(status[i] != "not_built" for i in ids),
            "tested": sum(status[i] == "tested" for i in ids),
            "total": len(ids),
        }

    modules = {m.id: count([i for i in status if m.id in owners[i]]) for m in config.modules}
    orphans = [i for i in status if not owners[i]]
    if orphans:
        modules["(no module)"] = count(orphans)
    return {"requirements": dict(sorted(status.items())), "modules": modules, "total": count(list(status))}
