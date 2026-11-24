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
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return None
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(event, dict) or "pull_request" not in event:
        return None
    from kb.forge.github import GithubForge

    return GithubForge()
