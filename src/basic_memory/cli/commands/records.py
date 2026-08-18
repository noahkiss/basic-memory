"""`bm ls`, `bm show`, `bm path` — the three read verbs over records.

Read-only, and deliberately the cheapest verbs in the set: they resolve a scope,
run one indexed query, and print. Nothing here may pull the API, the MCP tool
layer, fastapi, or dateparser onto its path (AGENTS.md, "Measured baseline").

The reads themselves live in `cli/direct.py` — `direct_record_listing` and
`direct_record` — with every other native verb's direct-path wiring. This module
is scope, render, and exit shape.

Three decisions are load-bearing:

- **Scope follows GAPS W5-C** (`--project` > nearest `.bm.yml` > every project).
  An unscoped `bm ls` is a roll-up and says which project each row came from,
  because a list that mixes projects without naming them is not actionable.
- **Identity is verified, never inferred** (GAPS T9/T10, `.forked/schema.md` §8).
  After resolving an id, the permalink that came back must equal the id asked
  for. A title that happens to match is not-found. Enforced in `direct_record`.
- **`bm path` prints the path and nothing else** (VERBS_PLAN D9). It exists for
  `$EDITOR "$(bm path tnd-x)"`, and a count line, a notice, or an affordance
  would land inside that command substitution. See `docs/OUTPUT_CONTRACT.md`.
"""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.direct import (
    AmbiguousRecord,
    RecordNotFound,
    RecordRow,
    direct_record,
    direct_record_listing,
)
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import resolve_read_scope
from basic_memory.project_marker import MarkerError

# Two spaces between columns, so values line up without box drawing
# (output contract rule 1).
COLUMN_GAP = 2

# Static affordances (GAPS W19 item 5). Static is the requirement, not a
# shortcut: W19's correction rules out conditional hints and per-session memory,
# because a hint that appears only sometimes teaches the surface unreliably.
LS_AFFORDANCE = "bm show <id> read the full entry · bm new record something worth finding again"
SHOW_AFFORDANCE = "bm edit <id> change it · bm path <id> print its file path"


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


# --- Render ---


def render_rows(rows: list[RecordRow], *, name_projects: bool) -> list[str]:
    """One record per line, identifier first, aligned (contract rules 1 and 2).

    ``name_projects`` adds the project column an unscoped roll-up needs. It sits
    after the id so the identifier stays in the first column whichever shape the
    listing takes.
    """
    if not rows:
        return []

    def leading(row: RecordRow) -> list[str]:
        if name_projects:
            return [row.record_id, row.project, row.note_type, row.status]
        return [row.record_id, row.note_type, row.status]

    columns = [leading(row) for row in rows]
    widths = [max(len(cell) for cell in column) for column in zip(*columns)]
    return [
        "".join(cell.ljust(width + COLUMN_GAP) for cell, width in zip(cells, widths)) + row.title
        for cells, row in zip(columns, rows)
    ]


# --- Verbs ---


