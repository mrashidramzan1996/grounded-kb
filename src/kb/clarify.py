"""`kb clarify`: record a person's answer as a section of docs/clarifications.md, stored as a quotable source."""

import datetime
import re
import shutil
import unicodedata
from pathlib import Path

from kb.config.load import load_config
from kb.records.repo import KbRepo
from kb.safewrite import check_target, write_atomic
from kb.sources.guard import guard
from kb.sources.readers import SourceError
from kb.sources.store import _config_for, add_source

CLARIFICATIONS = "docs/clarifications.md"
_HEADER = (
    "# Clarifications\n\nAnswers to open questions, recorded by the coding agent from the decision-maker's reply. "
    "Each section can be quoted as evidence.\n"
)
_HANDLE = re.compile(r"@[\w.-]+(/[\w.-]+)?", re.ASCII)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_BLOCK = re.compile(r"#|~~~|<!--|>|[-*+] |\||\d+[.)] ")  # what would make a paragraph into other markup
MAX_TOPIC, MAX_TEXT = 200, 1500  # with the header lines, the section stays under one segment (2000 characters)


class ClarifyError(ValueError):
    """The answer cannot be recorded; nothing was written."""


def _plain(name: str, value: str, limit: int) -> str:
    value = value.strip()
    if not value:
        raise ClarifyError(f"{name} is empty")
    if any(unicodedata.category(c) in ("Cc", "Cf") or c in "\u2028\u2029" for c in value):
        raise ClarifyError(f"{name} must be one line without control or invisible formatting characters")
    if "```" in value:
        raise ClarifyError(f"{name} must not contain a run of three or more backticks")
    if _BLOCK.match(value):
        raise ClarifyError(f"{name} must be a plain sentence, not headings, lists, quotes, tables or other markup")
    if len(value) > limit:
        raise ClarifyError(f"{name} is longer than {limit} characters; keep the answer to one short paragraph")
    if guard(value).text != value:
        raise ClarifyError(f"remove emails or secrets from the {name}; they would be masked")
    return value


def _roll_back(root: Path, config, old: bytes | None, stored_id: str | None, was_stored: bool) -> None:
    """Put the file, and the store under kb/sources, back as they were before the failed clarify."""
    if old is None:
        (root / CLARIFICATIONS).unlink(missing_ok=True)
    else:
        write_atomic(root, CLARIFICATIONS, old.decode("utf-8"))
    if stored_id is None:
        return  # add_source replaces the store atomically: it failed before changing anything
    target = root / "kb/sources" / stored_id
    if not was_stored:
        if not target.is_symlink() and target.is_dir():
            shutil.rmtree(target)
    elif old is not None:
        try:
            add_source(root, CLARIFICATIONS, config)  # re-store from the restored bytes
        except Exception:  # keep the original error; kb check will report the store
            pass


def clarify(root: Path, topic: str, text: str, by: str, date: str) -> tuple[str, str]:
    """Append a section to docs/clarifications.md and (re)store it as a source. Returns (source id, ref of the new
    section, SRC-x#seg-NN)."""
    root = Path(root)
    topic, text = _plain("topic", topic, MAX_TOPIC), _plain("text", text, MAX_TEXT)
    if not _HANDLE.fullmatch(by):
        raise ClarifyError(f"--by must look like @handle or @org/team, not {by!r}")
    try:
        if not _DATE.fullmatch(date):
            raise ValueError
        datetime.date.fromisoformat(date)
    except ValueError:
        raise ClarifyError(f"--date must be a valid YYYY-MM-DD date, not {date!r}") from None
    config = load_config(root / "kb.config.yaml")
    try:
        _config_for(CLARIFICATIONS, config)
    except SourceError as e:
        if "not in config.sources" in str(e):
            raise ClarifyError("add docs/** (or docs/clarifications.md) to config.sources") from e
        raise ClarifyError(str(e)) from e

    old: bytes | None = None
    if check_target(root, CLARIFICATIONS, "file", ClarifyError):
        old = (root / CLARIFICATIONS).read_bytes()
        try:
            old.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ClarifyError(f"cannot read {CLARIFICATIONS}: {e}") from e
    nl = "\r\n" if old is not None and b"\r\n" in old else "\n"
    section = f"## {topic}\n\nDecided by {by} on {date}.\n\n{text}\n".replace("\n", nl)
    if old is None:
        new = _HEADER.replace("\n", nl) + nl + section
    else:
        new = old.decode("utf-8").rstrip("\r\n") + nl + nl + section
    kb = KbRepo.load(root)
    stored = [sid for sid, src in kb.sources.items() if src.original == CLARIFICATIONS]
    previous = {s.id for sid in stored for s in kb.segments(sid)}
    write_atomic(root, CLARIFICATIONS, new)
    source = None
    try:
        source = add_source(root, CLARIFICATIONS, config)
        segments = KbRepo.load(root).segments(source.id)
        if not segments or segments[-1].id in previous:  # the appended section is the last one, and a new one
            raise ClarifyError(f"the new section {topic!r} was not stored as a segment")
    except Exception:
        _roll_back(root, config, old, source.id if source else None, bool(stored))
        raise
    return source.id, f"{source.id}#{segments[-1].id}"
