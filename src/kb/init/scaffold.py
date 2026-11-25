"""`kb init`: scaffold config, agent files, CODEOWNERS block and CI. Nothing is guessed: modules come only
from CODEOWNERS directory rules, approvers stay TODO. No model calls, no fact records."""

import json
import os
import re
import stat
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Literal

from kb.config.codeowners import parse_codeowners
from kb.gitfiles import GitError, default_branch
from kb.issues import Issue
from kb.rules import render
from kb.safewrite import WriteRefused, check_target, read_text, write_atomic
from kb.textblocks import upsert_block

CODEOWNERS_LOCATIONS = ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"]
CODEOWNERS_DEFAULT = "CODEOWNERS"
_APPROVAL_PATHS = [
    "/kb/requirements/",
    "/kb/exclusions/",
    "/kb/features/",
    "/kb/glossary/",
    "/kb/sources/*/triage.yaml",
    "/kb/eval/",
    "/kb/decisions/",
    "/kb/invariants/",
    "/kb/components/",
    "/kb.config.yaml",
]
# CI workflows that run `kb verify` (the files `kb init` writes): a change to one can drop or weaken the gate
CI_WORKFLOWS = [".github/workflows/kb.yml", ".gitlab-ci.yml"]
# the gate itself: who decides (CODEOWNERS) and what runs the check; the decision approvers own them, as kb.config.yaml
_GATE_PATHS = [f"/{p}" for p in (*CI_WORKFLOWS, *CODEOWNERS_LOCATIONS)]
_SAFE_SPEC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+=<>!~,\[\]-]*$")
_SAFE_BASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_WILDCARDS = set("*?[]\\!")


class InitError(Exception):
    """The repository cannot be initialised as asked; nothing was written."""


@dataclass
class InitResult:
    files_written: list[str]
    issues: list[Issue] = field(default_factory=list)


def _template(name: str) -> str:
    return (resources.files("kb.init") / "templates" / name).read_text(encoding="utf-8")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _directory_of(pattern: str) -> str | None:
    """The repo-relative directory a CODEOWNERS pattern names, or None for file/wildcard patterns."""
    if pattern.endswith("/**"):
        pattern = pattern[:-3]
    elif pattern.endswith("/"):
        pattern = pattern.rstrip("/")
    else:
        return None
    directory = pattern.lstrip("/")
    parts = directory.split("/")
    if not directory or _WILDCARDS & set(directory) or any(p in ("", ".", "..") for p in parts):
        return None
    return directory


def modules_from_codeowners(text: str) -> tuple[list[dict[str, str]], list[Issue]]:
    """One module per CODEOWNERS rule naming a directory; later rules for the same directory win (as on GitHub)."""
    owners_by_dir: dict[str, str] = {}
    for pattern, owners in parse_codeowners(text):
        directory = _directory_of(pattern)
        if directory is not None:
            owners_by_dir[directory] = owners[0] if owners and owners[0].startswith("@") else "TODO"
    last = {d: _slug(d.rsplit("/", 1)[-1]) for d in owners_by_dir}
    modules: list[dict[str, str]] = []
    issues: list[Issue] = []
    used: set[str] = set()
    for directory, owner in owners_by_dir.items():
        slug = last[directory]
        if sum(s == slug for s in last.values()) > 1:
            slug = _slug(directory)
        if not slug or slug in used:
            issues.append(Issue("init.module_skipped", f"no unique module id for CODEOWNERS directory {directory}/", "warning"))
            continue
        used.add(slug)
        modules.append({"id": f"MOD-{slug}", "paths": f"{directory}/**", "owner": owner})
    return modules, issues


