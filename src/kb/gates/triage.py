from kb.gates.base import GateContext
from kb.issues import Issue
from kb.records.ids import valid_segment_id
from kb.records.models import NONE_CODES, Feature, Requirement, Term, none_code
from kb.records.repo import KbRepo
from kb.textfmt import inline_code


def _requirement_sources(repo: KbRepo, rids) -> set[str]:
    out: set[str] = set()
    for rid in rids:
        rec = repo.records.get(rid)
        if isinstance(rec, Requirement):
            out |= {q.source for q in rec.evidence.quotes}
    return out


def cited_segments(repo: KbRepo, rid: str) -> set[str]:
    """The `SRC#seg` refs record `rid` quotes: its own evidence, or for a feature (and each claim) the quotes of the
    requirements it links, and for a term the requirements that name it as an entity."""
    rec = repo.records.get(rid)
    if rec is None:
        return set()
    evidence = getattr(rec, "evidence", None)
    out = {q.source for q in evidence.quotes} if evidence else set()
    if isinstance(rec, Feature):
        out |= _requirement_sources(repo, [*rec.requirements, *(c.requirement for c in rec.claims if c.requirement)])
    elif isinstance(rec, Term):
        out |= _requirement_sources(repo, [r for r, req in repo.records.items()
                                           if isinstance(req, Requirement) and rid in getattr(req, "entities", ())])
    return out


def triage_gate(ctx: GateContext) -> list[Issue]:
    """Every segment has a triage entry; a list names a record that quotes the segment; a `none:` reason has a code."""
    repo, issues = ctx.repo, []
    for src_id in sorted(repo.sources):
        path = f"kb/sources/{src_id}/triage.yaml"
        tri = repo.triage(src_id)
        triaged = tri.segments if tri else {}
        for seg in repo.segments(src_id):
            if seg.id not in triaged:
                issues.append(Issue(
                    "segment.untriaged",
                    f"`{src_id}#{seg.id}` {inline_code(seg.heading)} has no triage entry",
                    "warning",
                    path=path,
                    record=src_id,
                ))
        for seg_id, value in sorted(triaged.items()):
            ref = f"{src_id}#{seg_id}" if valid_segment_id(seg_id) else inline_code(f"{src_id}#{seg_id}")
            if isinstance(value, str):
                code = none_code(value)
                if code is None:
                    issues.append(Issue(
                        "triage.none_uncoded",
                        f"{ref}: {inline_code(value)} gives no reason code; write `none: <code>[: reason]` with one of "
                        f"{', '.join(NONE_CODES)}",
                        "warning", path, src_id,
                    ))
                elif code == "out-of-scope":
                    issues.append(Issue("triage.use_exclusion", f"{ref}: triaged out-of-scope; record it as an EXCL record instead",
                                        "warning", path, src_id))
                continue
            known = [rid for rid in value if rid in repo.records]  # a missing one is ref.missing (id_gate)
            if known and not any(f"{src_id}#{seg_id}" in cited_segments(repo, rid) for rid in known):
                # a warning here, so a 0.3 repo still passes; the ingest and redraft reports make it an error
                issues.append(Issue("triage.not_quoted", f"{ref}: triage names {', '.join(value)} but none of them quotes this segment",
                                    "warning", path, src_id))
    return issues
