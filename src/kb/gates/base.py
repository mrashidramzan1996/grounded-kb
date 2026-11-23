from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kb.config.schema import KbConfig
from kb.issues import Issue
from kb.records.repo import KbRepo


@dataclass
class GateContext:
    root: Path
    config: KbConfig
    repo: KbRepo
    base: KbRepo | None = None
    base_ref: str | None = None  # the commit `base` was read from


Gate = Callable[[GateContext], list[Issue]]


def run_gates(ctx: GateContext, gates: list[Gate]) -> list[Issue]:
    """Run every gate; repo load errors are included. Output order is deterministic."""
    issues = list(ctx.repo.errors)
    for gate in gates:
        issues.extend(gate(ctx))
    return sorted(issues, key=lambda i: (*i.sort_key, i.message))
