from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from kb.issues import Issue

EventKind = Literal["session_start", "stop", "pre_commit"]


@dataclass(frozen=True)
class HookEvent:
    kind: EventKind
    agent: str
    stop_active: bool = False


@dataclass
class HookResult:
    brief: str = ""
    errors: list[Issue] = field(default_factory=list)


class AgentIntegration(Protocol):
    name: str

    def install(self, root: Path) -> list[str]:
        """Write the agent's hook configuration under root; return the lines to show the user."""
        ...

    def parse(self, event: EventKind, payload: dict) -> HookEvent:
        """The agent's hook payload (already JSON-decoded, possibly empty) as a HookEvent."""
        ...

    def respond(self, event: HookEvent, result: HookResult) -> tuple[str, int]:
        """(stdout text, exit code) in the format this agent expects."""
        ...
