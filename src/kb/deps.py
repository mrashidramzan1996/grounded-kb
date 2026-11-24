"""Lockfile package diffing and import-to-package matching for the dependency hop."""

import re
import tomllib
from pathlib import PurePosixPath

LOCKFILES = ("uv.lock", "poetry.lock", "requirements.txt")
_TOML_LOCKFILES = ("uv.lock", "poetry.lock")
_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def module_name(path: str) -> str | None:
    p = PurePosixPath(path)
    if p.suffix != ".py":
        return None
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or None


def is_lockfile(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in LOCKFILES or (name.startswith("requirements") and name.endswith(".txt"))


def normalise_package(name: str) -> str:
    return re.sub(r"[-.]", "_", name.lower())


def lockfile_packages(path: str, data: bytes) -> dict[str, str]:
    """Normalised package name -> version. Unparseable TOML raises tomllib.TOMLDecodeError."""
    text = data.decode("utf-8", errors="replace")
    out: dict[str, str] = {}
    if PurePosixPath(path).name in _TOML_LOCKFILES:
        for pkg in tomllib.loads(text).get("package", []):
            if "name" in pkg and "version" in pkg:
                out[normalise_package(str(pkg["name"]))] = str(pkg["version"])
        return out
    for line in text.splitlines():
        line = line.split(";", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        m = _REQ.match(line)
        if m:
            out[normalise_package(m.group(1))] = m.group(2)
    return out


def changed_packages(base: dict[str, str], head: dict[str, str]) -> set[str]:
    return {n for n in base.keys() | head.keys() if base.get(n) != head.get(n)}


def imported_top_levels(imports: list[str]) -> set[str]:
    return {normalise_package(i.split(".", 1)[0]) for i in imports if i}
