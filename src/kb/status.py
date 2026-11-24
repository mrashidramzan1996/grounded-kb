"""Requirement status from code evidence: not_built -> built -> tested; "verified" comes from the last `kb verify`."""

import json
from pathlib import Path
from typing import Literal

from kb.code.registry import NoAdapter, SymbolIndex, UnsafePath, resolve_anchor, resolve_test
from kb.config.schema import KbConfig
from kb.gitfiles import GitError, head_commit, is_dirty, repo_files
from kb.markers import evidence_files, scan_markers
from kb.records.lifecycle import live_ids
from kb.records.models import Feature, Requirement
from kb.records.repo import KbRepo
from kb.safepath import read_regular
from kb.verify.report import STATUS_PATH

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


def verified_ids(root: Path) -> set[str] | None:
    """Requirements the last `kb verify` found verified, when it checked every claim (scope "all") on HEAD and
    the tree is still clean; None otherwise (no status.json, a partial run, another commit, local changes,
    or unreadable). A run on the affected claims only would undercount."""
    root = Path(root)
    try:
        data = read_regular(root, STATUS_PATH)
        if data is None:
            return None
        doc = json.loads(data)
        if not isinstance(doc, dict) or doc.get("scope") != "all":
            return None
        if doc.get("head") != head_commit(root) or is_dirty(root, "."):
            return None
        verdicts = doc.get("verdicts")
        if not isinstance(verdicts, list):
            return None
        return {
            v["id"] for v in verdicts
            if isinstance(v, dict) and v.get("kind") == "requirement" and v.get("status") == "verified"
            and isinstance(v.get("id"), str)
        }
    except (UnsafePath, ValueError, GitError, OSError):
        return None


def summarize(config: KbConfig, repo: KbRepo, status: dict[str, Status], verified: set[str] | None = None) -> dict:
    """{"requirements", "modules", "total"} for the CLI; "(no module)" appears only when it has requirements.
    With `verified` (from verified_ids), each count also has "verified"."""
    owners = requirement_modules(config, repo)

    def count(ids: list[str]) -> dict[str, int]:
        out = {
            "built": sum(status[i] != "not_built" for i in ids),
            "tested": sum(status[i] == "tested" for i in ids),
            "total": len(ids),
        }
        if verified is not None:
            out["verified"] = sum(i in verified for i in ids)
        return out

    modules = {m.id: count([i for i in status if m.id in owners[i]]) for m in config.modules}
    orphans = [i for i in status if not owners[i]]
    if orphans:
        modules["(no module)"] = count(orphans)
    return {"requirements": dict(sorted(status.items())), "modules": modules, "total": count(list(status))}
