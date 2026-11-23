from pathlib import Path
from typing import Annotated

import json

import typer

import kb
from kb.config.edit import add_module, set_paths
from kb.config.load import ConfigError, load_config
from kb.baseline import BaselineError, freeze_baseline
from kb.gitfiles import GitError, repo_files
from kb.modules import suggest_paths
from kb.ingest.brief import BRIEF_PATH, build_brief, ingest_sources, write_brief
from kb.ingest.report import ingest_report, render_report
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath
from kb.sources.readers import SourceError
from kb.sources.store import add_source, sync_sources
from kb.status import requirement_status, summarize

app = typer.Typer(help="Git-native knowledge base.", no_args_is_help=True)


@app.callback()
def main(
    ctx: typer.Context,
    root: Annotated[
        Path, typer.Option("-C", "--root", help="Repository root.")
    ] = Path("."),
) -> None:
    ctx.obj = {"root": root}


@app.command()
def version() -> None:
    """Print the kb version."""
    typer.echo(kb.__version__)


source_app = typer.Typer(help="Manage product-document sources.", no_args_is_help=True)
app.add_typer(source_app, name="source")


def _run(ctx: typer.Context, action) -> None:
    root = Path(ctx.obj["root"])
    try:
        sources = action(root, load_config(root / "kb.config.yaml"))
    except (ConfigError, SourceError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    repo = KbRepo.load(root)
    for s in sources:
        g = s.guard
        typer.echo(
            f"{s.id}: {len(repo.segments(s.id))} segments, "
            f"guard: {g.emails} emails, {g.secrets} secrets, {g.injections} injections"
        )


@source_app.command("add")
def source_add(ctx: typer.Context, paths: Annotated[list[Path], typer.Argument(help="Documents to add.")]) -> None:
    """Read, mask, segment and store documents under kb/sources/."""

    def add(root: Path, config):
        return [add_source(root, p.as_posix() if not p.is_absolute() else str(p), config) for p in paths]

    _run(ctx, add)


@source_app.command("sync")
def source_sync(ctx: typer.Context) -> None:
    """Re-read every stored source from its original."""
    _run(ctx, sync_sources)


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
            sources = ingest_sources(root, config, rels)
            write_brief(root, build_brief(root, config, [s.id for s in sources]))
    except (ConfigError, SourceError, GitError, UnsafePath) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    if report:
        typer.echo(render_report(result), nl=False)
        if strict and result.failed:
            raise typer.Exit(1)
        return
    typer.echo(f"Brief: {BRIEF_PATH}")
    typer.echo(f"Ask your coding agent: *follow {BRIEF_PATH}*")


module_app = typer.Typer(help="Manage modules; paths come later, from evidence.", no_args_is_help=True)
app.add_typer(module_app, name="module")


def _fail(e: Exception) -> typer.Exit:
    typer.echo(str(e), err=True)
    return typer.Exit(2)


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
    except ConfigError as e:
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
    except (ConfigError, GitError) as e:
        raise _fail(e) from e
    typer.echo(f"{id}: paths = {', '.join(globs)}")


@module_app.command("list")
def module_list(ctx: typer.Context) -> None:
    """List modules and their paths."""
    try:
        config = load_config(Path(ctx.obj["root"]) / "kb.config.yaml")
    except ConfigError as e:
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
        summary = summarize(config, repo, requirement_status(root, config, repo))
    except (ConfigError, GitError) as e:
        raise _fail(e) from e
    if format == "json":
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    for name, c in [*summary["modules"].items(), ("total", summary["total"])]:
        typer.echo(f"{name}: {c['built']}/{c['total']} built, {c['tested']}/{c['total']} tested")


baseline_app = typer.Typer(help="Freeze the agreed requirement set.", no_args_is_help=True)
app.add_typer(baseline_app, name="baseline")


@baseline_app.command("freeze")
def baseline_freeze(ctx: typer.Context, version: Annotated[str, typer.Argument(help="Baseline version, e.g. v1.")]) -> None:
    """Write kb/baseline/VERSION.yaml and tag HEAD (the file is left for you to commit)."""
    try:
        path = freeze_baseline(Path(ctx.obj["root"]), version)
    except (BaselineError, GitError) as e:
        raise _fail(e) from e
    rel = path.relative_to(Path(ctx.obj["root"])).as_posix()
    typer.echo(f"wrote {rel}; tagged HEAD as kb-baseline-{version}; commit the baseline file")
