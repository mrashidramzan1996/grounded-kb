"""Claude Code hooks.

Sources (fetched 2026-11-23): https://code.claude.com/docs/en/hooks
- SessionStart: plain text on stdout is added to Claude's context.
- Stop input has `stop_hook_active` (true when Claude is already continuing because of a stop hook).
- Stop decision output, exit 0: {"decision": "block", "reason": "..."} keeps Claude working; no output lets it stop.
"""

import json
from pathlib import Path

from kb.agents.settings import merge_hooks
from kb.ports.agents import EventKind, HookEvent, HookResult

REASON_CODES = 3


def block_reason(result: HookResult) -> str:
    codes = list(dict.fromkeys(i.code for i in result.errors))[:REASON_CODES]
    return f"kb check: {len(result.errors)} errors — {', '.join(codes)}; run `kb check --changed`"


class ClaudeCode:
    name = "claude-code"

    def install(self, root: Path) -> list[str]:
        return merge_hooks(root, ".claude/settings.json", self.name)

    def parse(self, event: EventKind, payload: dict) -> HookEvent:
        return HookEvent(event, self.name, stop_active=payload.get("stop_hook_active") is True)

    def respond(self, event: HookEvent, result: HookResult) -> tuple[str, int]:
        if event.kind == "session_start":
            return result.brief, 0
        if event.kind == "stop" and result.errors and not event.stop_active:
            return json.dumps({"decision": "block", "reason": block_reason(result)}, ensure_ascii=False), 0
        return "", 0
