"""Any other agent, or a human: a git pre-commit hook running `kb check --changed --strict`."""

import subprocess
from pathlib import Path

from kb.ports.agents import EventKind, HookEvent, HookResult
from kb.safewrite import WriteRefused, check_target, write_atomic

HOOK_LINE = "command -v kb >/dev/null 2>&1 && { kb hook pre-commit || exit $?; }"


def hooks_dir(root: Path) -> Path:
    """Where git looks for hooks (honours worktrees and core.hooksPath)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise WriteRefused(f"{root} is not a git repository; cannot install a git hook") from e
    return (root / out).resolve()


class Generic:
    name = "generic"

    def install(self, root: Path) -> list[str]:
        directory = hooks_dir(root)
        shown = (directory / "pre-commit").as_posix()
        if check_target(directory, "pre-commit", "file"):
            return [f"{shown} exists; add this line to it:", HOOK_LINE]
        write_atomic(directory, "pre-commit", f"#!/bin/sh\n{HOOK_LINE}\n", mode=0o755)
        return [f"wrote {shown}"]

    def parse(self, event: EventKind, payload: dict) -> HookEvent:
        return HookEvent(event, self.name)

    def respond(self, event: HookEvent, result: HookResult) -> tuple[str, int]:
        if event.kind == "session_start":
            return result.brief, 0
        if event.kind == "pre_commit" and result.errors:
            lines = [f"{i.severity} {i.code} {i.path or '-'} {i.record or '-'}: {' '.join(i.message.split())}" for i in result.errors]
            return "\n".join(lines) + "\n", 1
        return "", 0
