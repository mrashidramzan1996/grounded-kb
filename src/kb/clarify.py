"""`kb clarify`: record a person's answer as a section of docs/clarifications.md, stored as a quotable source."""

import re
import unicodedata
from pathlib import Path

from pathspec import GitIgnoreSpec

from kb.config.load import load_config
from kb.records.repo import KbRepo
from kb.safewrite import check_target, read_text, write_atomic
from kb.sources.store import add_source

CLARIFICATIONS = "docs/clarifications.md"
_HEADER = (
    "# Clarifications\n\nAnswers to open questions, recorded by the coding agent from the decision-maker's reply. "
    "Each section can be quoted as evidence.\n"
)
_HANDLE = re.compile(r"@[\w.-]+(/[\w.-]+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ClarifyError(ValueError):
    """The answer cannot be recorded; nothing was written."""


def _plain(name: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise ClarifyError(f"{name} is empty")
    if any(unicodedata.category(c) == "Cc" or c in "  " for c in value):
        raise ClarifyError(f"{name} must be one line without control characters")
    if "```" in value:
        raise ClarifyError(f"{name} must not contain a run of three or more backticks")
    return value


def clarify(root: Path, topic: str, text: str, by: str, date: str) -> tuple[str, str]:
    """Append a section to docs/clarifications.md and (re)store it as a source. Returns (source id, ref of the new
    section, SRC-x#seg-NN)."""
    root = Path(root)
    topic, text = _plain("topic", topic), _plain("text", text)
    if topic.startswith("#") or text.startswith("#"):
        raise ClarifyError("topic and text must be plain sentences, not headings")
    if not _HANDLE.fullmatch(by):
        raise ClarifyError(f"--by must look like @handle or @org/team, not {by!r}")
    if not _DATE.fullmatch(date):
        raise ClarifyError(f"--date must be YYYY-MM-DD, not {date!r}")
    config = load_config(root / "kb.config.yaml")
    if not GitIgnoreSpec.from_lines([p for s in config.sources for p in s.paths]).match_file(CLARIFICATIONS):
        raise ClarifyError("add docs/** (or docs/clarifications.md) to config.sources")

    exists = check_target(root, CLARIFICATIONS, "file", ClarifyError)
    old = read_text(root, CLARIFICATIONS, ClarifyError) if exists else None
    base = _HEADER if old is None else old.rstrip("\n") + "\n"
    write_atomic(root, CLARIFICATIONS, f"{base}\n## {topic}\n\nDecided by {by} on {date}.\n\n{text}\n")
    try:
        source = add_source(root, CLARIFICATIONS, config)
    except Exception:
        if old is None:
            (root / CLARIFICATIONS).unlink(missing_ok=True)
        else:
            write_atomic(root, CLARIFICATIONS, old)
        raise
    matching = [
        s.id for s in KbRepo.load(root).segments(source.id)
        if s.heading == topic or s.heading.endswith(f" > {topic}")
    ]
    if not matching:
        raise ClarifyError(f"the new section {topic!r} was not stored as a segment")
    return source.id, f"{source.id}#{matching[-1]}"
