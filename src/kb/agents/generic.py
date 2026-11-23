"""Any other agent, or a human: a git pre-commit hook running `kb check --changed --strict`."""

from pathlib import Path

from kb.ports.agents import EventKind, HookEvent, HookResult
from kb.safewrite import WriteRefused, check_target, write_atomic

HOOK_LINE = "command -v kb >/dev/null 2>&1 && { kb hook pre-commit || exit $?; }"
HOOK_PATH = ".git/hooks/pre-commit"


class Generic:
    name = "generic"

    def install(self, root: Path) -> list[str]:
        if not (root / ".git").is_dir():
            raise WriteRefused(f"{root / '.git'} is not a directory; cannot install a git hook")
        if check_target(root, HOOK_PATH, "file"):
            return [f"{HOOK_PATH} exists; add this line to it:", HOOK_LINE]
        write_atomic(root, HOOK_PATH, f"#!/bin/sh\n{HOOK_LINE}\n", mode=0o755)
        return [f"wrote {HOOK_PATH}"]

    def parse(self, event: EventKind, payload: dict) -> HookEvent:
        return HookEvent(event, self.name)

    def respond(self, event: HookEvent, result: HookResult) -> tuple[str, int]:
        if event.kind == "session_start":
            return result.brief, 0
        if event.kind == "pre_commit" and result.errors:
            lines = [f"{i.severity} {i.code} {i.path or '-'} {i.record or '-'}: {' '.join(i.message.split())}" for i in result.errors]
            return "\n".join(lines) + "\n", 1
        return "", 0
