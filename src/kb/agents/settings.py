"""Merging kb hook entries into an agent's JSON hooks file (Claude Code settings, Codex hooks.json)."""

import json
from pathlib import Path

from kb.safewrite import WriteRefused, check_target, read_text, write_atomic

EVENTS = {"SessionStart": "session-start", "Stop": "stop"}


def hook_command(event: str, agent: str) -> str:
    # kb missing must never break the session, so the command is a no-op then
    return f"command -v kb >/dev/null 2>&1 && kb hook {event} --agent {agent} || true"


def _has(groups: list, command: str) -> bool:
    return any(isinstance(g, dict) and any(isinstance(h, dict) and h.get("command") == command for h in g.get("hooks", [])) for g in groups)


def merge_hooks(root: Path, rel: str, agent: str) -> list[str]:
    """Add the SessionStart and Stop entries to `rel`, keeping every other key and entry. Idempotent.
    Raises WriteRefused (nothing written) for a symlinked or non-regular target or content that is not a JSON object."""
    exists = check_target(root, rel, "file")
    try:
        data = json.loads(read_text(root, rel)) if exists else {}
    except ValueError as e:
        raise WriteRefused(f"{rel} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise WriteRefused(f"{rel} is not a JSON object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise WriteRefused(f"{rel}: 'hooks' is not an object")
    for name, event in EVENTS.items():
        groups = hooks.setdefault(name, [])
        if not isinstance(groups, list):
            raise WriteRefused(f"{rel}: hooks.{name} is not a list")
        command = hook_command(event, agent)
        if not _has(groups, command):
            groups.append({"hooks": [{"type": "command", "command": command}]})
    text = json.dumps(data, indent=2) + "\n"
    if exists and read_text(root, rel) == text:
        return [f"{rel} already up to date"]
    write_atomic(root, rel, text)
    return [f"wrote {rel}"]
