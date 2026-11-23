"""Any other agent, or a human: a git pre-commit hook running `kb check --changed --strict`."""

from pathlib import Path

from kb.gitfiles import GitError, git_common_dir, git_path
from kb.ports.agents import EventKind, HookEvent, HookResult
from kb.safewrite import WriteRefused, check_target, write_atomic

# `if` (not `&&`): when kb isn't installed the hook must succeed, or every commit would be blocked.
HOOK_LINE = "if command -v kb >/dev/null 2>&1; then kb hook pre-commit || exit $?; fi"


def hooks_dir(root: Path) -> Path:
    """Where git looks for hooks (honours worktrees and core.hooksPath). Refuses a directory outside the
    repository (and its .git): a global core.hooksPath would make every repository run kb."""
    try:
        directory, common = git_path(root, "hooks"), git_common_dir(root)
    except (OSError, GitError) as e:
        raise WriteRefused(f"{root} is not a git repository; cannot install a git hook") from e
    if not (directory.is_relative_to(Path(root).resolve()) or directory.is_relative_to(common)):
        raise WriteRefused(
            f"git's hooks directory {directory} is outside this repository (a global core.hooksPath?), so "
            f"other repositories would run it; nothing written. To use it anyway, add this line to "
            f"{directory / 'pre-commit'} yourself: {HOOK_LINE}"
        )
    return directory


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
