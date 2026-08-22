"""`bm board`, and the bare `bm` that renders it — the project at a glance (GAPS U37).

Running plain `bm` in a harness (`! bm` in Claude Code) prints a compact working
view both the human and the agent read in-context: the headline, every live
task, and the two counts that say what else is waiting. It exists because the
equivalent question — "what is open here?" — otherwise costs a flagged `bm ls`
plus a `bm brief` nobody runs mid-session.

Three shape decisions:

- **The board is always pinned.** A "what's open here" view has no meaning as an
  all-projects roll-up. No marker and no `--project` prints the same one-line
  opt-in the session hook uses, exit 0 — an unmarked directory is a fact, not an
  error.
- **The board is not the brief.** No toolbox, no sections, no cap: the brief
  orients a session start; the board answers one question mid-session and stays
  well under the brief's size on any healthy project.
- **`board` is a real (hidden) command.** Bare `bm` routes here from the app
  callback, but the verb also answers to its name so the guards — notice,
  affordance, import — see it the way they see every other verb.
"""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.direct import BoardData, direct_board
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import resolve_read_scope
from basic_memory.project_marker import MarkerError

# Matches records.py: two spaces between aligned columns (contract rule 1).
COLUMN_GAP = 2

# Static affordance (GAPS W19 item 5) — registered with the affordance guard.
BOARD_AFFORDANCE = "bm show <id> read one · bm done <id> close one · bm brief the fuller picture"

# What an unmarked directory gets: the same opt-in line the session hook prints,
# because two spellings of "not tracked" would teach the surface unreliably.
NOT_TRACKED = "not bm-tracked — opt in with 'bm project add <name> --here' from the project root"


def _fail(message: str) -> typer.Exit:
    """One error line on stderr, exit 1, nothing on stdout (contract rule 6)."""
    typer.echo(message, err=True)
    return typer.Exit(1)


def render_board(board: BoardData, headline: Optional[str]) -> list[str]:
    """The board's payload: header, aligned task rows, closing summary line.

    The summary closes the listing the way a count line closes `bm ls`
    (contract rule 3), and an empty board still renders it — `0 open items` is
    a result, not an omission (rule 5).
    """
    shown = f'"{headline}"' if headline is not None else "(none set)"
    lines = [f"board: {board.project} · headline: {shown}"]

    if board.rows:
        cells = [[row.record_id, row.status] for row in board.rows]
        widths = [max(len(cell) for cell in column) for column in zip(*cells)]
        lines.extend(
            "".join(cell.ljust(width + COLUMN_GAP) for cell, width in zip(row_cells, widths))
            + row.title
            for row_cells, row in zip(cells, board.rows)
        )

    count = len(board.rows)
    lines.append(
        f"{count} open item{'' if count == 1 else 's'}"
        f" · shelved {board.shelved} · inbox {board.inbox}"
    )
    return lines


@app.command(name="board", hidden=True)
def board(
    project: Annotated[
        Optional[str],
        typer.Option(
            "--project",
            "-p",
            help="Project to read. Defaults to the .bm.yml above the working directory.",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """What is open here: headline, live tasks, and the parked and inbox counts.

    Bare `bm` prints the same board — the app callback calls this function. It
    is hidden from --help's command list because the bare spelling is the
    surface; the name exists so the board behaves like any other verb — flags,
    notices, guards — when asked for explicitly.
    """
    try:
        scope = resolve_read_scope(project)
    except MarkerError as exc:
        raise _fail(f"Error: {exc}")

    # An unmarked directory is a fact, not an error: say how to opt in, exit 0
    # (the session hook prints the same line, so the surface teaches one way).
    if scope.project is None:
        typer.echo(NOT_TRACKED)
        return

    try:
        data = run_with_cleanup(direct_board(scope.project))
    except ValueError as exc:
        raise _fail(f"Error: {exc}")

    # Deferred with the other leaf imports; a pure file read, no session.
    from basic_memory.services.headline import read_headline

    for line in render_board(data, read_headline(data.external_id)):
        typer.echo(line)

    emit_notices(scope, quiet=quiet, command="board")
    if not quiet:
        typer.echo(BOARD_AFFORDANCE)
