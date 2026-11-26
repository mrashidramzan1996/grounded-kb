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


def exact_ref(root: Path, full: str) -> str | None:
    """The object id ref `full` (a full name: refs/...) points to, looked up by that exact name. Never git's name
    lookup, which would fall through from a missing refs/heads/main to a tag named refs/heads/main."""
    if not full.startswith("refs/"):
        return None
    r = _git(root, "show-ref", "--verify", "--hash", full, check=False)
    out = r.stdout.decode().strip()
    return out if r.returncode == 0 and out else None


def _default_branch_ref(root: Path) -> str | None:
    """The default branch's full ref: refs/heads/<name>, else refs/remotes/origin/<name>, for origin/HEAD's target,
    then main, then master; None when none exists."""
    # without --short: git abbreviates the target to "origin/main" or, when a tag refs/tags/origin/main exists,
    # to the ambiguous "heads/origin/main"-style names; the full ref is stripped by hand instead
    r = _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", check=False)
    candidates = []
    if r.returncode == 0 and r.stdout.strip().decode().startswith("refs/remotes/origin/"):
        candidates.append(r.stdout.decode().strip().removeprefix("refs/remotes/origin/"))
    candidates += ["main", "master"]
    for name in candidates:
        for full in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
            if exact_ref(root, full) is not None:
                return full
    return None


def _shown(full: str) -> str:
    """A branch's full ref as people name it: "main" for refs/heads/main, "origin/main" for refs/remotes/origin/main."""
    return full.removeprefix("refs/heads/").removeprefix("refs/remotes/")


def find_default_branch(root: Path) -> str | None:
    """The default branch as a ref name that resolves (the local branch, else origin/<name>), or None when
    neither origin/HEAD's target, main nor master exists."""
    full = _default_branch_ref(root)
    return _shown(full) if full is not None else None


def default_branch_tip(root: Path) -> tuple[str, str] | None:
    """branch_tip for the default branch, from its full ref (never looked up again by its short name)."""
    full = _default_branch_ref(root)
    return branch_tip(root, full, shown=_shown(full)) if full is not None else None


def default_branch(root: Path) -> str:
    """find_default_branch, falling back to the current branch."""
    return find_default_branch(root) or _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()


def current_branch(root: Path) -> str | None:
    """The checked-out local branch's short name, or None when HEAD is detached."""
    r = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return r.stdout.decode().strip() or None if r.returncode == 0 else None


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


# Where a branch named by its short name is looked for, in this order: a local branch, a remote-tracking branch named
# with its remote ("origin/main"), then origin's (a CI checkout has no local base branch).
_BRANCH_PREFIXES = ("refs/heads/", "refs/remotes/", "refs/remotes/origin/")


def branch_tip(root: Path, name: str, *, shown: str | None = None) -> tuple[str, str] | None:
    """(the branch as shown, its tip commit) for branch `name`, looked up only among branches and by exact full ref:
    refs/heads/<name>, refs/remotes/<name>, refs/remotes/origin/<name>; a full ref under refs/heads/ or refs/remotes/
    is the only candidate (shown as given, or as `shown`). None when no candidate exists. GitError (never a guess):
    - a tag named <name>, like a candidate ref or like the shown name: git resolves refs/tags/<name> before
      refs/remotes/<name> (and a missing refs/heads/main falls through to the tag refs/tags/refs/heads/main), so
      whoever can push a tag could otherwise choose the commit the name means in git commands and scripts;
    - candidates at different commits (a local main behind or ahead of origin/main, a local branch named
      origin/main): which one the base is would be a guess."""
    if not name or name.startswith("-"):
        return None
    full_given = name.startswith(("refs/heads/", "refs/remotes/"))
    candidates = [name] if full_given else [prefix + name for prefix in _BRANCH_PREFIXES]
    found = [(c, oid) for c in candidates if (oid := exact_ref(root, c)) is not None]
    shown = shown or (name if full_given or not found else _shown(found[0][0]))
    for tag in dict.fromkeys((name, *candidates, shown)):
        if exact_ref(root, f"refs/tags/{tag}") is not None:
            raise GitError(f"a tag named {tag!r} shadows the branch; delete it or pass a full ref "
                           "(refs/heads/<branch> or refs/remotes/<remote>/<branch>)")
    if not found:
        return None
    first, oid = found[0]
    if other := next((c for c, o in found if o != oid), None):
        raise GitError(f"the base branch {name!r} is ambiguous: {first} and {other} point to different commits; "
                       "pass a full ref")
    tip = rev_parse(root, oid)  # a full object id: peeled to its commit, no name lookup
    return (shown, tip) if tip is not None else None


