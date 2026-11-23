import re

from kb.gates.base import GateContext
from kb.issues import Issue

_ADR_PATH = re.compile(r"kb/decisions/(ADR-[^/]+)\.md")


def decision_guard(ctx: GateContext) -> list[Issue]:
    """An ADR that exists at base must be byte-identical at head; new ADRs are fine."""
    if ctx.base is None:
        return []
    issues = []
    for path, data in sorted(ctx.base.raw.items()):
        m = _ADR_PATH.fullmatch(path)
        if not m:
            continue
        head = ctx.repo.raw.get(path)
        if head == data:
            continue
        what = "deleted" if head is None else "modified"
        issues.append(Issue("decision_guard", f"accepted decision {m.group(1)} was {what}; supersede it instead", path=path, record=m.group(1)))
    return issues
