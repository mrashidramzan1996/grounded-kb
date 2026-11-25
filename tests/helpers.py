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

def classification(rid: str) -> dict:
    """Explicit default classification for helper-written requirements (never derived from the quote)."""
    out = {"modality": "must", "actor": "system", "origin": "stated"}
    return out | ({"category": "performance"} if rid.startswith("REQ-NF-") else {})


def req(id: str, statement: str, quote: tuple[str, str, str], **fields):
    """A Requirement for acme_kb_with. quote=(source stem, heading suffix, text); the segment is resolved later:
    the first one under that heading containing the text, else the first under it."""

    def build(segment_id):
        from kb.records.models import Requirement

        stem, suffix, text = quote
        data = classification(id) | {
            "id": id,
            "statement": statement,
            "evidence": {"quotes": [{"source": f"SRC-{stem}#{segment_id(stem, suffix, text)}", "text": text}]},
            "modules": ["MOD-billing"],
        } | fields
        return Requirement.model_validate({k: v for k, v in data.items() if v is not None})  # None drops a default

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
    triage.write_text(triage.read_text().replace("[REQ-F-005]", '"none: other: annual pricing is drafted elsewhere"'))
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
    fields = "".join(f"{k}: {v}\n" for k, v in classification(rid).items())
    return (
        f"id: {rid}\nstatement: {text!r}\n{fields}evidence:\n  quotes:\n  - source: SRC-n#{seg}\n    text: {text!r}\nmodules:\n- MOD-a\n{flag_lines}"
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


# --- kb todo helpers ---------------------------------------------------------------------------

TODO_CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-a\n    owner: "@alice"\n'
TODO_DOC = "# Seats\n\nA seat costs 5 EUR.\n"
TODO_TRIAGE = "kb/sources/SRC-n/triage.yaml"


def ok(root: Path, *args: str):
    r = invoke(root, *args)
    assert r.exit_code == 0, r.output
    return r


def todo_json(root: Path) -> dict:
    return json.loads(ok(root, "todo", "--format", "json").stdout)


def todo_repo(tmp_path: Path, config: str = TODO_CONFIG, **docs: str) -> Path:
    """A committed repo with kb.config.yaml and docs/n.md (plus `docs`), nothing ingested."""
    return make_git_repo(None, tmp_path, extra={"kb.config.yaml": config, "docs/n.md": TODO_DOC, **docs})


def todo_ingested(tmp_path: Path, config: str = TODO_CONFIG, **docs: str) -> Path:
    """todo_repo() with every document ingested and committed."""
    root = todo_repo(tmp_path, config, **docs)
    ok(root, "ingest")
    commit(root, "ingest")
    return root


def todo_drafted(tmp_path: Path, config: str = TODO_CONFIG) -> Path:
    """todo_ingested() with REQ-F-001 quoting seg-01 and seg-01 triaged, committed and evaluated: a clean KB."""
    root = todo_ingested(tmp_path, config)
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001]\n")
    commit(root, "drafts")
    evaluated(root)
    return root


def evaluated(root: Path, message: str = "evaluated") -> None:
    """Record an evaluation of the knowledge base as it is now with nothing left open (as `kb eval report --write`
    writes it, and a person's `kb eval decide categories`), then commit: `kb todo` lists no eval item for it."""
    import datetime

    from kb.eval.decisions import DECISIONS
    from kb.eval.findings import fingerprint, summary, write_summary
    from kb.gates.coverage import coverage_stats
    from kb.records.repo import KbRepo
    from kb.yamlio import dump_yaml

    repo = KbRepo.load(root)
    agr = {"matched": 0, "only_kb": 0, "only_b": 0, "kb_items": 0, "b_items": 0, "unlocated": 0, "span_f1": None,
           "estimated_total": None, "entity_f1": None, "relation_f1": None}
    today = datetime.date.today().isoformat()
    write_summary(root, summary(head(root), fingerprint(repo), "0" * 32, coverage_stats(repo), agr, [], today))
    put(root, DECISIONS, dump_yaml({"decisions": [{
        "finding": "categories", "canonical": "categories", "sentences": [], "decision": "not_a_rule",
        "record": None, "record_sha256": None, "reason": "None apply.", "by": "@alice", "date": today,
    }]}))
    commit(root, message)


# --- in-memory repos over one stored Markdown document (entities, graph) -----------------------

def stored_repo(doc: str, files: dict[str, str], src: str = "SRC-spec"):
    """A KbRepo holding `doc` stored as `src` (segmented as `kb source add` would) plus `files` ({kb path: text})."""
    from kb.records.repo import KbRepo
    from kb.sources.segment import assign_segment_ids, segment

    segs, nxt = assign_segment_ids([], segment(nt(doc)), 1)
    source_yaml = (
        f"id: {src}\noriginal: docs/{src.removeprefix('SRC-')}.md\noriginal_sha256: \"{'a' * 64}\"\nnormaliser: text@1\n"
        "trust: spec\nvisibility: internal\nguard:\n  emails: 0\n  secrets: 0\n  injections: 0\n  injection_segments: []\n"
        f"next_seg: {nxt}\n"
    )
    stored = {
        f"kb/sources/{src}/source.yaml": source_yaml,
        f"kb/sources/{src}/text.md": doc,
        f"kb/sources/{src}/segments.jsonl": "".join(
            json.dumps({"id": s.id, "heading": s.heading, "page": s.page, "start": s.start, "end": s.end, "hash": s.hash}) + "\n"
            for s in segs
        ),
    }
    return KbRepo.from_files({p: t.encode() for p, t in (stored | files).items()})
