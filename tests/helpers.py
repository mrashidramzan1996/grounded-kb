import json
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


def set_claim(feature_id: str, index: int, **fields):
    def mutate(files):
        path = _path_of(files, feature_id)
        current, body = _split(path, files[path])
        current["claims"][index].update(fields)
        files[path] = _join(path, current, body)

    return mutate


def add_file(path: str, text: str):
    def mutate(files):
        files[path] = text.encode()

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


# --- source helpers ----------------------------------------------------------------------------

def pdf(fixtures):
    """The normalised text of the acme-billing pricing spec."""
    from kb.sources.readers import read_document

    path = fixtures / "repos/acme-billing/docs/pricing-spec-v3.pdf"
    return read_document(path.name, path.read_bytes())[0]


def nt(text: str):
    from kb.sources.readers import NormalisedText

    return NormalisedText(text, [(1, 0)])


def text_of(segment, text: str) -> str:
    return text[segment.start : segment.end]


def add_all(repo: Path):
    """Add every file under docs/ as a source; returns the Sources."""
    from kb.config.load import load_config
    from kb.sources.store import add_source

    config = load_config(repo / "kb.config.yaml")
    return [add_source(repo, p.relative_to(repo).as_posix(), config) for p in sorted((repo / "docs").iterdir())]


def grep(directory: Path, pattern: str) -> list[Path]:
    """Files under `directory` whose bytes match the regular expression."""
    import re

    rx = re.compile(pattern.encode())
    return [p for p in sorted(directory.rglob("*")) if p.is_file() and rx.search(p.read_bytes())]


# --- acme quote-gate helpers -----------------------------------------------------------------

def req(id: str, statement: str, quote: tuple[str, str, str], **fields):
    """A Requirement for acme_kb_with. quote=(source stem, heading suffix, text); the segment is resolved later:
    the first one under that heading containing the text, else the first under it."""

    def build(segment_id):
        from kb.records.models import Requirement

        stem, suffix, text = quote
        data = {
            "id": id,
            "statement": statement,
            "evidence": {"quotes": [{"source": f"SRC-{stem}#{segment_id(stem, suffix, text)}", "text": text}]},
            "modules": ["MOD-billing"],
        } | fields
        return Requirement.model_validate(data)

    return build


# --- ingest helpers ----------------------------------------------------------------------------

BRIEF = ".kb/cache/ingest/brief.md"


def brief_of(repo: Path) -> str:
    return (repo / BRIEF).read_text(encoding="utf-8")


def total_segments(repo: Path) -> int:
    from kb.records.repo import KbRepo

    kb = KbRepo.load(repo)
    return sum(len(kb.segments(s)) for s in kb.sources)


def apply_drafts(repo: Path, drafts: Path) -> None:
    """Simulate an agent following the brief: copy the drafted files over the repo (its kb.config.yaml,
    which carries the proposed modules, replaces the repo's)."""
    shutil.copytree(drafts, repo, dirs_exist_ok=True)


def ctx(repo: Path):
    """(root, config, kb repo, repo files): the arguments of suggest_paths."""
    from kb.config.load import load_config
    from kb.gitfiles import repo_files
    from kb.records.repo import KbRepo

    return repo, load_config(repo / "kb.config.yaml"), KbRepo.load(repo), repo_files(repo)


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- check helpers -----------------------------------------------------------------------------

def break_ref(root: Path) -> None:
    """Make REQ-F-001 name a feature that does not exist (-> ref.missing)."""
    path = root / "kb/requirements/REQ-F-001.yaml"
    path.write_text(path.read_text() + "features:\n- FEAT-999\n")


def set_mode(root: Path, mode: str) -> None:
    path = root / "kb.config.yaml"
    path.write_text(path.read_text() + f"  mode: {mode}\n")


# --- context / trace helpers -------------------------------------------------------------------

def ids(items) -> list[str]:
    return [i.id for i in items]


def replace_doc_text(repo: Path, rel: str, old: str, new: str) -> None:
    """Reword a text document in place (the original changes; its stored source does not until a sync)."""
    path = repo / rel
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


# --- agent hook helpers ------------------------------------------------------------------------

def payload(fixtures: Path, name: str) -> str:
    return (fixtures / "hooks" / f"{name}.json").read_text()


SETTINGS_PATH = {"claude-code": ".claude/settings.json", "codex": ".codex/hooks.json"}


# --- clean-KB helpers --------------------------------------------------------------------------

SECRET_KEY = "AKIAIOSFODNN7EXAMPLE"


def remove_secret_paragraph(path: Path) -> None:
    """Fix the original: drop the paragraph holding the planted example key."""
    from docx import Document

    doc = Document(str(path))
    hits = [p for p in doc.paragraphs if SECRET_KEY in p.text]
    assert len(hits) == 1
    hits[0]._element.getparent().remove(hits[0]._element)
    doc.save(str(path))


def clean_drafted_acme(repo: Path) -> None:
    """Resolve what kb check reports on the drafted acme repo: drop the invented REQ-F-005 and the secret."""
    (repo / "kb/requirements/REQ-F-005.yaml").unlink()
    triage = repo / "kb/sources/SRC-pricing-spec-v3/triage.yaml"
    triage.write_text(triage.read_text().replace("[REQ-F-005]", '"none: annual pricing is drafted elsewhere"'))
    remove_secret_paragraph(repo / "docs/architecture-notes.docx")
    assert invoke(repo, "source", "sync").exit_code == 0


# --- verify helpers (a git copy of kb_valid: the verify_repo fixture) ----------------------------

CLAIM_TEST = "tests/test_seats.py"


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def commit(repo: Path, message: str = "change") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def fail_claim_test(repo: Path) -> None:
    """Make the test linked to FEAT-001.C1 fail."""
    path = repo / CLAIM_TEST
    path.write_text(path.read_text().replace("    ...\n", "    assert False\n", 1))


def forge_file(tmp_path: Path, repo: Path, **extra) -> Path:
    path = tmp_path / "forge.json"
    data = {"pr": {"number": 7, "head_sha": head(repo), "author": "alice"}, "reviews": [], "teams": {}, "comments": []}
    path.write_text(json.dumps(data | extra))
    return path


def verdicts(stdout: str) -> dict[str, dict]:
    return {v["id"]: v for v in json.loads(stdout)["verdicts"]}


# --- redraft test helpers --------------------------------------------------------------------

QUOTE = "A seat costs 5 EUR."


def req_yaml(rid: str, text: str = QUOTE, flags: tuple[str, ...] = (), seg: str = "seg-01") -> str:
    flag_lines = "flags:\n" + "".join(f"- {f}\n" for f in flags) if flags else ""
    return (
        f"id: {rid}\nstatement: {text!r}\nevidence:\n  quotes:\n  - source: SRC-n#{seg}\n    text: {text!r}\nmodules:\n- MOD-a\n{flag_lines}"
    )


def adr_md(rid: str, supersedes: str | None = None) -> str:
    sup = f"supersedes: {supersedes}\n" if supersedes else ""
    return (
        f"---\nid: {rid}\ntitle: Seats\n{sup}governs: [MOD-a]\nevidence:\n  quotes:\n  - source: SRC-n#seg-01\n"
        f"    text: {QUOTE!r}\n---\nBody.\n"
    )


def put(root, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)
