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


def issue_key(i: Issue) -> tuple:
    """What makes two issues the same problem, at two points in history."""
    return (i.code, i.record, i.path, i.message)


def new_since(issues: list[Issue], before: list[Issue]) -> list[Issue]:
    """The issues that were not already there `before`."""
    known = {issue_key(i) for i in before}
    return [i for i in issues if issue_key(i) not in known]
