from kb.gates.base import GateContext
from kb.issues import Issue


def _inline(text: str) -> str:
    return " ".join(text.split())


def triage_gate(ctx: GateContext) -> list[Issue]:
    """One warning per source segment that has no triage entry."""
    issues = []
    for src_id in sorted(ctx.repo.sources):
        tri = ctx.repo.triage(src_id)
        triaged = tri.segments if tri else {}
        for seg in ctx.repo.segments(src_id):
            if seg.id not in triaged:
                issues.append(Issue(
                    "segment.untriaged",
                    f"`{src_id}#{seg.id}` \"{_inline(seg.heading)}\" has no triage entry",
                    "warning",
                    path=f"kb/sources/{src_id}/triage.yaml",
                    record=src_id,
                ))
    return issues
