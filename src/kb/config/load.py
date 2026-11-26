from pathlib import Path

from pydantic import ValidationError

from kb.config.schema import KbConfig
from kb.gitfiles import policy_file_at
from kb.yamlio import YamlError, load_yaml_strict

CONFIG = "kb.config.yaml"


class ConfigError(Exception):
    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__("\n".join(messages))


def load_config(path: Path) -> KbConfig:
    path = Path(path)
    try:
        data = load_yaml_strict(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YamlError) as e:
        raise ConfigError([f"{path}: {e}"]) from e
    if data is None:
        raise ConfigError([f"{path}: config is empty"])
    try:
        return KbConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(
            [f"{path}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']} (got {err['input']!r})"
             for err in e.errors()]
        ) from e


def config_at(root: Path, ref: str) -> KbConfig | None:
    """kb.config.yaml as committed at `ref`; None when it is not in that tree. A file that does not parse or validate
    is a ConfigError naming the ref (never a fallback to the working tree); a git failure is a GitError."""
    data = policy_file_at(root, ref, CONFIG)
    if data is None:
        return None
    try:
        return KbConfig.model_validate(load_yaml_strict(data.decode("utf-8")))
    except (UnicodeDecodeError, YamlError, ValidationError) as e:
        raise ConfigError([f"{CONFIG} at {ref}: {e}"]) from e
