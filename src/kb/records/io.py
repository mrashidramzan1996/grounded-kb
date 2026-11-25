"""Record files: parse and canonical render. Records hold only human-owned fields."""

import re

from pydantic import BaseModel, ValidationError

from kb.records.models import (
    Component,
    Decision,
    Exclusion,
    Feature,
    Invariant,
    Record,
    Requirement,
    Term,
)
from kb.yamlio import YamlError, dump_yaml, load_yaml_strict


class RecordError(ValueError):
    pass


# file-name prefix -> (model, extension); .md records carry a free-text body
_BY_PREFIX: dict[str, tuple[type[BaseModel], str]] = {
    "REQ-": (Requirement, ".yaml"),
    "EXCL-": (Exclusion, ".yaml"),
    "TERM-": (Term, ".yaml"),
    "FEAT-": (Feature, ".md"),
    "ADR-": (Decision, ".md"),
    "INV-": (Invariant, ".md"),
    "COMP-": (Component, ".md"),
}
_FENCE = "---\n"


def _model_for(name: str) -> tuple[type[BaseModel], str]:
    for prefix, (model, ext) in _BY_PREFIX.items():
        if name.startswith(prefix):
            if not name.endswith(ext):
                raise RecordError(f"{name}: {model.__name__} files use the {ext} extension")
            return model, ext
    raise RecordError(f"{name}: not a record file name")


def _split_front_matter(name: str, text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0] != _FENCE:
        raise RecordError(f"{name}: missing front matter (file must start with '---')")
    for i in range(1, len(lines)):
        if lines[i] in (_FENCE, "---"):
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    raise RecordError(f"{name}: unterminated front matter")


def parse_record(path: str, data: bytes, *, check_name: bool = True) -> Record:
    name = re.split(r"[\\/]", path)[-1]
    model, ext = _model_for(name)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise RecordError(f"{name}: not valid UTF-8: {e}") from e
    body: str | None = None
    if ext == ".md":
        text, body = _split_front_matter(name, text)
    try:
        fields = load_yaml_strict(text)
    except YamlError as e:
        raise RecordError(f"{name}: {e}") from e
    if not isinstance(fields, dict):
        raise RecordError(f"{name}: expected a mapping of fields")
    if body is not None:
        if "body" in fields:
            raise RecordError(f"{name}: 'body' is the text after the front matter, not a field")
        fields["body"] = body
    try:
        record = model.model_validate(fields)
    except ValidationError as e:
        raise RecordError(
            f"{name}: " + "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        ) from e
    if check_name and name != f"{record.id}{ext}":  # type: ignore[attr-defined]
        raise RecordError(f"{name}: file name does not match id {record.id}")  # type: ignore[attr-defined]
    return record  # type: ignore[return-value]


def render_record(record: Record, keep: tuple[str, ...] = ()) -> bytes:
    """Canonical form: model field order, defaults and None omitted (except the fields named in `keep`), front matter for .md."""
    has_body = "body" in type(record).model_fields
    exclude = {"body"} if has_body else None
    fields = record.model_dump(mode="json", exclude_defaults=True, exclude=exclude)
    if keep:
        full = record.model_dump(mode="json", exclude=exclude)
        fields = {k: v for k, v in full.items() if k in fields or k in keep}
    out = dump_yaml(fields)
    if has_body:
        out = f"{_FENCE}{out}{_FENCE}{record.body}"  # type: ignore[attr-defined]
    return out.encode("utf-8")