def merge_base_of(root: Path, commit: str, shown: str) -> str:
    """The merge-base of `commit` (a SHA) and HEAD, with no fallback to another name; `shown` names it in the error."""
    require_commit(root)
    r = _git(root, "merge-base", commit, "HEAD", check=False)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.decode().strip()
    raise GitError(
        f"cannot compute merge-base of '{shown}' and HEAD; check the base name, "
        "fetch the base branch, or use fetch-depth: 0 in CI"
    )


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
    """Files changed in the working tree against `commit`, plus untracked files. A rename lists both paths (no rename
    detection, whatever diff.renames says): the old path's owners must approve a file moved away, and a CODEOWNERS
    file moved elsewhere is a CODEOWNERS change."""
    changed = _split(_git(root, "diff", "--no-renames", "--name-only", "-z", commit).stdout)
    untracked = _split(_git(root, "ls-files", "-z", "--others", "--exclude-standard").stdout)
    return sorted(set(changed) | set(untracked))


def diff_names(root: Path, a: str, b: str, prefix: str) -> list[str]:
    """Paths under `prefix` that differ between commits `a` and `b` (added, edited or deleted; a rename is both)."""
    return _split(_git(root, "diff", "--no-renames", "--name-only", "-z", a, b, "--", prefix).stdout)


def read_tree_files_names(root: Path, ref: str, prefix: str) -> list[str]:
    """The paths under `prefix` at `ref` (blobs and symlink entries), without reading them."""
    return _split(_git(root, "ls-tree", "-r", "-z", "--name-only", ref, "--", prefix).stdout)


def _cat_file_batch(root: Path, oids: list[str]) -> list[bytes]:
    """The content of each object id, in order, from one `git cat-file --batch` process."""
    r = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"], input="".join(f"{o}\n" for o in oids).encode(),
                       capture_output=True, check=False, env=_env())
    if r.returncode != 0:
        raise GitError(f"git cat-file --batch failed: {r.stderr.decode(errors='replace').strip()}")
    out, pos, contents = r.stdout, 0, []
    for oid in oids:
        end = out.index(b"\n", pos)
        header = out[pos:end].split(b" ")  # `<oid> <type> <size>`, or `<oid> missing`
        if len(header) != 3:
            raise GitError(f"git cat-file --batch could not read object {oid}")
        size = int(header[2])
        contents.append(out[end + 1:end + 1 + size])
        pos = end + 1 + size + 1  # the content is followed by a newline
    return contents


def read_tree_files(root: Path, ref: str, prefix: str) -> dict[str, bytes]:
    """path -> bytes for every blob and symlink entry (its target) under `prefix` at `ref`; submodules are skipped.
    Two processes: one `ls-tree`, one `cat-file --batch` fed object ids (paths may hold newlines)."""
    out = _git(root, "ls-tree", "-r", "-z", ref, "--", prefix).stdout
    entries: list[tuple[str, str]] = []  # (path, oid)
    for entry in _split(out):
        meta, _, path = entry.partition("\t")
        _mode, kind, oid = meta.split(" ")
        if kind == "blob":  # 100644, 100755 and 120000 (symlink); a gitlink (type commit) has no content here
            entries.append((path, oid))
    if not entries:
        return {}
    return dict(zip((p for p, _ in entries), _cat_file_batch(root, [o for _, o in entries]), strict=True))


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


def has_tag_matching(root: Path, pattern: str) -> bool:
    """Whether any tag matches the glob `pattern` (git tag -l)."""
    return bool(_git(root, "tag", "-l", pattern, check=False).stdout.strip())


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
