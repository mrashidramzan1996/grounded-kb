from pathlib import Path

from pydantic import ValidationError

from kb.config.schema import KbConfig
from kb.yamlio import YamlError, load_yaml_strict


class ConfigError(Exception):
    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__("\n".join(messages))


def load_config(path: Path) -> KbConfig:
    path = Path(path)
    try:
        data = load_yaml_strict(path.read_text())
    except (OSError, YamlError) as e:
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
