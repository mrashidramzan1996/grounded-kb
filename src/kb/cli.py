from pathlib import Path, PurePosixPath
from typing import Annotated

import datetime
import json
import sys

import typer

import kb
from kb.config.edit import add_module, set_paths
from kb.config.load import ConfigError, load_config
from kb.check import run_check
from kb.clarify import ClarifyError, clarify as record_clarification
from kb.context import ContextError, governing, render_pack, session_brief
from kb.agents.registry import agent_names, get_agent
from kb.hooks import run_hook
from kb.safewrite import WriteRefused
from kb.baseline import BaselineError, freeze_baseline
from kb.init.scaffold import InitError, init_repo
from kb.gitfiles import GitError, changed_files, head_commit, repo_files, rev_parse, toplevel
from kb.modules import suggest_paths
from kb.ingest.brief import BRIEF_PATH, build_brief, ingest_sources, write_base, write_brief
from kb.cache import remove_cache, write_cache
from kb.ingest.redraft import (
    REDRAFT_BASE, REDRAFT_BRIEF, REDRAFT_FILES, REDRAFT_IDS, REDRAFT_NEW, RedraftError, build_redraft_brief, changed_segments, changed_text,
    citing, new_text, ref_key, segment_hashes, select, unfound, untriaged, untriaged_cited,
)
from kb.ingest.redraft_report import redraft_report, render_redraft_report
from kb.ingest.report import ingest_report, render_report
from kb.records.io import RecordError
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath
from kb.sources.readers import SourceError
from kb.sources.store import add_sources, sync_sources
from kb.status import requirement_status, summarize, verified_ids
from kb.todo import build_todo, render_todo_json, render_todo_text, summary_line as todo_summary
from kb.trace import TraceError, trace_chain
from kb.forge import ForgeError, forge_from_option
from kb.testrun import TestRunError
from kb.verify.report import render_json, render_markdown, render_text, summary_line, write_outputs
from kb.verify.run import run_verify
from kb.yamlio import YamlError

app = typer.Typer(help="Git-native knowledge base.", no_args_is_help=True)

# What a command reports as a usage, config or tool error (exit 2, the message on stderr, no traceback).
TOOL_ERRORS = (
    ConfigError, SourceError, GitError, UnsafePath, RecordError, YamlError, OSError,
    ContextError, TraceError, BaselineError, WriteRefused, TestRunError, ForgeError,
)


def _fail(e: Exception) -> typer.Exit:
    """The one error exit: the message on stderr, exit 2."""
    typer.echo(str(e) or type(e).__name__, err=True)
    return typer.Exit(2)


@app.callback()
def main(
    ctx: typer.Context,
    root: Annotated[
        Path | None, typer.Option("-C", "--root", help="Repository root (default: the current directory).")
    ] = None,
) -> None:
    ctx.obj = {"root": root if root is not None else Path("."), "root_explicit": root is not None}


@app.command()
def version() -> None:
    """Print the kb version."""
    typer.echo(kb.__version__)


@app.command()
def init(
    ctx: typer.Context,
    ci: Annotated[str, typer.Option("--ci", help="CI template: github, gitlab or none.")] = "github",
    install_spec: Annotated[str | None, typer.Option("--install-spec", help="Package spec CI installs kb from.")] = None,
) -> None:
    """Scaffold kb.config.yaml, agent rules, CODEOWNERS block and CI. Modules only from CODEOWNERS."""
    if ci not in ("github", "gitlab", "none"):
        typer.echo(f"unknown --ci {ci!r}: use github, gitlab or none", err=True)
        raise typer.Exit(2)
    extra = {"install_spec": install_spec} if install_spec is not None else {}
    try:
        result = init_repo(Path(ctx.obj["root"]), ci=ci, **extra)  # type: ignore[arg-type]
    except (InitError, ConfigError, OSError) as e:
        typer.echo(f"kb init: {e}", err=True)
        raise typer.Exit(2) from e
    for rel in result.files_written:
        typer.echo(f"wrote {rel}")
    for issue in result.issues:
        typer.echo(f"{issue.severity} {issue.code}: {issue.message}", err=True)
    typer.echo("next: set policy.approvers in kb.config.yaml (TODO), then `kb check`.", err=True)


