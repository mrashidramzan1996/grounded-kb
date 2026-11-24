import json
import os
from pathlib import Path

from kb.ports.forge import Forge


class ForgeError(Exception):
    """The forge could not be reached or is misconfigured (exit 2)."""


def forge_from_option(value: str, root: Path) -> Forge | None:
    if value == "none":
        return None
    if value.startswith("local:") and len(value) > len("local:"):
        from kb.forge.local import LocalForge

        return LocalForge(root / value[len("local:") :])
    if value == "github":
        from kb.forge.github import GithubForge

        return GithubForge()
    if value == "auto":
        return _auto()
    raise ValueError(f"unknown forge {value!r}: use none, local:<path>, github or auto")


def _auto() -> Forge | None:
    """GitHub on a pull_request event; None outside Actions or on another event (a push).

    Inside Actions an event that cannot be read fails closed: skipping the forge would skip owner approval."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise ForgeError("GITHUB_ACTIONS is set but GITHUB_EVENT_PATH is not; cannot tell whether this is a pull request")
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ForgeError(f"cannot read the event file: {type(e).__name__}") from None
    if not isinstance(event, dict):
        raise ForgeError("the event file must hold a JSON object")
    if "pull_request" not in event:
        return None
    from kb.forge.github import GithubForge

    return GithubForge()
