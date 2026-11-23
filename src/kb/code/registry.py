from pathlib import Path

from kb.code.python_pack import PythonPack
from kb.ports.code import CodeAdapter, Symbol
from kb.safepath import UnsafePath, check_relpath, read_regular

__all__ = ["NoAdapter", "UnsafePath", "adapter_for", "resolve_anchor", "resolve_test"]

_ADAPTERS: list[CodeAdapter] = [PythonPack()]


class NoAdapter(Exception):
    pass


def adapter_for(path: str) -> CodeAdapter | None:
    return next((a for a in _ADAPTERS if a.handles(path)), None)


def _lookup(root: Path, path: str, name: str) -> Symbol | None:
    check_relpath(path)
    adapter = adapter_for(path)
    if adapter is None:
        raise NoAdapter(path)
    source = read_regular(root, path)
    if source is None:
        return None
    return next((s for s in adapter.symbols(path, source) if s.name == name), None)


def resolve_anchor(root: Path, anchor: str) -> Symbol | None:
    """The symbol for `path#name` / `path#Class.method`, or None. Raises UnsafePath, NoAdapter."""
    path, sep, name = anchor.partition("#")
    if not sep or not name:
        return None
    return _lookup(root, path, name)


def resolve_test(root: Path, ref: str) -> Symbol | None:
    """The function for `path::name` / `path::Class::name`, or None. Raises UnsafePath, NoAdapter."""
    path, sep, rest = ref.partition("::")
    if not sep or not rest:
        return None
    sym = _lookup(root, path, rest.replace("::", "."))
    return sym if sym is not None and sym.kind in ("function", "method", "test") else None
