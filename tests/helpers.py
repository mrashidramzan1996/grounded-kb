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


# --- gate-test helpers: mutations edit a loaded {path: bytes} files dict -------------------------

def _path_of(files: dict[str, bytes], rec_id: str) -> str:
    (path,) = [p for p in files if p.rsplit("/", 1)[-1].rsplit(".", 1)[0] == rec_id]
    return path


def _split(path: str, data: bytes) -> tuple[dict, str]:
    """(fields, body) of a record file; body is '' for .yaml records."""
    from kb.yamlio import load_yaml_strict

    text = data.decode()
    if path.endswith(".md"):
        _, front, body = text.split("---\n", 2)
        return load_yaml_strict(front), body
    return load_yaml_strict(text), ""


def _join(path: str, fields: dict, body: str) -> bytes:
    from kb.yamlio import dump_yaml

    out = dump_yaml(fields)
    return (f"---\n{out}---\n{body}" if path.endswith(".md") else out).encode()


def rename_id(old: str, new: str):
    """Change the id inside the file; the file name stays, so it no longer matches."""

    def mutate(files):
        path = _path_of(files, old)
        fields, body = _split(path, files[path])
        fields["id"] = new
        files[path] = _join(path, fields, body)

    return mutate


def set_field(rec_id: str, **fields):
    def mutate(files):
        path = _path_of(files, rec_id)
        current, body = _split(path, files[path])
        current.update(fields)
        files[path] = _join(path, current, body)

    return mutate


def adr_bytes(id: str, **fields) -> bytes:
    data = {"id": id, "title": f"Decision {id}", "governs": ["MOD-sessions"]} | fields
    return _join("x.md", data, f"Body of {id}.\n")


def add_adr(id: str, **fields):
    def mutate(files):
        files[f"kb/decisions/{id}.md"] = adr_bytes(id, **fields)

    return mutate


class GitCtx:
    """A git working copy of kb_valid; `reload` builds a GateContext from disk."""

    def __init__(self, root: Path, config):
        self.root, self.config = root, config

    def reload(self, base: str | None = None):
        from kb.gates.base import GateContext
        from kb.records.repo import KbRepo

        return GateContext(
            self.root, self.config, KbRepo.load(self.root), KbRepo.at_ref(self.root, base) if base else None
        )


def edit(ctx: GitCtx, path: str, text: str) -> None:
    """Append `text` to the file (so it still parses but its bytes change)."""
    with (ctx.root / path).open("a") as f:
        f.write(text)


def add_adr_file(ctx: GitCtx, id: str, **fields) -> None:
    (ctx.root / f"kb/decisions/{id}.md").write_bytes(adr_bytes(id, **fields))


# --- document builders for reader tests --------------------------------------------------------

def pdf_bytes(pages: list[list[tuple[str, int, str]]], header: str = "", footer: str = "") -> bytes:
    """A PDF whose pages hold (font style, size, text) paragraphs; `{n}` in header/footer is the page number."""
    from datetime import datetime, timezone

    from fpdf import FPDF

    class Doc(FPDF):
        def header(self):
            if header:
                self.set_font("Helvetica", size=8)
                self.cell(0, 6, header.format(n=self.page_no()), align="C")
                self.ln(10)

        def footer(self):
            if footer:
                self.set_y(-15)
                self.set_font("Helvetica", size=8)
                self.cell(0, 6, footer.format(n=self.page_no()), align="C")

    pdf = Doc()
    pdf.set_creation_date(datetime(2026, 11, 2, tzinfo=timezone.utc))
    for paragraphs in pages:
        pdf.add_page()
        for style, size, text in paragraphs:
            pdf.set_font("Helvetica", style=style, size=size)
            pdf.multi_cell(0, 6, text)
            pdf.ln(3)
    return bytes(pdf.output())


def docx_bytes(doc) -> bytes:
    import io

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
