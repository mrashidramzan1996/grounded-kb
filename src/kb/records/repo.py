"""Index of everything under kb/: records, sources and the problems found loading them."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from kb.gitfiles import read_tree_files
from kb.issues import Issue
from kb.records.io import RecordError, parse_record
from kb.records.models import FeatureClaim, Record, Source, Triage
from kb.sources.segment import Segment
from kb.yamlio import YamlError, load_yaml_strict

_RECORD_FILE = re.compile(
    r"kb/(?:requirements/REQ-[^/]+\.yaml|features/FEAT-[^/]+\.md|decisions/ADR-[^/]+\.md"
    r"|invariants/INV-[^/]+\.md|components/COMP-[^/]+\.md|glossary/TERM-[^/]+\.yaml)"
)
_SOURCE_FILE = re.compile(r"kb/sources/(SRC-[^/]+)/(source\.yaml|text\.md|segments\.jsonl|triage\.yaml)")
_SEGMENT_KEYS = {"id": str, "heading": str, "page": int, "start": int, "end": int, "hash": str}


def _model_from_yaml(model: type[BaseModel], data: bytes):
    try:
        raw = load_yaml_strict(data.decode("utf-8"))
        return model.model_validate(raw)
    except (UnicodeDecodeError, YamlError) as e:
        raise RecordError(str(e)) from e
    except ValidationError as e:
        raise RecordError(
            "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        ) from e


def _parse_segments(data: bytes) -> list[Segment]:
    segs = []
    for n, line in enumerate(data.decode("utf-8").splitlines(), 1):
        try:
            obj = json.loads(line)
            ok = isinstance(obj, dict) and obj.keys() == _SEGMENT_KEYS.keys() and all(
                type(obj[k]) is t for k, t in _SEGMENT_KEYS.items()
            )
        except json.JSONDecodeError as e:
            raise RecordError(f"line {n}: invalid JSON: {e}") from e
        if not ok:
            raise RecordError(f"line {n}: expected exactly the keys {sorted(_SEGMENT_KEYS)} with correct types")
        segs.append(Segment(**obj))
    return segs


class KbRepo:
    def __init__(self) -> None:
        self.records: dict[str, Record] = {}
        self.paths: dict[str, str] = {}
        self.errors: list[Issue] = []
        self.sources: dict[str, Source] = {}
        self._segments: dict[str, list[Segment]] = {}
        self._text: dict[str, str] = {}
        self._triage: dict[str, Triage] = {}

    @classmethod
    def from_files(cls, files: dict[str, bytes]) -> "KbRepo":
        repo = cls()
        for path in sorted(files):
            if not path.startswith("kb/"):
                continue
            try:
                repo._load_file(path, files[path])
            except (RecordError, UnicodeDecodeError) as e:
                repo.errors.append(Issue("schema", str(e), path=path))
        return repo

    def _load_file(self, path: str, data: bytes) -> None:
        if _RECORD_FILE.fullmatch(path):
            rec = parse_record(path, data)
            self.records[rec.id] = rec  # type: ignore[attr-defined]
            self.paths[rec.id] = path  # type: ignore[attr-defined]
        elif m := _SOURCE_FILE.fullmatch(path):
            self._load_source_file(m.group(1), m.group(2), data)
        else:
            self.errors.append(Issue("schema.unknown_file", "not a recognised kb file", path=path))

    def _load_source_file(self, src_id: str, name: str, data: bytes) -> None:
        if name == "source.yaml":
            src = _model_from_yaml(Source, data)
            if src.id != src_id:
                raise RecordError(f"source id {src.id} does not match directory {src_id}")
            self.sources[src_id] = src
        elif name == "text.md":
            self._text[src_id] = data.decode("utf-8")
        elif name == "segments.jsonl":
            self._segments[src_id] = _parse_segments(data)
        else:
            self._triage[src_id] = _model_from_yaml(Triage, data)

    @classmethod
    def load(cls, root: Path) -> "KbRepo":
        base = Path(root)
        kb_dir = base / "kb"
        files = {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(kb_dir.rglob("*")) if p.is_file()}
        return cls.from_files(files)

    @classmethod
    def at_ref(cls, root: Path, ref: str) -> "KbRepo":
        return cls.from_files(read_tree_files(Path(root), ref, "kb"))

    def claims(self) -> dict[str, FeatureClaim]:
        """Every feature claim, keyed by claim id."""
        return {
            c.id: c
            for rec in self.records.values()
            if hasattr(rec, "claims")
            for c in rec.claims  # type: ignore[attr-defined]
        }

    def segments(self, src_id: str) -> list[Segment]:
        return self._segments.get(src_id, [])

    def source_text(self, src_id: str) -> str:
        return self._text.get(src_id, "")

    def triage(self, src_id: str) -> Triage | None:
        return self._triage.get(src_id)
