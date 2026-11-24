from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

Outcome = Literal["passed", "failed", "skipped", "missing", "flaky", "no_runner"]


@dataclass(frozen=True)
class TestRun:
    __test__ = False  # not a pytest class, despite the name

    outcomes: dict[str, Outcome]  # test ref -> outcome, one per requested ref
    log_tail: str  # last 4000 chars of stdout+stderr of the first run (stderr only, never in JSON)


class TestRunner(Protocol):
    __test__ = False

    def run(self, root: Path, refs: list[str]) -> TestRun: ...
