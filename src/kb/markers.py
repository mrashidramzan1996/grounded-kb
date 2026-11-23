import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pathspec import GitIgnoreSpec

from kb.config.schema import KbConfig
from kb.safepath import UnsafePath, read_regular

_MARKER = re.compile(r"r\[(impl|verify)\s+([A-Z][A-Z0-9.\-]*[A-Z0-9])\]")


@dataclass(frozen=True)
class Marker:
    kind: Literal["impl", "verify"]
    id: str
    path: str
    line: int


def scan_markers(root: Path, files: list[str]) -> list[Marker]:
    """Markers in the given files. Only regular non-symlink files inside root, up to 2 MB, valid UTF-8."""
    out: list[Marker] = []
    for rel in files:
        try:
            data = read_regular(root, rel)
        except UnsafePath:
            continue
        if data is None:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            out.extend(Marker(m.group(1), m.group(2), rel, n) for m in _MARKER.finditer(line))
    return out


def marker_eligible(path: str) -> bool:
    """Whether markers in this repo file count: not under kb/ and no dot-directory or dot-file segment."""
    parts = path.split("/")
    return parts[0] != "kb" and not any(p.startswith(".") for p in parts)


def evidence_files(files: list[str], config: KbConfig) -> list[str]:
    """The files whose markers count as code evidence: marker_eligible ones outside config.sources (a marker
    quoted in a product document is document text)."""
    docs = GitIgnoreSpec.from_lines([p for s in config.sources for p in s.paths])
    return [f for f in files if marker_eligible(f) and not docs.match_file(f)]
