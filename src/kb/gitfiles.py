import subprocess
from pathlib import Path


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=check)


def _split(out: bytes) -> list[str]:
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def repo_files(root: Path) -> list[str]:
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    return sorted(set(_split(out)))


def _ref_exists(root: Path, ref: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def default_branch(root: Path) -> str:
    r = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.decode().strip().removeprefix("origin/")
    for name in ("main", "master"):
        if _ref_exists(root, f"refs/heads/{name}"):
            return name
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()


def changed_files(root: Path, base: str | None = None) -> list[str]:
    base = base or default_branch(root)
    mb = _git(root, "merge-base", base, "HEAD", check=False)
    ref = mb.stdout.decode().strip() if mb.returncode == 0 else "HEAD"
    changed = _split(_git(root, "diff", "--name-only", "-z", ref).stdout)
    untracked = _split(_git(root, "ls-files", "-z", "--others", "--exclude-standard").stdout)
    return sorted(set(changed) | set(untracked))


def read_tree_files(root: Path, ref: str, prefix: str) -> dict[str, bytes]:
    out = _git(root, "ls-tree", "-r", "-z", "--name-only", ref, "--", prefix).stdout
    return {p: _git(root, "show", f"{ref}:{p}").stdout for p in _split(out)}
