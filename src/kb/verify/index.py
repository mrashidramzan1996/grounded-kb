"""The anchor index: per-file cards of symbols, imports and markers, and the test/impl links derived from them.

These are structure claims, true by construction. They are derived from the code and never committed (A-11).
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kb.code.registry import adapter_for
from kb.config.schema import KbConfig
from kb.config.validate import modules_for
from kb.deps import module_name
from kb.gitfiles import repo_files
from kb.markers import Marker, evidence_files, scan_markers
from kb.safepath import UnsafePath, read_regular

INDEX_PATH = ".kb/cache/index.json"


@dataclass(frozen=True)
class FileCard:
    path: str
    modules: tuple[str, ...]
    symbols: tuple[tuple[str, str, str], ...]  # (anchor, kind, fingerprint), sorted by anchor
    imports: tuple[str, ...]
    markers: tuple[tuple[str, str, int, str | None], ...]  # (kind, id, line, enclosing test ref or None)


@dataclass
class AnchorIndex:
    cards: dict[str, FileCard] = field(default_factory=dict)

    def tests_for(self, record_or_claim_id: str) -> list[str]:
        return sorted({
            ref for c in self.cards.values() for kind, mid, _, ref in c.markers
            if kind == "verify" and mid == record_or_claim_id and ref
        })

    def impl_files(self, record_or_claim_id: str) -> list[str]:
        return sorted(
            p for p, c in self.cards.items()
            if any(kind == "impl" and mid == record_or_claim_id for kind, mid, _, _ in c.markers)
        )

    def importers_of(self, path: str) -> list[str]:
        names = {module_name(path)} - {None}
        if not names:
            return []
        prefixes = tuple(n + "." for n in names)
        return sorted(
            p for p, c in self.cards.items()
            if p != path and any(i in names or i.startswith(prefixes) for i in c.imports)
        )

    def to_json(self) -> str:
        return json.dumps({"cards": {p: asdict(c) for p, c in self.cards.items()}}, sort_keys=True, indent=2) + "\n"


def _line_starts(source: bytes) -> list[int]:
    starts, pos = [0], 0
    for line in source.splitlines(keepends=True):
        pos += len(line)
        starts.append(pos)
    return starts


def _card(path: str, source: bytes, markers: list[Marker], config: KbConfig) -> FileCard:
    adapter = adapter_for(path)
    symbols = adapter.symbols(path, source) if adapter else []
    tests = [s for s in symbols if s.kind == "test"]
    starts = _line_starts(source)
    entries = []
    for m in markers:
        ref = None
        offset = starts[min(m.line - 1, len(starts) - 1)]
        inside = [s for s in tests if s.start_byte <= offset < s.end_byte]
        if inside:
            inner = min(inside, key=lambda s: s.end_byte - s.start_byte)
            ref = f"{path}::{inner.name.replace('.', '::')}"
        entries.append((m.kind, m.id, m.line, ref))
    return FileCard(
        path=path,
        modules=tuple(modules_for(path, config)),
        symbols=tuple(sorted((s.anchor, s.kind, adapter.fingerprint(source, s)) for s in symbols)) if adapter else (),
        imports=tuple(sorted(set(adapter.imports(path, source)))) if adapter else (),
        markers=tuple(sorted(entries, key=lambda e: (e[2], e[0], e[1]))),
    )


def build_index(root: Path, config: KbConfig) -> AnchorIndex:
    root = Path(root)
    files = evidence_files(repo_files(root), config)
    by_file: dict[str, list[Marker]] = {}
    for m in scan_markers(root, files):
        by_file.setdefault(m.path, []).append(m)
    cards: dict[str, FileCard] = {}
    for path in files:
        if adapter_for(path) is None and path not in by_file:
            continue
        try:
            source = read_regular(root, path)
        except UnsafePath:
            continue
        if source is None:
            continue
        cards[path] = _card(path, source, by_file.get(path, []), config)
    return AnchorIndex(dict(sorted(cards.items())))
