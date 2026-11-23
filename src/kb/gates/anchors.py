import os
from collections.abc import Callable
from pathlib import Path

from kb.code.registry import NoAdapter, SymbolIndex, UnsafePath, resolve_anchor, resolve_test
from kb.gates.base import GateContext
from kb.gitfiles import GitError, repo_files
from kb.issues import Issue
from kb.markers import marker_eligible, scan_markers
from kb.ports.code import Symbol
from kb.records.models import Invariant


def _walk(root: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):  # os.walk does not follow symlinked dirs
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        rel = Path(dirpath).relative_to(root)
        out.extend((rel / f).as_posix() for f in sorted(filenames))
    return out


def _files(root: Path) -> list[str]:
    try:
        files = repo_files(root)
    except (GitError, OSError):
        files = _walk(root)
    return [f for f in files if marker_eligible(f)]


def _resolve(resolver: Callable[..., Symbol | None], index: SymbolIndex, ref: str, missing: str, path: str | None, rid: str) -> Issue | None:
    try:
        if resolver(index.root, ref, index) is not None:
            return None
    except UnsafePath as e:
        return Issue("anchor.invalid_path", f"{rid}: {e}", path=path, record=rid)
    except NoAdapter:
        return Issue("anchor.no_adapter", f"{rid}: no code adapter for {ref}; left unverified", "warning", path, rid)
    hint = "; file has syntax errors; symbols may be missing" if index.has_syntax_errors(ref.partition("#")[0].partition("::")[0]) else ""
    return Issue(missing, f"{rid}: {ref} does not resolve{hint}", path=path, record=rid)


def anchor_gate(ctx: GateContext) -> list[Issue]:
    repo, root, issues = ctx.repo, ctx.root, []
    markers = scan_markers(root, _files(root))
    index = SymbolIndex(root)
    for feature, claim in repo.claims():
        path = repo.paths[feature.id]
        if claim.code is not None:
            issue = _resolve(resolve_anchor, index, claim.code, "anchor.unresolved", path, claim.id)
            if issue:
                issues.append(issue)
        if claim.test is not None:
            issue = _resolve(resolve_test, index, claim.test, "test.missing", path, claim.id)
            if issue:
                issues.append(issue)
        elif not any(m.kind == "verify" and m.id == claim.id for m in markers):
            issues.append(Issue("claim.no_test", f"{claim.id} has no test and no r[verify {claim.id}] marker", "warning", path, claim.id))
    for rid, rec in sorted(repo.records.items()):
        if not isinstance(rec, Invariant):
            continue
        path = repo.paths[rid]
        if not rec.tests:
            issues.append(Issue("invariant.no_test", f"{rid} lists no tests", "warning", path, rid))
        for ref in rec.tests:
            issue = _resolve(resolve_test, index, ref, "test.missing", path, rid)
            if issue:
                issues.append(issue)
    known = set(repo.records) | {c.id for _, c in repo.claims()}
    for m in markers:
        if m.id not in known:
            issues.append(Issue("marker.unknown_id", f"r[{m.kind} {m.id}] names no record or claim", path=m.path))
    return issues