source_app = typer.Typer(help="Manage product-document sources.", no_args_is_help=True)
app.add_typer(source_app, name="source")


def _run(ctx: typer.Context, action) -> None:
    root = Path(ctx.obj["root"])
    try:
        sources = action(root, load_config(root / "kb.config.yaml"))
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    repo = KbRepo.load(root)
    for s in sources:
        g = s.guard
        typer.echo(
            f"{s.id}: {len(repo.segments(s.id))} segments, "
            f"guard: {g.emails} emails, {g.secrets} secrets, {g.injections} injections"
        )
    _warn_empty(repo, sources)


def _warn_empty(repo: KbRepo, sources) -> None:
    for s in sources:
        if not repo.segments(s.id):
            typer.echo(f"warning source.empty: {s.original} gave no text (scanned or image-only?); nothing to quote", err=True)


@source_app.command("add")
def source_add(ctx: typer.Context, paths: Annotated[list[Path], typer.Argument(help="Documents to add.")]) -> None:
    """Read, mask, segment and store documents under kb/sources/."""

    def add(root: Path, config):
        return add_sources(root, [p.as_posix() if not p.is_absolute() else str(p) for p in paths], config)

    _run(ctx, add)


@source_app.command("sync")
def source_sync(ctx: typer.Context) -> None:
    """Re-read every stored source from its original."""
    root = Path(ctx.obj["root"])
    before = KbRepo.load(root)
    _run(ctx, sync_sources)
    after = KbRepo.load(root)
    changed = changed_segments(before, after)
    ids = citing(after, changed)
    broken = set(unfound(after))
    # quotes broken before the sync are named apart, and only when the sync changed which quotes are found
    still_broken = sorted(broken - set(ids)) if broken != set(unfound(before)) else []
    known = segment_hashes(before)
    added = [ref for ref in new_text(after, after) if ref not in known]  # new text the sync added
    _, uncited = changed_text(before, after)
    for line in _sync_hint(after, ids, still_broken, added, sorted(uncited, key=ref_key)):
        typer.echo(line, err=True)
    touched = changed | (set(segment_hashes(after)) - set(known))
    for issue in untriaged_cited(after, after, touched):  # --new does not offer these
        typer.echo(f"warning {issue.code}: {issue.message}", err=True)


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _sync_hint(after: KbRepo, ids: list[str], broken: list[str], added: list[str], uncited: list[str]) -> list[str]:
    """Name the records citing changed text, the records whose quotes were already not found (`broken`), and the
    new or changed segments with no records yet (`added`: new and untriaged; `uncited`: changed, cited by no live
    record), and the redraft to run before committing the sync."""
    fresh = sorted(set(added) | set(uncited), key=ref_key)
    if not ids and not broken and not fresh:
        return []

    def names(rids: list[str]) -> str:
        return ", ".join(
            f"{i} ({', '.join(after.records[i].flags)})" if getattr(after.records[i], "flags", None) else i for i in rids
        )

    lines = []
    if ids:
        lines.append(f"{_count(len(ids), 'record cites', 'records cite')} changed text: {names(ids)}.")
    if broken:
        lines.append(f"{_count(len(broken), 'record has', 'records have')} quotes not found: {names(broken)}.")
    if fresh:
        refs = ", ".join(fresh[:10]) + (f" … and {len(fresh) - 10} more" if len(fresh) > 10 else "")
        what = _count(len(fresh), "new or changed segment has", "new or changed segments have")
        lines.append(f"{what} no records yet: {refs}.")
    flags = " ".join(f for f, on in [("--changed", ids or broken or uncited), ("--new", added)] if on)
    verb = ("redraft and draft" if ids or broken else "draft") if fresh else "redraft"
    lines.append(f"Before committing, run `kb redraft {flags}` to {verb} them.")
    return lines