@app.command(name="ls")
def ls(
    note_type: Annotated[
        Optional[str],
        typer.Option("--type", "-t", help="Only records of this type, e.g. task or finding."),
    ] = None,
    status: Annotated[
        Optional[str],
        typer.Option("--status", "-s", help="Only records with this status, e.g. open."),
    ] = None,
    area: Annotated[
        Optional[str],
        typer.Option("--area", "-a", help="Only records in this area."),
    ] = None,
    limit: Annotated[
        Optional[int],
        typer.Option("--limit", "-n", help="Print at most this many records."),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(
            "--project",
            "-p",
            help="Project to read. Defaults to .bm.yml, then every project.",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """List the records in a project: id, type, status, and title, one per line.

    Reports every project unless `--project` or a `.bm.yml` above the working
    directory pins one; an unscoped listing names the project each row came from.
    A note without a record type is not a record and is not listed.

    A record some other record supersedes reads `superseded` in the status
    column. `bm show <id>` names the successor.
    """
    # Trigger: --limit 0 or a negative limit.
    # Why: a listing cannot be scoped to fewer than one row, so this is an
    #     invalid flag — an addressing failure, not an empty result (rule 5).
    # Outcome: one error line, exit 1.
    if limit is not None and limit < 1:
        raise fail(f"Error: --limit must be 1 or more, got {limit}")

    try:
        scope = resolve_read_scope(project)
    except MarkerError as exc:
        raise fail(f"Error: {exc}")

    try:
        listing = run_with_cleanup(
            direct_record_listing(
                scope.project, note_type=note_type, status=status, area=area, limit=limit
            )
        )
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    for line in render_rows(listing.rows, name_projects=not scope.is_pinned):
        typer.echo(line)

    # Contract rule 3: the count closes the listing, on its own line. Rule 5: an
    # empty result is a result — `0 records`, exit 0. The singular matches what
    # `bm new` already prints for one write (GAPS U13).
    count = len(listing.rows)
    typer.echo(f"{count} record{'' if count == 1 else 's'}")

    if listing.truncated and not quiet:
        typer.echo(f"more records match — raise --limit above {limit}")
    emit_notices(scope, quiet=quiet, command="ls")
    if not quiet:
        typer.echo(LS_AFFORDANCE)


@app.command(name="show")
def show(
    record_id: Annotated[str, typer.Argument(help="The record's id, e.g. tnd-q8w3e1r5.")],
    project: Annotated[
        Optional[str],
        typer.Option(
            "--project",
            "-p",
            help="Project to read. Defaults to .bm.yml, then every project.",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """Print a record's file, exactly as it is on disk.

    The file's bytes are the payload and nothing is added to them (contract:
    raw content is byte-exact). Anything derived — a successor that supersedes
    this record — follows as a notice, after the payload, and `--quiet` drops it.

    The one thing printed that is not in the file is a final newline, and only
    when the file lacks one: without it the body's last word and the first notice
    read as one token (GAPS U2).
    """
    try:
        scope = resolve_read_scope(project)
    except MarkerError as exc:
        raise fail(f"Error: {exc}")

    try:
        record = run_with_cleanup(direct_record(scope.project, record_id))
    except ValueError as exc:
        raise fail(f"Error: {exc}")
    except RecordNotFound:
        raise fail(f"Error: no record '{record_id}' in scope")
    except AmbiguousRecord as exc:
        raise fail(f"Error: '{record_id}' is in more than one project ({exc}) — name one with -p")

    # Trigger: the record is indexed but its file is not on disk.
    # Why: a note can exist in the database with nothing materialized (GAPS T12),
    #     and printing an empty payload would report that as an empty note.
    # Outcome: name the path that is missing and exit 1.
    if not record.path.is_file():
        raise fail(f"Error: {record.record_id} is indexed but its file is missing: {record.path}")

    # Bytes, not text: reading through the text layer translates CRLF to LF and
    # raises on any file that is not valid UTF-8, and both break the contract's
    # "raw content is byte-exact" rule — the second one as a traceback rather
    # than the single stderr line rule 6 requires. `click.echo` writes bytes to
    # the binary stream underneath stdout.
    payload = record.path.read_bytes()
    typer.echo(payload, nl=False)

    # Trigger: the file's bytes do not end in a newline.
    # Why: the payload is byte-exact, so the separator cannot be written into the
    #     file's own bytes here — and without one the body's last word runs into
    #     the notice below it, which reads as a single token to any reader
    #     (GAPS U2). Files written since U2's writer fix already end in one.
    # Outcome: one newline, printed after the payload rather than inside it.
    if payload and not payload.endswith(b"\n"):
        typer.echo("")

    if not quiet:
        for supersession in record.superseded_by:
            typer.echo(supersession.describe())
    emit_notices(scope, quiet=quiet, command="show")
    if not quiet:
        typer.echo(SHOW_AFFORDANCE)


@app.command(name="path")
def path(
    record_id: Annotated[str, typer.Argument(help="The record's id, e.g. tnd-q8w3e1r5.")],
    project: Annotated[
        Optional[str],
        typer.Option(
            "--project",
            "-p",
            help="Project to read. Defaults to .bm.yml, then every project.",
        ),
    ] = None,
) -> None:
    """Print the absolute path of a record's file, and nothing else.

    Built for `$EDITOR "$(bm path tnd-q8w3e1r5)"`, which is why this verb prints
    no count line, no notices, and no hints: every one of them would land inside
    the command substitution (VERBS_PLAN D9, `docs/OUTPUT_CONTRACT.md`).
    """
    try:
        scope = resolve_read_scope(project)
    except MarkerError as exc:
        raise fail(f"Error: {exc}")

    try:
        record = run_with_cleanup(direct_record(scope.project, record_id))
    except ValueError as exc:
        raise fail(f"Error: {exc}")
    except RecordNotFound:
        raise fail(f"Error: no record '{record_id}' in scope")
    except AmbiguousRecord as exc:
        raise fail(f"Error: '{record_id}' is in more than one project ({exc}) — name one with -p")

    typer.echo(record.path)

    # Trigger: the row resolved but its file is gone (GAPS U10).
    # Why: `bm show` calls this an error and exits 1, and this verb stayed silent
    #     — so the same condition read as fine here. Refusing would be wrong
    #     though: the path is exactly what you want in order to restore the file,
    #     and the verb exists to be substituted into another command.
    # Outcome: the path still goes to stdout at exit 0, so `$EDITOR "$(bm path
    #     …)"` is unchanged; the warning goes to stderr, where a substitution
    #     does not capture it but a human still sees it.
    if not record.path.exists():
        typer.echo(
            f"note: {record.record_id} is indexed but its file is missing — restore it, "
            f"or run 'bm reindex -p {record.project}' to drop the row",
            err=True,
        )