def _render_config(modules: list[dict[str, str]], has_docs: bool) -> str:
    q = json.dumps
    out = [
        "# kb.config.yaml, written by `kb init`. Nothing here is guessed: fill in what is marked TODO.",
        "# `kb check` reads this file.",
        "version: 1",
        "",
        "# Short team names -> forge handles. None generated: use the forge handle (@org/team) directly below.",
        "# A team can list `members: [login, ...]` (logins, no @); kb uses them when the forge cannot read the team.",
        "teams: {}",
        "",
        "policy:",
        "  # warn-only: `kb check` reports problems but exits 0. Switch to `enforce` once the KB is trusted.",
        "  mode: warn-only",
        "  approvers:",
        "    # TODO: forge handles (e.g. \"@acme/product\") that approve intent (requirements, exclusions, features)...",
        "    intent: [TODO]",
        "    # TODO: ...and decisions (ADRs, invariants).",
        "    decisions: [TODO]",
        "",
    ]
    if has_docs:
        out += [
            "# Product documents whose text the KB may quote. `trust: spec` = authoritative.",
            "sources:",
            f"  - paths: [{q('docs/**')}]",
            "    trust: spec",
        ]
    else:
        out += ["# Product documents the KB may quote; add e.g. `- paths: [\"docs/**\"]`.", "sources: []"]
    out.append("")
    if modules:
        out += [
            "# Modules come only from CODEOWNERS directory rules; owner is the rule's first handle (TODO if none).",
            "modules:",
        ]
        for m in modules:
            out += [f"  - id: {m['id']}", f"    paths: [{q(m['paths'])}]", f"    owner: {q(m['owner'])}", "    risk: standard"]
    else:
        out += ["# add with: kb module add (or let kb ingest propose them)", "modules: []"]
    return "\n".join(out) + "\n"


def agents_block() -> str:
    """The kb block of AGENTS.md: the working rules and the playbook."""
    return render(_template("agents_block.md"))


def upsert_agents_md(root: Path) -> str:
    """Set AGENTS.md's kb block to agents_block(), as `kb init` writes it, creating the file when it is missing and
    leaving everything outside the block alone. Raises WriteRefused (nothing written) for a symlinked or
    non-regular AGENTS.md, one that cannot be read, or unbalanced kb markers."""
    root = Path(root)
    exists = check_target(root, "AGENTS.md", "file")
    current = read_text(root, "AGENTS.md") if exists else ""
    try:
        text = upsert_block(current, "kb", agents_block(), "html")
    except ValueError as e:
        raise WriteRefused(f"AGENTS.md: {e}") from e
    if exists and text == current:
        return "AGENTS.md is up to date"
    write_atomic(root, "AGENTS.md", text)
    return "updated AGENTS.md (kb block)"


def _codeowners_block() -> str:
    lines = [
        "# kb: approvals for the knowledge base. Set policy.approvers in kb.config.yaml, then",
        "# uncomment these lines and replace TODO with the approving team(s).",
    ]
    gate = [
        "# The gate itself (the config above, CODEOWNERS and the CI workflow that runs kb verify):",
        "# replace TODO with the decision approvers.",
    ]
    return "\n".join(lines + [f"# {p} TODO" for p in _APPROVAL_PATHS] + gate + [f"# {p} TODO" for p in _GATE_PATHS])


def _check_target(root: Path, rel: str, kind: Literal["file", "dir"]) -> bool:
    return check_target(root, rel, kind, InitError)


def _read(root: Path, rel: str) -> str:
    return read_text(root, rel, InitError)


_write_atomic = write_atomic


def _base_branch(root: Path) -> str:
    try:
        name = default_branch(root).removeprefix("origin/")
    except (GitError, OSError):
        return "main"
    return name if _SAFE_BASE.match(name) else "main"


