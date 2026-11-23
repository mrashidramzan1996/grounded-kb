import shutil
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from kb.cli import app
from kb.config.schema import ModuleCfg


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def make_git_repo(
    src: Path | None, tmp: Path, extra: dict[str, str] | None = None
) -> Path:
    repo = tmp / "repo"
    if src is not None:
        shutil.copytree(src, repo)
    else:
        repo.mkdir()
    for rel, content in (extra or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "add", "-A")
    git(repo, "commit", "--allow-empty", "-m", "init")
    return repo


def invoke(root: Path, *args: str):
    return CliRunner().invoke(app, ["-C", str(root), *args])


def codes(issues) -> list[str]:
    return [i.code for i in issues]


def mod(id: str, paths: list[str] | None = None, owner: str = "team-a") -> ModuleCfg:
    return ModuleCfg(id=id, paths=paths or [], owner=owner)


def load_dir(root: Path) -> dict[str, bytes]:
    """Every file under root as {posix relative path: bytes}."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
