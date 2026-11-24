from dataclasses import dataclass
from typing import Literal, Protocol

Kind = Literal["function", "class", "method", "route", "table", "config_key", "flag", "test"]


@dataclass(frozen=True)
class Symbol:
    anchor: str
    kind: Kind
    path: str
    name: str
    start_byte: int
    end_byte: int


class CodeAdapter(Protocol):
    name: str

    def handles(self, path: str) -> bool: ...

    def symbols(self, path: str, source: bytes) -> list[Symbol]: ...

    def has_syntax_errors(self, source: bytes) -> bool: ...

    def fingerprint(self, source: bytes, symbol: Symbol) -> str: ...

    def imports(self, path: str, source: bytes) -> list[str]: ...
