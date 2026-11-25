"""Hook entry point shared by every agent. Changed paths come from git only; payloads carry nothing we trust
except the agent's own "already continuing" flag, and are parsed defensively."""

import json
import sys
from pathlib import Path

from kb.agents.registry import get_agent
from kb.check import errors_at, run_check
from kb.context import session_brief
from kb.gitfiles import resolve_base
from kb.issues import Issue, new_since
from kb.ports.agents import EventKind, HookResult
from kb.records.repo import KbRepo
from kb.todo import build_todo

EVENTS: dict[str, EventKind] = {"session-start": "session_start", "stop": "stop", "pre-commit": "pre_commit"}
OWNER_TODO, SECRET = "config.owner_todo", "source.secret_in_original"


def _payload(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _todo_summary(root: Path) -> str:
    """A short note on the open kb work (`kb todo`), or "" when there is none. Never raises: the brief must always
    print, so a failing `kb todo` leaves only a note on stderr."""
    try:
        todo = build_todo(root)
    except Exception as e:
        sys.stderr.write(f"kb: note todo.failed: {type(e).__name__}: {' '.join(str(e).split())}\n")
        return ""
    if not (todo.do or todo.ask):
        return ""
    return (
        f"## kb to-do\n{_plural(len(todo.do), 'thing')} to do, {_plural(len(todo.ask), 'question')} for the user. "
        "Run `kb todo` and follow the playbook in AGENTS.md.\n"
    )


def _brief(root: Path) -> str:
    out = _todo_summary(root) + session_brief(root)
    failed = len(KbRepo.load(root).errors)
    if failed:
        out = f"⚠ {failed} KB files failed to load; run `kb check`\n" + out
    return out


def _note(i: Issue) -> None:
    sys.stderr.write(f"kb: note {i.code} {i.path or '-'}: {' '.join(i.message.split())}\n")


def _blocking(root: Path, kind: EventKind) -> list[Issue]:
    """The errors that block: those new since the merge-base with the default branch (all of them when none
    resolves). Human actions never block an agent and are noted on stderr instead: TODO owners (the agent
    cannot fix them) and secrets in original documents (it must not edit them). A new secret does block a
    commit (pre-commit), so a person removes it before it lands."""
    errors = [i for i in run_check(root, changed_only=True).issues if i.severity == "error"]
    rest = [i for i in errors if i.code not in (OWNER_TODO, SECRET)]
    secrets = [i for i in errors if i.code == SECRET] if kind == "pre_commit" else []
    if (rest or secrets) and (base := resolve_base(root)) is not None:
        at_base = errors_at(root, base)
        rest, secrets = new_since(rest, at_base), new_since(secrets, at_base)
    for i in errors:
        if i.code == OWNER_TODO or (i.code == SECRET and i not in secrets):
            _note(i)
    return rest + secrets


def run_hook(root, event_name: str, agent: str, stdin_text: str) -> tuple[str, int]:
    """(stdout, exit code). session-start and stop never fail the agent: on an internal error they print
    nothing blocking, exit 0 and leave one line on stderr. stop and pre-commit block only on errors that are
    new since the merge-base with the default branch; pre-commit exits 1 on those, 2 on a tool error, and 0
    without a kb.config.yaml.
    Raises KeyError for an unknown event or agent."""
    root = Path(root)
    if event_name not in EVENTS:
        raise KeyError(f"unknown hook event {event_name!r}: use {', '.join(EVENTS)}")
    kind = EVENTS[event_name]
    integration = get_agent("generic" if kind == "pre_commit" else agent)
    event = integration.parse(kind, _payload(stdin_text))
    if kind == "pre_commit" and not (root / "kb.config.yaml").is_file():
        return "", 0  # not a kb repository (the hook may be shared): nothing to check
    result = HookResult()
    try:
        if kind == "session_start":
            result.brief = _brief(root)
        elif kind == "stop":
            if not event.stop_active:
                result.errors = _blocking(root, kind)
        else:
            result.errors = _blocking(root, kind)
    except Exception as e:  # a broken tool must not trap the agent; pre-commit reports it as a tool error
        sys.stderr.write(f"kb hook {event_name}: {' '.join(str(e).split())}\n")
        if kind == "pre_commit":
            return "", 2
        return integration.respond(event, HookResult())
    return integration.respond(event, result)
