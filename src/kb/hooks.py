"""Hook entry point shared by every agent. Changed paths come from git only; payloads carry nothing we trust
except the agent's own "already continuing" flag, and are parsed defensively."""

import json
import sys
from pathlib import Path

from kb.agents.registry import get_agent
from kb.check import run_check
from kb.context import session_brief
from kb.ports.agents import EventKind, HookResult
from kb.records.repo import KbRepo

EVENTS: dict[str, EventKind] = {"session-start": "session_start", "stop": "stop", "pre-commit": "pre_commit"}


def _payload(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _brief(root: Path) -> str:
    out = session_brief(root)
    failed = len(KbRepo.load(root).errors)
    if failed:
        out = f"⚠ {failed} KB files failed to load; run `kb check`\n" + out
    return out


def _errors(root: Path, *, changed_only: bool) -> list:
    return [i for i in run_check(root, changed_only=changed_only).issues if i.severity == "error"]


def run_hook(root, event_name: str, agent: str, stdin_text: str) -> tuple[str, int]:
    """(stdout, exit code). session-start and stop never fail the agent: on an internal error they print
    nothing blocking, exit 0 and leave one line on stderr. pre-commit exits 1 on errors, 2 on a tool error.
    Raises KeyError for an unknown event or agent."""
    root = Path(root)
    if event_name not in EVENTS:
        raise KeyError(f"unknown hook event {event_name!r}: use {', '.join(EVENTS)}")
    kind = EVENTS[event_name]
    integration = get_agent("generic" if kind == "pre_commit" else agent)
    event = integration.parse(kind, _payload(stdin_text))
    result = HookResult()
    try:
        if kind == "session_start":
            result.brief = _brief(root)
        elif kind == "stop":
            if not event.stop_active:
                result.errors = _errors(root, changed_only=True)
        else:
            result.errors = _errors(root, changed_only=True)
    except Exception as e:  # a broken tool must not trap the agent; pre-commit reports it as a tool error
        sys.stderr.write(f"kb hook {event_name}: {' '.join(str(e).split())}\n")
        if kind == "pre_commit":
            return "", 2
        return integration.respond(event, HookResult())
    return integration.respond(event, result)