@app.command()
def ingest(
    ctx: typer.Context,
    paths: Annotated[list[Path] | None, typer.Argument(help="Documents or directories; default: every configured source.")] = None,
    report: Annotated[bool, typer.Option("--report", help="Gate what the agent drafted and print a report.")] = False,
    strict: Annotated[bool, typer.Option("--strict", help="With --report: exit 1 when anything was rejected or a source has an error.")] = False,
    base: Annotated[str | None, typer.Option("--base", help="With --report: compare against this ref (default HEAD).")] = None,
) -> None:
    """Store sources and write a drafting brief for your coding agent; --report gates what it drafted."""
    root = Path(ctx.obj["root"])
    if report and paths:
        typer.echo("--report takes no paths", err=True)
        raise typer.Exit(2)
    if not report and (strict or base):
        typer.echo("--strict and --base need --report", err=True)
        raise typer.Exit(2)
    try:
        if report:
            result = ingest_report(root, base)
        else:
            config = load_config(root / "kb.config.yaml")
            rels = [p.as_posix() if not p.is_absolute() else str(p) for p in paths or []]
            base = head_commit(root)
            sources, skipped = ingest_sources(root, config, rels)
            write_brief(root, build_brief(root, config, [s.id for s in sources], base))
            write_base(root, base)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    if report:
        typer.echo(render_report(result), nl=False)
        if not result.drafted:
            typer.echo(f"warning: nothing drafted since {result.base}; give the agent the brief, or pass --base", err=True)
        if strict and (result.failed or not result.drafted):
            raise typer.Exit(1)
        return
    for rel in skipped:
        typer.echo(f"skipped (no reader): {rel}", err=True)
    _warn_empty(KbRepo.load(root), sources)
    typer.echo(f"Brief: {BRIEF_PATH}")
    typer.echo(f"Ask your coding agent: *follow {BRIEF_PATH}*")


@app.command()
def redraft(
    ctx: typer.Context,
    ids: Annotated[list[str] | None, typer.Argument(help="Record ids (REQ-*, ADR-*, INV-*) to redraft.")] = None,
    flagged: Annotated[bool, typer.Option("--flagged", help="Also select live requirements with flags.")] = False,
    changed: Annotated[bool, typer.Option("--changed", help="Also select records citing changed text or with a quote not found, and offer changed text no record cites.")] = False,
    new: Annotated[bool, typer.Option("--new", help="Also offer the segments with no triage entry, to draft new records from.")] = False,
    report: Annotated[bool, typer.Option("--report", help="Gate what the agent redrafted and print a report.")] = False,
    strict: Annotated[bool, typer.Option("--strict", help="With --report: exit 1 when anything was rejected.")] = False,
    base: Annotated[str | None, typer.Option(
        "--base", help="With --changed: the commit before the sync, when it is already committed (default HEAD). "
        "With --report: the commit to compare against (default: the brief's commit).",
    )] = None,
) -> None:
    """Write a focused brief for your coding agent to redraft selected records; --report gates the redraft."""
    root = Path(ctx.obj["root"])
    if report:
        if ids or flagged or changed or new:
            typer.echo("--report takes no ids, --flagged, --changed or --new", err=True)
            raise typer.Exit(2)
        try:
            result = redraft_report(root, base)
        except (RedraftError, *TOOL_ERRORS) as e:
            raise _fail(e) from e
        typer.echo(render_redraft_report(result), nl=False)
        if strict and result.rejected:
            raise typer.Exit(1)
        return
    if strict:
        typer.echo("--strict needs --report", err=True)
        raise typer.Exit(2)
    if not ids and not flagged and not changed and not new:
        typer.echo("name record ids, or use --flagged, --changed or --new", err=True)
        raise typer.Exit(2)
    if base and not changed:
        typer.echo("--base needs --changed or --report", err=True)
        raise typer.Exit(2)
    try:
        # the brief, its base file and a later --report all name the commit compared against
        commit = rev_parse(root, base) if base else head_commit(root)
        if commit is None:
            raise GitError(f"unknown base {base!r}")
        repo = KbRepo.load(root)
        before = KbRepo.at_ref(root, commit) if changed or new else None
        selection = select(repo, sorted(set(ids or [])), flagged_=flagged, changed=changed, before=before)
        cited, uncited = changed_text(before, repo) if changed and before else (set(), set())
        # text to draft from: untriaged segments no record cites at base (--new), and changed segments no live
        # record cites (--changed)
        fresh = sorted(set(new_text(repo, before) if new and before else []) | uncited, key=ref_key)
        if new and before:
            for issue in untriaged_cited(repo, before, set(untriaged(repo)) - cited):
                typer.echo(f"warning {issue.code}: {issue.message}", err=True)
        if not selection and not fresh:
            for rel in REDRAFT_FILES:  # a later --report must not gate against a stale brief
                remove_cache(root, rel)
            hint = "; if you already committed the sync, pass --base <commit before it>" if changed and not base else ""
            typer.echo(f"nothing to redraft{hint}", err=True)
            return
        # the segments new records may cite: those offered to draft from, and the changed ones the records cite
        offered = sorted(set(fresh) | cited, key=ref_key)
        write_cache(root, REDRAFT_BRIEF, build_redraft_brief(root, repo, selection, commit, fresh))
        write_cache(root, REDRAFT_BASE, commit + "\n")
        write_cache(root, REDRAFT_IDS, "".join(f"{rid}\n" for rid in selection))
        write_cache(root, REDRAFT_NEW, "".join(f"{ref}\n" for ref in offered))
    except (RedraftError, *TOOL_ERRORS) as e:
        raise _fail(e) from e
    typer.echo(f"Brief: {REDRAFT_BRIEF}")
    typer.echo(f"Ask your coding agent: *follow {REDRAFT_BRIEF}*")


