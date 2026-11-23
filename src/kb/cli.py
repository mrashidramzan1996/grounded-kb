from pathlib import Path
from typing import Annotated

import typer

import kb

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
