"""Claude Code hooks.

Sources (fetched 2026-11-23): https://code.claude.com/docs/en/hooks
- SessionStart: plain text on stdout is added to Claude's context.
- Stop input has `stop_hook_active` (true when Claude is already continuing because of a stop hook).
- Stop decision output, exit 0: {"decision": "block", "reason": "..."} keeps Claude working; no output lets it stop.
"""

import json
from importlib import resources
from pathlib import Path

from kb.agents.settings import merge_hooks
from kb.ports.agents import EventKind, HookEvent, HookResult
from kb.rules import render
from kb.safewrite import WriteRefused, check_target, read_text, write_atomic

REASON_CODES = 3
SKILL = ".claude/skills/kb/SKILL.md"
SKILL_MARKER = "<!-- written by kb -->"


def block_reason(result: HookResult) -> str:
    codes = list(dict.fromkeys(i.code for i in result.errors))[:REASON_CODES]
    return f"kb check: {len(result.errors)} errors — {', '.join(codes)}; run `kb check --changed`"


class ClaudeCode:
    name = "claude-code"

    def install(self, root: Path) -> list[str]:
        """The kb skill, then the hooks in .claude/settings.json. Raises WriteRefused, with nothing written, when the
        skill exists without kb's marker (it is the user's own) or is unsafe to write; a failed skill write leaves
        settings.json untouched."""
        text = render((resources.files("kb.init") / "templates" / "skill_kb.md").read_text(encoding="utf-8"))
        current = read_text(root, SKILL) if check_target(root, SKILL, "file") else None
        if current is not None and SKILL_MARKER not in current:
            raise WriteRefused(f"{SKILL} exists and was not written by kb (no {SKILL_MARKER} line); move it away to install the kb skill")
        if current == text:
            skill = f"{SKILL} already up to date"
        else:
            write_atomic(root, SKILL, text)
            skill = f"wrote {SKILL}"
        return [*merge_hooks(root, ".claude/settings.json", self.name), skill]

    def parse(self, event: EventKind, payload: dict) -> HookEvent:
        return HookEvent(event, self.name, stop_active=payload.get("stop_hook_active") is True)

    def respond(self, event: HookEvent, result: HookResult) -> tuple[str, int]:
        if event.kind == "session_start":
            return result.brief, 0
        if event.kind == "stop" and result.errors and not event.stop_active:
            return json.dumps({"decision": "block", "reason": block_reason(result)}, ensure_ascii=False), 0
        return "", 0