@app.command()
def clarify(
    ctx: typer.Context,
    topic: Annotated[str, typer.Option("--topic", help="What the question was about; becomes the section heading.")],
    text: Annotated[str, typer.Option("--text", help="The person's answer, as one plain paragraph.")],
    by: Annotated[str, typer.Option("--by", help="Who decided: @handle or @org/team.")],
    date: Annotated[str | None, typer.Option("--date", help="When they decided, YYYY-MM-DD (default: today).")] = None,
) -> None:
    """Record a person's answer in docs/clarifications.md as a source an agent can quote."""
    root = Path(ctx.obj["root"])
    try:
        _, ref = record_clarification(root, topic, " ".join(text.split()) if "\n" in text.strip() else text, by,
                                      date or datetime.date.today().isoformat())
    except (ClarifyError, *TOOL_ERRORS) as e:
        raise _fail(e) from e
    typer.echo(f"recorded in docs/clarifications.md; quote it as {ref}")


module_app = typer.Typer(help="Manage modules; paths come later, from evidence.", no_args_is_help=True)
app.add_typer(module_app, name="module")


@module_app.command("add")
def module_add(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Module id, e.g. MOD-billing.")],
    owner: Annotated[str, typer.Option("--owner", help="Team key or @handle (or TODO).")],
    risk: Annotated[str, typer.Option("--risk", help="low, standard or high.")] = "standard",
    title: Annotated[str | None, typer.Option("--title")] = None,
) -> None:
    """Add a module with no paths (planned)."""
    try:
        add_module(Path(ctx.obj["root"]), id, owner=owner, risk=risk, title=title)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    typer.echo(f"Added {id} (planned: no paths yet)")


@module_app.command("paths")
def module_paths(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument(help="Module id.")],
    accept: Annotated[bool, typer.Option("--accept", help="Write the paths suggested from r[impl] evidence.")] = False,
    set_: Annotated[list[str] | None, typer.Option("--set", help="Set the module's paths to these globs (repeatable).")] = None,
) -> None:
    """Set a module's paths: from evidence (--accept) or explicitly (--set)."""
    root = Path(ctx.obj["root"])
    if accept == bool(set_):
        typer.echo("give exactly one of --accept or --set GLOB", err=True)
        raise typer.Exit(2)
    try:
        config = load_config(root / "kb.config.yaml")
        module = next((m for m in config.modules if m.id == id), None)
        if module is None:
            raise ConfigError([f"unknown module {id}"])
        if set_:
            globs = list(set_)
        else:
            suggested = suggest_paths(root, config, KbRepo.load(root), repo_files(root)).get(id)
            if not suggested:
                typer.echo(f"no suggestion for {id}: no r[impl] evidence for new paths", err=True)
                raise typer.Exit(1)
            globs = sorted({*module.paths, *suggested})
        set_paths(root, id, globs)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    typer.echo(f"{id}: paths = {', '.join(globs)}")


