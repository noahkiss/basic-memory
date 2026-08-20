"""`bm rm` — delete records, recoverably (GAPS U27).

Deletion was the missing exit: an inbox record that turned out to be noise had
no status to park it under and no verb to remove it, so triage dead-ended at
"leave it" (the finding that filed U27). `bm rm` closes that loop through the
same mutation/materialization pair the API's delete endpoint runs, and then
commits the deletion into the store history — which is what makes it safe: the
content sits in the parent commit and `bm undo` puts it back.

Relations that pointed at a deleted record go unresolved, and `bm doctor`
reports them. That is honest and deliberate: the link recorded a claim about a
record that no longer exists, and silently dropping the edge would hide that
the claim lost its target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import ReadScope

# Static affordance (GAPS W19 item 5): a deletion's next step is checking what
# moved, and its safety net is the history the commit just extended.
RM_AFFORDANCE = "bm history dirty see what changed · bm undo restore what was just deleted"


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises."""
    typer.echo(message, err=True)
    return typer.Exit(1)


@dataclass(frozen=True, slots=True)
class Deletion:
    """What happened to one id: a payload line's fields, or the error to print."""

    record_id: str
    note_type: str | None = None
    notices: tuple[str, ...] = ()
    error: str | None = None


async def delete_records(project_name: str, record_ids: list[str]) -> list[Deletion]:
    """Delete each record independently: rows, file, and its history commit.

    One id failing must not stop the rest (GAPS U27) — an agent triaging an
    inbox hands over several ids at once, and a typo in one is not a reason to
    keep the other nine. Each outcome is reported per id.
    """
    from basic_memory.cli.commands.record_write import RecordVerbError, _open_record
    from basic_memory.index.local_write_stack import LocalNoteWriteError
    from basic_memory.store.history import dirty_paths
    from basic_memory.store.write_hook import store_relative_path

    outcomes: list[Deletion] = []
    for record_id in record_ids:
        try:
            stack, project, record = await _open_record(project_name, record_id)

            # Trigger: the file the delete removes has uncommitted changes.
            # Why: the history holds only committed content, so that edit would
            #     vanish with nothing to restore — the same edit `bm undo`
            #     refuses to overwrite for the same reason (W3-B).
            # Outcome: refuse this id, naming the command that records the edit.
            store_relative = store_relative_path(project.path, record.file_path)
            if store_relative is not None and any(
                path == store_relative for _, path in dirty_paths()
            ):
                raise RecordVerbError(
                    f"'{record.record_id}' has uncommitted changes the deletion would "
                    "discard. Record them first with 'bm history commit --all', then re-run."
                )

            history = await stack.delete_note(
                project_external_id=project.external_id,
                entity_external_id=record.entity_external_id,
                note_path=record.file_path,
            )
        except (RecordVerbError, LocalNoteWriteError) as exc:
            outcomes.append(Deletion(record_id=record_id, error=str(exc)))
            continue
        outcomes.append(
            Deletion(record_id=record_id, note_type=record.note_type, notices=history.notices)
        )
    return outcomes


@app.command(name="rm")
def rm(
    record_ids: Annotated[
        list[str],
        typer.Argument(help="One or more record ids, e.g. tnd-q8w3e1r5."),
    ],
    project: Annotated[
        Optional[str],
        typer.Option("--project", "-p", help="Project to delete from. Defaults to .bm.yml."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """Delete records. Recoverable: the deletion is committed to the note history.

    Each id is processed independently — an id that does not resolve is one
    error line on stderr, the rest are still deleted, and the exit code is 1
    when any failed.
    """
    from basic_memory.cli.record_notes import write_project_name

    try:
        project_name = write_project_name(project)
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    try:
        outcomes = run_with_cleanup(delete_records(project_name, record_ids))
    except typer.Exit:
        raise
    except Exception as exc:
        raise fail(f"Error: {exc}")

    deleted = [outcome for outcome in outcomes if outcome.error is None]
    failed = [outcome for outcome in outcomes if outcome.error is not None]

    for outcome in failed:
        typer.echo(f"Error: {outcome.error}", err=True)
    for outcome in deleted:
        typer.echo(f"{outcome.record_id}  {outcome.note_type}  deleted")
    typer.echo(f"{len(deleted)} deleted" + (f", {len(failed)} failed" if failed else ""))
    if not quiet:
        for outcome in deleted:
            for line in outcome.notices:
                typer.echo(line)

    emit_notices(ReadScope(project=project_name, origin="write"), quiet=quiet, command="rm")
    if not quiet:
        typer.echo(RM_AFFORDANCE)
    if failed:
        raise typer.Exit(1)
