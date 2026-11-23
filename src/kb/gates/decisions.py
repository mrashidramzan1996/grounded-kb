import re

from kb.gates.base import GateContext
from kb.issues import Issue

_GUARDED = re.compile(r"kb/(?:decisions/(ADR-[^/]+)|invariants/(INV-[^/]+))\.md")


def decision_guard(ctx: GateContext) -> list[Issue]:
    """An ADR or INV that exists at base must be byte-identical at head; new ones are fine (A-14: an accepted
    invariant, like a decision, changes only through a record that supersedes it)."""
    if ctx.base is None:
        return []
    issues = []
    for path, data in sorted(ctx.base.raw.items()):
        m = _GUARDED.fullmatch(path)
        if not m:
            continue
        head = ctx.repo.raw.get(path)
        if head == data:
            continue
        what = "deleted" if head is None else "modified"
        rid, kind = (m.group(1), "decision") if m.group(1) else (m.group(2), "invariant")
        issues.append(Issue("decision_guard", f"accepted {kind} {rid} was {what}; supersede it instead", path=path, record=rid))
    return issues