@module_app.command("list")
def module_list(ctx: typer.Context) -> None:
    """List modules and their paths."""
    try:
        config = load_config(Path(ctx.obj["root"]) / "kb.config.yaml")
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    for m in config.modules:
        typer.echo(f"{m.id}  {', '.join(m.paths) if m.paths else '(planned: no paths yet)'}  owner: {m.owner}  risk: {m.risk}")


@app.command()
def status(
    ctx: typer.Context,
    format: Annotated[str, typer.Option("--format", help="text or json.")] = "text",
) -> None:
    """Show how many live requirements are built and tested, per module."""
    if format not in ("text", "json"):
        typer.echo(f"unknown format {format!r}: use text or json", err=True)
        raise typer.Exit(2)
    root = Path(ctx.obj["root"])
    try:
        config = load_config(root / "kb.config.yaml")
        repo = KbRepo.load(root)
        verified = verified_ids(root)
        summary = summarize(config, repo, requirement_status(root, config, repo), verified)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    if verified is None:
        typer.echo("run `kb verify --all` for verified counts", err=True)
    failed = sorted({i.path for i in repo.errors if i.path})
    if repo.errors:
        typer.echo(f"{len(failed)} KB files failed to load (run `kb check`): {', '.join(failed[:3])}", err=True)
    if format == "json":
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
    else:
        for name, c in [*summary["modules"].items(), ("total", summary["total"])]:
            line = f"{name}: {c['built']}/{c['total']} built, {c['tested']}/{c['total']} tested"
            typer.echo(line + (f", {c['verified']}/{c['total']} verified" if "verified" in c else ""))
    if repo.errors:
        raise typer.Exit(1)


baseline_app = typer.Typer(help="Freeze the agreed requirement set.", no_args_is_help=True)
app.add_typer(baseline_app, name="baseline")


@baseline_app.command("freeze")
def baseline_freeze(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Baseline version, e.g. v1.")],
    allow_branch: Annotated[bool, typer.Option("--allow-branch", help="Freeze even when HEAD is not the default branch's tip.")] = False,
) -> None:
    """Write kb/baseline/VERSION.yaml and tag HEAD (the file is left for you to commit). Refuses while
    `kb check` reports errors, and off the default branch's tip unless --allow-branch."""
    try:
        path = freeze_baseline(Path(ctx.obj["root"]), version, allow_branch=allow_branch)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    rel = path.relative_to(Path(ctx.obj["root"])).as_posix()
    typer.echo(f"wrote {rel}; tagged HEAD as kb-baseline-{version}; commit the baseline file")


@app.command()
def check(
    ctx: typer.Context,
    format: Annotated[str, typer.Option("--format", help="text or json.")] = "text",
    strict: Annotated[bool, typer.Option("--strict", help="Exit 1 on any error, whatever the policy mode.")] = False,
    base: Annotated[str | None, typer.Option("--base", help="Also guard accepted decisions against this ref.")] = None,
    changed: Annotated[bool, typer.Option("--changed", help="Limit file-level checks to files changed against --base (default: the default branch).")] = False,
) -> None:
    """Run every gate: config, records, quotes, anchors, sources, modules, triage."""
    if format not in ("text", "json"):
        typer.echo(f"unknown format {format!r}: use text or json", err=True)
        raise typer.Exit(2)
    root = Path(ctx.obj["root"])
    if not (root / "kb.config.yaml").exists():
        typer.echo(f"{root / 'kb.config.yaml'} not found; run `kb init` to create it", err=True)
        raise typer.Exit(2)
    try:
        report = run_check(root, base=base, changed_only=changed)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    errors = sum(i.severity == "error" for i in report.issues)
    warnings = len(report.issues) - errors
    if format == "json":
        typer.echo(json.dumps({
            "mode": report.mode,
            "issues": [
                {"code": i.code, "severity": i.severity, "path": i.path, "record": i.record, "message": i.message}
                for i in report.issues
            ],
            "summary": {"errors": errors, "warnings": warnings},
        }, indent=2))
    else:
        for i in report.issues:
            message = " ".join(i.message.split())
            typer.echo(f"{i.severity} {i.code} {i.path or '-'} {i.record or '-'}: {message}")
    typer.echo(f"{errors} errors, {warnings} warnings (mode: {report.mode})", err=True)
    if errors and (strict or report.mode == "enforce"):
        raise typer.Exit(1)