def init_repo(
    root: Path,
    *,
    ci: Literal["github", "gitlab", "none"] = "github",
    install_spec: str | None = None,
) -> InitResult:
    """Scaffold kb in `root`. Raises InitError (nothing written) if kb.config.yaml exists, a target is a
    symlink or not a regular file, or an argument is unsafe. Existing user content is never rewritten
    outside kb's marked blocks; existing kb/README.md and CI files are left alone (CI: snippet reported).
    Without install_spec the CI templates install nothing: they warn until KB_INSTALL_SPEC is set (I6)."""
    root = Path(root)
    if install_spec is not None and not _SAFE_SPEC.match(install_spec):
        raise InitError(f"unsafe --install-spec {install_spec!r}")
    if os.path.lexists(root / "kb.config.yaml"):
        raise InitError("kb.config.yaml already exists; kb init does not overwrite it")

    ci_path = {"github": ".github/workflows/kb.yml", "gitlab": ".gitlab-ci.yml", "none": None}[ci]
    existing: dict[str, bool] = {}
    for rel in [".gitignore", "AGENTS.md", "CLAUDE.md", *CODEOWNERS_LOCATIONS, "kb/README.md", ".gitlab-ci.yml", ".github/workflows/kb.yml"]:
        existing[rel] = _check_target(root, rel, "file")
    _check_target(root, "kb", "dir")
    if ci_path == ".github/workflows/kb.yml":
        _check_target(root, ".github/workflows", "dir")

    codeowners_rel = next((p for p in CODEOWNERS_LOCATIONS if existing[p]), CODEOWNERS_DEFAULT)
    codeowners_text = _read(root, codeowners_rel) if existing[codeowners_rel] else ""
    modules, issues = modules_from_codeowners(codeowners_text)
    has_docs = _check_target(root, "docs", "dir")

    files: dict[str, str] = {"kb.config.yaml": _render_config(modules, has_docs)}
    for rel, name, content, style in [
        ("AGENTS.md", "kb", agents_block(), "html"),
        ("CLAUDE.md", "kb", "@AGENTS.md", "html"),
        (codeowners_rel, "kb", _codeowners_block(), "hash"),
        (".gitignore", "kb", ".kb/", "hash"),  # A-11: caches and the drafting brief are never committed
    ]:
        try:
            files[rel] = upsert_block(_read(root, rel) if existing.get(rel) else "", name, content, style)  # type: ignore[arg-type]
        except ValueError as e:
            raise InitError(f"{rel}: {e}") from e
    if existing["kb/README.md"]:
        issues.append(Issue("init.kept", "kb/README.md already exists and was left as is", "warning", path="kb/README.md"))
    else:
        files["kb/README.md"] = _template("kb_readme.md")
    if ci_path:
        ci_text = (
            _template("github_workflow.yml" if ci == "github" else "gitlab_ci.yml")
            .replace("{{install_spec}}", install_spec or "")
            .replace("{{base}}", _base_branch(root))
        )
        if existing[ci_path]:
            issues.append(Issue(
                "init.ci_exists",
                f"{ci_path} already exists and was left as is; add this to it:\n{ci_text}",
                "warning",
                path=ci_path,
            ))
        else:
            files[ci_path] = ci_text

    # kb.config.yaml goes last: a failed run then leaves no config and `kb init` can simply be re-run
    order = sorted(files, key=lambda rel: (rel == "kb.config.yaml", rel))
    _write_all(root, files, order)
    return InitResult(sorted(files), issues)


def _write_all(root: Path, files: dict[str, str], order: list[str]) -> None:
    """Write `files` in `order`; on any failure restore every original and remove what this run created."""
    originals: dict[str, tuple[bytes, int] | None] = {}
    new_dirs: list[Path] = []
    for rel in order:
        path = root / rel
        try:
            originals[rel] = (path.read_bytes(), stat.S_IMODE(os.lstat(path).st_mode))
        except FileNotFoundError:
            originals[rel] = None
        parent = path.parent
        missing = []
        while parent != root and not parent.exists():
            missing.append(parent)
            parent = parent.parent
        new_dirs += [d for d in reversed(missing) if d not in new_dirs]
    try:
        for rel in order:
            _write_atomic(root, rel, files[rel])
    except BaseException:
        for rel, original in originals.items():
            path = root / rel
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original[0])
                os.chmod(path, original[1])
        for d in reversed(new_dirs):
            try:
                d.rmdir()
            except OSError:
                pass
        raise
