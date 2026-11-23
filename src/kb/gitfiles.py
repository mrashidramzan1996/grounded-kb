import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r


def _split(out: bytes) -> list[str]:
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def repo_files(root: Path) -> list[str]:
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    return sorted(set(_split(out)))


def _ref_exists(root: Path, ref: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def default_branch(root: Path) -> str:
    """A ref name that resolves: the local branch if it exists, else origin/<name>."""
    r = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    candidates = []
    if r.returncode == 0 and r.stdout.strip():
        candidates.append(r.stdout.decode().strip().removeprefix("origin/"))
    candidates += ["main", "master"]
    for name in candidates:
        if _ref_exists(root, f"refs/heads/{name}"):
            return name
        if _ref_exists(root, f"refs/remotes/origin/{name}"):
            return f"origin/{name}"
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()


def _merge_base(root: Path, base: str) -> str:
    for ref in (base, f"origin/{base}"):
        r = _git(root, "merge-base", ref, "HEAD", check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.decode().strip()
    raise GitError(
        f"cannot compute merge-base of '{base}' and HEAD; check the base name, "
        "fetch the base branch, or use fetch-depth: 0 in CI"
    )


def changed_files(root: Path, base: str | None = None) -> list[str]:
    ref = _merge_base(root, base or default_branch(root))
    changed = _split(_git(root, "diff", "--name-only", "-z", ref).stdout)
    untracked = _split(_git(root, "ls-files", "-z", "--others", "--exclude-standard").stdout)
    return sorted(set(changed) | set(untracked))


def read_tree_files(root: Path, ref: str, prefix: str) -> dict[str, bytes]:
    out = _git(root, "ls-tree", "-r", "-z", "--name-only", ref, "--", prefix).stdout
    return {p: _git(root, "show", f"{ref}:{p}").stdout for p in _split(out)}