@app.command()
def todo(
    ctx: typer.Context,
    format: Annotated[str, typer.Option("--format", help="text or json.")] = "text",
) -> None:
    """List what your coding agent should do next, and what it must ask you. Exit 0 whatever is listed."""
    if format not in ("text", "json"):
        typer.echo(f"unknown format {format!r}: use text or json", err=True)
        raise typer.Exit(2)
    try:
        result = build_todo(Path(ctx.obj["root"]))
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    typer.echo((render_todo_json if format == "json" else render_todo_text)(result), nl=False)
    typer.echo(todo_summary(result), err=True)


_RENDER = {"text": render_text, "json": render_json, "markdown": render_markdown}


@app.command()
def verify(
    ctx: typer.Context,
    base: Annotated[str | None, typer.Option("--base", help="Base branch (default: the default branch).")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Check every claim, not only those the change affects.")] = False,
    format: Annotated[str, typer.Option("--format", help="text, json or markdown.")] = "text",
    forge: Annotated[str, typer.Option("--forge", help="auto, none, github or local:PATH.")] = "auto",
    comment: Annotated[bool, typer.Option("--comment", help="Post the Markdown report on the pull request.")] = False,
    strict: Annotated[bool, typer.Option("--strict", help="Exit 1 on anything blocking, whatever the policy mode.")] = False,
) -> None:
    """Run the linked tests and derive claim, requirement and invariant states; check stale context and owner approval.
    Writes .kb/cache/status.json and .kb/cache/verify.md."""
    if format not in _RENDER:
        typer.echo(f"unknown format {format!r}: use text, json or markdown", err=True)
        raise typer.Exit(2)
    root = Path(ctx.obj["root"])
    if not (root / "kb.config.yaml").exists():
        typer.echo(f"{root / 'kb.config.yaml'} not found; run `kb init` to create it", err=True)
        raise typer.Exit(2)
    try:
        port = forge_from_option(forge, root)
    except (ValueError, *TOOL_ERRORS) as e:  # ValueError: an unknown --forge value
        raise _fail(e) from e
    try:
        report = run_verify(root, base=base, all_=all_, forge=port)
        write_outputs(root, report)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    typer.echo(_RENDER[format](report), nl=False)
    if comment:
        if port is None or report.pr is None:
            typer.echo("no pull request; comment skipped", err=True)
        else:
            try:
                port.upsert_comment(report.pr, render_markdown(report))
            except ForgeError as e:  # e.g. a read-only token on a fork PR: the exit code is decided by policy only
                typer.echo(f"kb: could not post the PR comment: {e}", err=True)
            except TOOL_ERRORS as e:
                raise _fail(e) from e
    typer.echo(summary_line(report), err=True)
    if report.blocking and (strict or report.mode == "enforce"):
        raise typer.Exit(1)


def _repo_rel(root: Path, path: str) -> str:
    """A path given on the command line as a repo-relative POSIX path (unchanged when it is outside the repo)."""
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path
    return PurePosixPath(p.as_posix()).as_posix()


@app.command()
def context(
    ctx: typer.Context,
    paths: Annotated[list[str] | None, typer.Option("--paths", help="Files you will touch (repeatable).")] = None,
    module: Annotated[list[str] | None, typer.Option("--module", help="Module id (repeatable).")] = None,
    feature: Annotated[list[str] | None, typer.Option("--feature", help="Feature id (repeatable).")] = None,
    id: Annotated[list[str] | None, typer.Option("--id", help="Record id (repeatable).")] = None,
    changed: Annotated[bool, typer.Option("--changed", help="Files changed against the default branch.")] = False,
    brief: Annotated[bool, typer.Option("--session-brief", help="What a coding agent gets at session start.")] = False,
    format: Annotated[str, typer.Option("--format", help="md or json.")] = "md",
) -> None:
    """Print the live requirements, decisions and invariants governing what you are about to touch."""
    given = [bool(paths), bool(module), bool(feature), bool(id), changed, brief]
    if sum(given) != 1:
        typer.echo("give exactly one of --paths, --module, --feature, --id, --changed or --session-brief", err=True)
        raise typer.Exit(2)
    if format not in ("md", "json"):
        typer.echo(f"unknown format {format!r}: use md or json", err=True)
        raise typer.Exit(2)
    if brief and format != "md":
        typer.echo("--session-brief is Markdown only", err=True)
        raise typer.Exit(2)
    root = Path(ctx.obj["root"])
    try:
        if brief:
            out = session_brief(root)
            repo = KbRepo.load(root)
        else:
            config = load_config(root / "kb.config.yaml")
            repo = KbRepo.load(root)
            files = changed_files(root) if changed else [_repo_rel(root, p) for p in paths or []]
            pack = governing(root, config, repo, paths=files, modules=module or (), features=feature or (), ids=id or ())
            out = render_pack(pack, format)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    typer.echo(out, nl=False)
    if repo.errors:
        failed = sorted({i.path for i in repo.errors if i.path})
        typer.echo(f"{len(failed)} KB files failed to load (run `kb check`): {', '.join(failed[:3])}", err=True)
        raise typer.Exit(1)


@app.command()
def trace(ctx: typer.Context, id: Annotated[str, typer.Argument(help="A record id or SRC-…#seg-NN.")]) -> None:
    """Show the evidence chain of a record (re-checking its quotes), or every record resting on a segment."""
    try:
        lines, ok = trace_chain(Path(ctx.obj["root"]), id)
    except TOOL_ERRORS as e:
        raise _fail(e) from e
    for line in lines:
        typer.echo(line)
    if not ok:
        raise typer.Exit(1)


@app.command()
def hook(
    ctx: typer.Context,
    event: Annotated[str, typer.Argument(help="session-start, stop or pre-commit.")],
    agent: Annotated[str, typer.Option("--agent", help="claude-code, codex or generic.")] = "generic",
) -> None:
    """Agent hook entry point: reads the hook payload on stdin, answers in the agent's format."""
    stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    root = Path(ctx.obj["root"])
    if not ctx.obj["root_explicit"]:
        root = toplevel(Path(".")) or root  # git missing or unusable: keep the current directory (fail safe)
    try:
        out, code = run_hook(root, event, agent, stdin)
    except KeyError as e:
        typer.echo(e.args[0], err=True)
        raise typer.Exit(2) from e
    typer.echo(out, nl=False)
    if code:
        raise typer.Exit(code)


agents_app = typer.Typer(help="Wire kb into coding agents.", no_args_is_help=True)
app.add_typer(agents_app, name="agents")


@agents_app.command("install")
def agents_install(
    ctx: typer.Context,
    names: Annotated[list[str], typer.Argument(help=f"One or more of: {', '.join(agent_names())}.")],
) -> None:
    """Install the session-start and stop hooks (claude-code, codex; claude-code also gets the kb skill) or a git pre-commit hook (generic)."""
    try:
        integrations = [get_agent(n) for n in names]
    except KeyError as e:
        typer.echo(e.args[0], err=True)
        raise typer.Exit(2) from e
    for integration in integrations:
        try:
            lines = integration.install(Path(ctx.obj["root"]))
        except (WriteRefused, OSError) as e:
            typer.echo(f"kb agents install {integration.name}: {e}", err=True)
            raise typer.Exit(2) from e
        for line in lines:
            typer.echo(line)
