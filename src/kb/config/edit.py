"""Edit kb.config.yaml in place with ruamel round-trip mode, so comments and order survive.
Every edit is validated by loading the result; an edit that does not load is never written."""

import os
import tempfile
from collections.abc import Callable, Sequence
from io import StringIO
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from kb.config.load import ConfigError, load_config
from kb.config.schema import ModuleCfg
from kb.config.validate import resolve_owner

CONFIG_FILE = "kb.config.yaml"


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    y.preserve_quotes = True
    return y


def _edit(root: Path, change: Callable[[CommentedMap], None]) -> None:
    path = Path(root) / CONFIG_FILE
    try:
        data = _yaml().load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as e:
        raise ConfigError([f"{path}: {e}"]) from e
    if not isinstance(data, CommentedMap):
        raise ConfigError([f"{path}: config must be a mapping"])
    change(data)
    out = StringIO()
    _yaml().dump(data, out)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".kb.config.", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(out.getvalue())
        try:
            load_config(Path(tmp))
        except ConfigError as e:
            raise ConfigError([m.replace(tmp, str(path)) for m in e.messages]) from e
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _modules(data: CommentedMap) -> CommentedSeq:
    mods = data.get("modules")
    if mods is None:
        mods = CommentedSeq()
        data["modules"] = mods
    if not isinstance(mods, CommentedSeq):
        raise ConfigError(["modules must be a list"])
    return mods


def _block_list(items: Sequence[str]) -> CommentedSeq:
    seq = CommentedSeq(items)
    seq.fa.set_block_style()
    return seq


def add_module(
    root: Path, id: str, *, owner: str, risk: str = "standard", title: str | None = None, paths: Sequence[str] = ()
) -> None:
    config = load_config(Path(root) / CONFIG_FILE)
    try:
        ModuleCfg.model_validate({"id": id, "owner": owner})
    except ValidationError as e:
        raise ConfigError([f"module id {id!r} must look like MOD-billing (lowercase letters, digits, dashes)"]) from e
    if any(m.id == id for m in config.modules):
        raise ConfigError([f"module {id} already exists"])
    if owner != "TODO" and resolve_owner(owner, config) is None:
        raise ConfigError([f"owner '{owner}' is neither a team key nor an @handle (or use TODO)"])

    def change(data: CommentedMap) -> None:
        entry = CommentedMap()
        entry["id"] = id
        if title:
            entry["title"] = title
        if paths:
            entry["paths"] = _block_list(paths)
        entry["owner"] = owner
        if risk != "standard":
            entry["risk"] = risk
        mods = _modules(data)
        if not mods:
            mods.fa.set_block_style()
        mods.append(entry)

    _edit(Path(root), change)


def set_paths(root: Path, id: str, globs: list[str]) -> None:
    config = load_config(Path(root) / CONFIG_FILE)
    if all(m.id != id for m in config.modules):
        raise ConfigError([f"unknown module {id}"])

    def change(data: CommentedMap) -> None:
        for entry in _modules(data):
            if entry.get("id") == id:
                entry["paths"] = _block_list(globs)
                return

    _edit(Path(root), change)
