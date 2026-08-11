"""Command module for the note store's local git history.

Imports stay narrow on purpose: this verb reads a git repository, so nothing
here may pull the database, API, or MCP import graph onto its path.
"""

from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.store.history import HistoryError, dirty_paths, sweep_commit

history_app = typer.Typer(help="Inspect and commit the note store's local history")
app.add_typer(history_app, name="history")


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


@history_app.command()
def dirty() -> None:
    """List note-store files with uncommitted changes."""
    try:
        entries = dirty_paths()
    except HistoryError as exc:
        raise fail(f"Error: {exc}")

    # Path leads the row: it is the identifier a caller acts on (contract rule 2).
    path_width = max((len(path) for _, path in entries), default=0)
    for status, path in entries:
        typer.echo(f"{path:<{path_width}}  {status}")
    typer.echo(f"{len(entries)} dirty files")


@history_app.command()
def commit(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="Store-relative paths to commit."),
    ] = None,
    commit_all: Annotated[
        bool,
        typer.Option("--all", help="Commit every dirty file as one commit."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the status lines and next-step hints."),
    ] = False,
) -> None:
    """Commit note changes that bm did not make itself.

    The sweep is its own command rather than a flag on a write verb: a flag
    would weld unrelated changes into one commit, and undoing the tool's work
    would then undo somebody else's.
    """
    if commit_all and paths:
        raise fail("Error: pass either paths or --all, not both.")
    if not commit_all and not paths:
        raise fail("Error: pass one or more paths, or --all.")

    scope = "--all" if commit_all else " ".join(paths or ())
    try:
        result = sweep_commit(f"bm history commit {scope}", None if commit_all else paths)
    except HistoryError as exc:
        raise fail(f"Error: {exc}")

    # An empty sweep is a result, not a failure (contract rule 5).
    if result is None:
        typer.echo("nothing to commit")
        return

    typer.echo(f"sha: {result.sha}")
    typer.echo(f"paths: {len(result.paths)} committed")

    if result.dirty_others and not quiet:
        typer.echo(
            f"note: {len(result.dirty_others)} other files have uncommitted changes "
            "(not included in this commit)"
        )
        typer.echo("run 'bm history dirty' to review")
