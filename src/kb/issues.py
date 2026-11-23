from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    path: str | None = None
    record: str | None = None

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.path or "", self.code, self.record or "")
