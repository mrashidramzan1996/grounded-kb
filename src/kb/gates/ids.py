import re
from collections import Counter
from collections.abc import Iterator

from kb.config.validate import resolve_owner
from kb.gates.base import GateContext
from kb.issues import Issue
from kb.records.ids import TERM_ID
from kb.records.lifecycle import supersede_edges
from kb.records.models import (
    QUOTED_TYPES,
    Component,
    Decision,
    Evidence,
    Exclusion,
    Feature,
    Invariant,
    Requirement,
    Term,
)
from kb.records.repo import KbRepo


def _evidence_refs(ev: Evidence | None, repo: KbRepo) -> Iterator[str]:
    """Problems with quote sources: the source and its segment must exist."""
    for q in ev.quotes if ev else []:
        src, _, seg = q.source.partition("#")
        if src not in repo.sources:
            yield f"quote source {src} does not exist"
        elif seg not in {s.id for s in repo.segments(src)}:
            yield f"quote source {q.source}: no such segment"


def _ref_problems(rec, ctx: GateContext) -> Iterator[str]:
    repo, modules = ctx.repo, {m.id for m in ctx.config.modules}

    def module(m: str) -> Iterator[str]:
        if m not in modules:
            yield f"module {m} is not in kb.config.yaml"

    def record(r: str) -> Iterator[str]:
        if r not in repo.records:
            yield f"{r} does not exist"

    if isinstance(rec, (Requirement, Exclusion)):
        for f in rec.features:
            yield from record(f)
        for m in rec.modules:
            yield from module(m)
    if isinstance(rec, Requirement):
        # an actor written as a TERM id must name a term; any other actor is plain text
        if rec.actor is not None and re.fullmatch(TERM_ID, rec.actor):
            yield from record(rec.actor)
        for e in rec.entities:
            yield from record(e)
    if isinstance(rec, (Feature, Component)):
        yield from module(rec.module)
    if isinstance(rec, Feature):
        for r in rec.requirements:
            yield from record(r)
        for c in rec.claims:
            if c.requirement:
                yield from record(c.requirement)
    if isinstance(rec, (Decision, Invariant)):
        for g in rec.governs:
            yield from module(g) if g.startswith("MOD-") else record(g)
    if isinstance(rec, QUOTED_TYPES):
        yield from _evidence_refs(rec.evidence, repo)
    if isinstance(rec, Term):
        yield from _evidence_refs(rec.evidence, repo)
        for rel in rec.relations:
            yield from record(rel.target)
        yield from _evidence_refs(Evidence(quotes=[r.quote for r in rec.relations]) if rec.relations else None, repo)


def _owner_ok(owner: str, ctx: GateContext) -> bool:
    return resolve_owner(owner, ctx.config) is not None or owner in {m.id for m in ctx.config.modules}


def _supersedes_issues(rid: str, rec, path: str, repo: KbRepo) -> Iterator[Issue]:
    target = rec.supersedes
    if target not in repo.records:
        yield Issue("ref.missing", f"{rid} supersedes {target}, which does not exist", path=path, record=rid)
    elif type(repo.records[target]) is not type(rec):
        yield Issue("supersede.type_mismatch", f"{rid} supersedes {target}, a different record type", path=path, record=rid)


def _cycles(repo: KbRepo) -> Iterator[list[str]]:
    nxt = {s: t for t, ss in supersede_edges(repo).items() for s in ss}
    seen: set[str] = set()
    for start in sorted(nxt):
        walk, cur = [], start
        while cur in nxt and cur not in seen and cur not in walk:
            walk.append(cur)
            cur = nxt[cur]
        if cur in walk:
            yield sorted(walk[walk.index(cur) :])
        seen.update(walk)


def id_gate(ctx: GateContext) -> list[Issue]:
    repo, issues = ctx.repo, []
    counts = Counter(rid for rid, _ in repo.id_paths)
    for rid, path in repo.id_paths:
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem != rid:
            issues.append(Issue("id.filename_mismatch", f"file name does not match id {rid}", path=path, record=rid))
        if counts[rid] > 1:
            issues.append(Issue("id.duplicate", f"{rid} is defined by more than one file", path=path, record=rid))

    for rid, rec in sorted(repo.records.items()):
        path = repo.paths[rid]
        for msg in _ref_problems(rec, ctx):
            issues.append(Issue("ref.missing", f"{rid}: {msg}", path=path, record=rid))
        if rec.owner is not None and not _owner_ok(rec.owner, ctx):
            issues.append(Issue("owner.unknown", f"{rid}: owner '{rec.owner}' is not a team, @handle or module", path=path, record=rid))
        if isinstance(rec, Feature):
            for c in rec.claims:
                if not c.id.startswith(f"{rid}."):
                    issues.append(Issue("id.claim_prefix", f"claim {c.id} must start with {rid}.", path=path, record=rid))
        if isinstance(rec, (Decision, Invariant)) and rec.supersedes:
            issues.extend(_supersedes_issues(rid, rec, path, repo))

    for target, supers in sorted(supersede_edges(repo).items()):
        if len(supers) > 1:
            issues.append(
                Issue(
                    "supersede.conflict",
                    f"{target} is superseded by both {' and '.join(supers)}",
                    path=repo.paths[target],
                    record=target,
                )
            )
    for cycle in _cycles(repo):
        issues.append(Issue("supersede.cycle", f"supersede cycle: {' -> '.join(cycle)}", path=repo.paths[cycle[0]], record=cycle[0]))

    for src_id in sorted(repo.sources):
        tri = repo.triage(src_id)
        for items in (tri.segments.values() if tri else ()):
            for item in [] if isinstance(items, str) else items:
                if item not in repo.records:
                    issues.append(Issue(
                        "ref.missing", f"triage of {src_id} names {item}, which does not exist",
                        path=f"kb/sources/{src_id}/triage.yaml", record=src_id,
                    ))
    return issues
