"""Strict, deterministic YAML in and out."""

from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.constructor import DuplicateKeyError


class YamlError(ValueError):
    pass


def load_yaml_strict(text: str) -> Any:
    """Parse YAML; duplicate keys, unresolved anchors and syntax errors raise YamlError."""
    yaml = YAML(typ="safe", pure=True)
    yaml.allow_duplicate_keys = False
    try:
        return yaml.load(text)
    except DuplicateKeyError as e:
        raise YamlError(f"duplicate key: {e}") from e
    except YAMLError as e:
        raise YamlError(str(e)) from e
    except RecursionError as e:  # nesting deep enough to exhaust the parser's recursion: untrusted input, not a crash
        raise YamlError("nested too deeply") from e


def dump_yaml(data: Any) -> str:
    """Canonical dump: insertion order kept, block style, no wrapping."""
    yaml = YAML(typ="safe", pure=True)
    yaml.default_flow_style = False
    yaml.sort_base_mapping_type_on_output = False
    yaml.width = 4096
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()
