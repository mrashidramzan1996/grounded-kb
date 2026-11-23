"""Codex hooks.

Sources (fetched 2026-11-23): https://developers.openai.com/codex/hooks, redirecting to
https://learn.chatgpt.com/docs/hooks
- Config: `.codex/hooks.json`, {"hooks": {"<Event>": [{"matcher"?, "hooks": [{"type": "command", "command": ...}]}]}}.
- SessionStart input: session_id, transcript_path, cwd, hook_event_name, model, permission_mode, source.
  Plain text on stdout is added as developer context.
- Stop input adds turn_id, `stop_hook_active` (this turn was already continued by Stop) and last_assistant_message.
- Stop output: JSON is required on exit 0 (plain text is invalid). {"decision": "block", "reason": "..."} makes Codex
  continue with `reason` as the new prompt; {} lets it stop. No hookSpecificOutput is defined for Stop.
- Non-managed hooks run only after review and trust in `/hooks`; a changed hook must be re-trusted.
"""

import json
from pathlib import Path

from kb.agents.claude_code import block_reason
from kb.agents.settings import merge_hooks
from kb.ports.agents import EventKind, HookEvent, HookResult

TRUST_NOTICE = "Codex runs project hooks only in a trusted project, after you review them in `/hooks`."


class Codex:
    name = "codex"

    def install(self, root: Path) -> list[str]:
        return merge_hooks(root, ".codex/hooks.json", self.name) + [TRUST_NOTICE]

    def parse(self, event: EventKind, payload: dict) -> HookEvent:
        return HookEvent(event, self.name, stop_active=payload.get("stop_hook_active") is True)

    def respond(self, event: HookEvent, result: HookResult) -> tuple[str, int]:
        if event.kind == "session_start":
            return result.brief, 0
        if event.kind == "stop":
            if result.errors and not event.stop_active:
                return json.dumps({"decision": "block", "reason": block_reason(result)}, ensure_ascii=False), 0
            return "{}", 0
        return "", 0
