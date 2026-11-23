"""Index of everything under kb/: records, sources and the problems found loading them."""

import json
import os
import re
import stat
from collections.abc import Iterable, Iterator
from pathlib import Path

from pydantic import BaseModel, ValidationError

from kb.gitfiles import read_tree_files, tree_symlinks
from kb.issues import Issue
from kb.records.io import RecordError, parse_record
from kb.records.models import Feature, FeatureClaim, Record, Source, Triage
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
        self.raw: dict[str, bytes] = {}
        # every (id, path) loaded, including ids claimed by more than one file
        self.id_paths: list[tuple[str, str]] = []
        self.sources: dict[str, Source] = {}
        self._segments: dict[str, list[Segment]] = {}
        self._text: dict[str, str] = {}
        self._triage: dict[str, Triage] = {}

    @classmethod
    def from_files(cls, files: dict[str, bytes], symlinks: Iterable[str] = ()) -> "KbRepo":
        """`symlinks` are kb/ paths that were symlinks: reported, never read."""
        repo = cls()
        for link in sorted(symlinks):
            repo.errors.append(Issue("schema.symlink", "symlinks are not allowed under kb/", path=link))
        for path in sorted(files):
            if not path.startswith("kb/"):
                continue
            repo.raw[path] = files[path]
            try:
                repo._load_file(path, files[path])
            except (RecordError, UnicodeDecodeError) as e:
                repo.errors.append(Issue("schema", str(e), path=path))
        return repo

    def _load_file(self, path: str, data: bytes) -> None:
        if _RECORD_FILE.fullmatch(path):
            rec = parse_record(path, data, check_name=False)  # id_gate reports mismatches
            self.id_paths.append((rec.id, path))  # type: ignore[attr-defined]
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
        kb = base / "kb"
        if kb.is_symlink() or (kb.exists() and not kb.resolve().is_relative_to(base.resolve())):
            repo = cls()
            repo.errors.append(Issue("schema.symlink", "kb/ must not be a symlink or resolve outside the repo", path="kb"))
            return repo
        files: dict[str, bytes] = {}
        links: list[str] = []
        special: list[str] = []
        for dirpath, dirnames, filenames in os.walk(kb, followlinks=False):
            here = Path(dirpath)
            for d in sorted(dirnames):
                if (here / d).is_symlink():
                    links.append((here / d).relative_to(base).as_posix())
            dirnames[:] = sorted(d for d in dirnames if not (here / d).is_symlink())
            for f in sorted(filenames):
                p = here / f
                rel = p.relative_to(base).as_posix()
                mode = os.lstat(p).st_mode
                if stat.S_ISLNK(mode):
                    links.append(rel)
                elif stat.S_ISREG(mode):
                    files[rel] = p.read_bytes()
                else:
                    special.append(rel)
        repo = cls.from_files(files, links)
        for rel in sorted(special):
            repo.errors.append(Issue("schema.unknown_file", "not a regular file", path=rel))
        return repo

    @classmethod
    def at_ref(cls, root: Path, ref: str) -> "KbRepo":
        links = tree_symlinks(Path(root), ref, "kb")
        files = {p: d for p, d in read_tree_files(Path(root), ref, "kb").items() if p not in links}
        return cls.from_files(files, links)

    def claims(self) -> Iterator[tuple[Feature, FeatureClaim]]:
        """Every (feature, claim) pair, features in sorted id order."""
        for _, rec in sorted(self.records.items()):
            if isinstance(rec, Feature):
                for c in rec.claims:
                    yield rec, c

    def segments(self, src_id: str) -> list[Segment]:
        return self._segments.get(src_id, [])

    def source_text(self, src_id: str) -> str:
        return self._text.get(src_id, "")

    def triage(self, src_id: str) -> Triage | None:
        return self._triage.get(src_id)
