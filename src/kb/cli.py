from pathlib import Path
from typing import Annotated

import typer

import kb
from kb.config.load import ConfigError, load_config
from kb.gitfiles import GitError
from kb.ingest.brief import BRIEF_PATH, build_brief, ingest_sources, write_brief
from kb.ingest.report import ingest_report, render_report
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath
from kb.sources.readers import SourceError
from kb.sources.store import add_source, sync_sources

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
    strict: Annotated[bool, typer.Option("--strict", help="With --report: exit 1 when anything was rejected.")] = False,
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
        if strict and result.rejected:
            raise typer.Exit(1)
        return
    typer.echo(f"Brief: {BRIEF_PATH}")
    typer.echo(f"Ask your coding agent: *follow {BRIEF_PATH}*")
