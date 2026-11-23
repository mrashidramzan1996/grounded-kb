from pathlib import Path

from kb.code.python_pack import PythonPack
from kb.ports.code import CodeAdapter, Symbol
from kb.safepath import UnsafePath, check_relpath, read_regular

__all__ = ["NoAdapter", "SymbolIndex", "UnsafePath", "adapter_for", "resolve_anchor", "resolve_test"]

_ADAPTERS: list[CodeAdapter] = [PythonPack()]


class NoAdapter(Exception):
    pass


def adapter_for(path: str) -> CodeAdapter | None:
    return next((a for a in _ADAPTERS if a.handles(path)), None)


class SymbolIndex:
    """Per-run cache of each file's symbols (or the NoAdapter/UnsafePath it raised)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._cache: dict[str, list[Symbol] | NoAdapter | UnsafePath] = {}
        self._broken: set[str] = set()

    def symbols(self, path: str) -> list[Symbol]:
        """Symbols of `path` ([] if the file is absent). Raises UnsafePath, NoAdapter."""
        if path not in self._cache:
            self._cache[path] = self._load(path)
        entry = self._cache[path]
        if isinstance(entry, Exception):
            raise entry
        return entry

    def has_syntax_errors(self, path: str) -> bool:
        return path in self._broken

    def _load(self, path: str) -> "list[Symbol] | NoAdapter | UnsafePath":
        try:
            check_relpath(path)
            adapter = adapter_for(path)
            if adapter is None:
                return NoAdapter(path)
            source = read_regular(self.root, path)
        except UnsafePath as e:
            return e
        if source is None:
            return []
        if adapter.has_syntax_errors(source):
            self._broken.add(path)
        return adapter.symbols(path, source)


def _lookup(root: Path, index: SymbolIndex | None, path: str, name: str) -> Symbol | None:
    index = index or SymbolIndex(root)
    return next((s for s in index.symbols(path) if s.name == name), None)


def resolve_anchor(root: Path, anchor: str, index: SymbolIndex | None = None) -> Symbol | None:
    """The symbol for `path#name` / `path#Class.method`, or None. Raises UnsafePath, NoAdapter."""
    path, sep, name = anchor.partition("#")
    if not sep or not name:
        return None
    return _lookup(root, index, path, name)


def resolve_test(root: Path, ref: str, index: SymbolIndex | None = None) -> Symbol | None:
    """The function for `path::name` / `path::Class::name`, or None. Raises UnsafePath, NoAdapter."""
    path, sep, rest = ref.partition("::")
    if not sep or not rest:
        return None
    sym = _lookup(root, index, path, rest.replace("::", "."))
    return sym if sym is not None and sym.kind in ("function", "method", "test") else None
