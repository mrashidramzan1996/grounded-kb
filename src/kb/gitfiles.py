import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class GitError(Exception):
    pass


# Set by git for hooks (pre-commit exports GIT_INDEX_FILE); every call here names its repository with -C.
SCRUBBED_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in SCRUBBED_GIT_ENV}


NO_COMMITS = "this repository has no commits yet; commit at least once first"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False, env=_env())


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    r = _run(root, *args)
    if check and r.returncode != 0:
        if _run(root, "rev-parse", "--git-dir").returncode == 0 and _run(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode:
            raise GitError(NO_COMMITS)  # M3: whatever failed needed a commit
        raise GitError(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r


def _split(out: bytes) -> list[str]:
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def repo_files(root: Path) -> list[str]:
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    return sorted(set(_split(out)))


def _ref_exists(root: Path, ref: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def find_default_branch(root: Path) -> str | None:
    """The default branch as a ref name that resolves (the local branch, else origin/<name>), or None when
    neither origin/HEAD's target, main nor master exists."""
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
    return None


def default_branch(root: Path) -> str:
    """find_default_branch, falling back to the current branch."""
    return find_default_branch(root) or _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()


def has_commits(root: Path) -> bool:
    return _ref_exists(root, "HEAD")


def rev_parse(root: Path, ref: str) -> str | None:
    """The commit `ref` names, or None."""
    r = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return r.stdout.decode().strip() if r.returncode == 0 and r.stdout.strip() else None


def is_ancestor(root: Path, ancestor: str, ref: str = "HEAD") -> bool:
    return _git(root, "merge-base", "--is-ancestor", ancestor, ref, check=False).returncode == 0


def resolve_base(root: Path) -> str | None:
    """The merge-base of HEAD with the default branch, or None when no default branch (or no commit) exists."""
    branch = find_default_branch(root)
    if branch is None or not has_commits(root):
        return None
    r = _git(root, "merge-base", branch, "HEAD", check=False)
    return r.stdout.decode().strip() if r.returncode == 0 and r.stdout.strip() else None


def require_commit(root: Path) -> None:
    """Raise GitError(NO_COMMITS) in a repository without commits."""
    if not has_commits(root) and _run(root, "rev-parse", "--git-dir").returncode == 0:
        raise GitError(NO_COMMITS)


def merge_base(root: Path, base: str) -> str:
    require_commit(root)
    for ref in (base, f"origin/{base}"):
        r = _git(root, "merge-base", ref, "HEAD", check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.decode().strip()
    raise GitError(
        f"cannot compute merge-base of '{base}' and HEAD; check the base name, "
        "fetch the base branch, or use fetch-depth: 0 in CI"
    )


def changed_files(root: Path, base: str | None = None) -> list[str]:
    return changed_since(root, merge_base(root, base or default_branch(root)))


def changed_since(root: Path, commit: str) -> list[str]:
    """Files changed in the working tree against `commit`, plus untracked files."""
    changed = _split(_git(root, "diff", "--name-only", "-z", commit).stdout)
    untracked = _split(_git(root, "ls-files", "-z", "--others", "--exclude-standard").stdout)
    return sorted(set(changed) | set(untracked))


def read_tree_files(root: Path, ref: str, prefix: str) -> dict[str, bytes]:
    out = _git(root, "ls-tree", "-r", "-z", "--name-only", ref, "--", prefix).stdout
    return {p: _git(root, "show", f"{ref}:{p}").stdout for p in _split(out)}


def file_at(root: Path, ref: str, path: str) -> bytes | None:
    """The bytes of `path` at `ref`, or None when it is not in that tree."""
    return read_tree_files(root, ref, path).get(path)


def tree_symlinks(root: Path, ref: str, prefix: str) -> set[str]:
    """Paths under prefix that are git symlink entries (mode 120000) at ref."""
    out = _git(root, "ls-tree", "-r", "-z", ref, "--", prefix).stdout
    links = set()
    for entry in _split(out):
        meta, _, path = entry.partition("\t")
        if meta.split(" ", 1)[0] == "120000":
            links.add(path)
    return links


def head_commit(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.decode().strip()


def commit_time(root: Path, ref: str) -> int:
    """Committer time of `ref` as Unix seconds."""
    return int(_git(root, "show", "-s", "--format=%ct", ref).stdout.decode().strip())


def tag_exists(root: Path, name: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{name}", check=False).returncode == 0


def create_tag(root: Path, name: str) -> None:
    """Lightweight tag at HEAD; fails if it exists."""
    _git(root, "tag", name, "HEAD")


def delete_tag(root: Path, name: str) -> None:
    _git(root, "tag", "-d", name)


def is_dirty(root: Path, pathspec: str) -> bool:
    """Whether `git status --porcelain` reports anything under pathspec."""
    return bool(_git(root, "status", "--porcelain", "--", pathspec).stdout.strip())


def toplevel(cwd: Path) -> Path | None:
    """The working-tree root containing `cwd`, or None (not a repository, or git unusable)."""
    try:
        r = _git(cwd, "rev-parse", "--show-toplevel", check=False)
    except OSError:
        return None
    out = r.stdout.decode(errors="replace").strip()
    return Path(out) if r.returncode == 0 and out else None


def git_path(root: Path, name: str) -> Path:
    """Where git keeps `name` (e.g. hooks: honours worktrees and core.hooksPath)."""
    return (root / _git(root, "rev-parse", "--git-path", name).stdout.decode().strip()).resolve()


def git_common_dir(root: Path) -> Path:
    """The repository's .git directory shared by all its worktrees."""
    return (root / _git(root, "rev-parse", "--git-common-dir").stdout.decode().strip()).resolve()


@contextmanager
def checkout_at(root: Path, commit: str) -> Iterator[Path]:
    """A temporary detached worktree of `commit`, removed afterwards. No hooks run (core.hooksPath=/dev/null)."""
    tmp = Path(tempfile.mkdtemp(prefix="kb-base-"))
    tree = tmp / "tree"
    try:
        _git(root, "-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach", "--quiet", str(tree), commit)
        yield tree
    finally:
        _git(root, "worktree", "remove", "--force", str(tree), check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        _git(root, "worktree", "prune", check=False)
