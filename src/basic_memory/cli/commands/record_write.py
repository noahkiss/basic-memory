"""`bm edit`, `bm mark`, `bm done` — the three verbs that change a record.

They share a module because they share everything that matters: the write-chain
scope, the identity-verified lookup, the local write stack, and the shape of
what they print. What differs is one rule each, and each rule is a decision:

- **`bm edit` accepts only the kept-current types** — `guide`, `profile`,
  `state`, `inbox` (`.forked/schema.md` §4, VERBS_PLAN D12). On a `task` it names
  `bm done`/`bm mark`; on a `finding` it names supersession. A finding is
  provisional by construction, so correcting one in place destroys the evidence
  the record existed to hold.
- **`bm mark` sets `status`, on a `task`, and nothing else** (D5). Status is one
  of exactly four mutable things in the schema; widening `mark` to any other
  field reopens set-once through the back door.
- **`bm done` is `bm mark <id> done`.** Not a second code path — the same one,
  with the status supplied.

**A frontmatter-only change leaves the body byte-identical.** `bm mark` sends an
empty append with a `metadata` block, which `services/note_preparation.py`
recognizes as metadata-only and applies without touching the note's text. A
status change that reflowed the body would show up in the history as a content
edit nobody made.

Every write here goes through `LocalNoteWriteStack`, so the history commit, the
headline refresh, and the vocabulary funnel all happen exactly once, in the
place item A put them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Coroutine, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import ReadScope

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.cli.record_notes import ExistingRecord, WriteProject
    from basic_memory.index.local_write_stack import LocalNoteWriteStack

# Static affordances (GAPS W19 item 5) — a fixed list per verb, never derived
# from what just happened.
EDIT_AFFORDANCE = "bm show <id> read it back · bm history dirty see uncommitted changes"
MARK_AFFORDANCE = "bm ls --status open what is still open · bm new record what you learned"

# The types `bm edit` changes in place. Fixed by the schema, not by a project's
# vocabulary: a type is kept current or it is not, and that is a property of the
# type's temporal shape rather than of any one project's declarations.
KEPT_CURRENT_TYPES: tuple[str, ...] = ("guide", "profile", "state", "inbox")

# The status `bm done` sets. `done` rather than `dropped`: closing work and
# abandoning it are different outcomes, and only the first has a verb.
DONE_STATUS = "done"

# `--body -` reads the new body from stdin, the way `git commit -F -` does.
STDIN_BODY = "-"


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """One line of payload, plus whatever the write has to say afterwards."""

    record_id: str
    note_type: str
    detail: str
    project: str
    notices: tuple[str, ...]


class RecordVerbError(Exception):
    """One of these verbs refused, with the message it prints (contract rule 6)."""


# --- Shared machinery ---


async def _open_record(
    project_name: str, record_id: str
) -> "tuple[LocalNoteWriteStack, WriteProject, ExistingRecord]":
    """Build the write stack and locate one record in the project, by id.

    The stack owns the engine and the session maker; the lookup borrows that
    session maker rather than opening a second one, because the pool holds a
    single connection and a second one deadlocks against it.
    """
    from basic_memory import db
    from basic_memory.cli.record_notes import (
        RecordResolutionError,
        resolve_record,
        resolve_write_project,
    )
    from basic_memory.index.local_write_stack import direct_note_writer

    stack = await direct_note_writer()
    async with db.scoped_session(stack.session_maker) as session:
        project = await resolve_write_project(session, project_name)
        try:
            record = await resolve_record(session, project, record_id)
        except RecordResolutionError as exc:
            raise RecordVerbError(f"no record '{record_id}' in project '{project.name}'") from exc
    return stack, project, record


def _ungoverned_notices(external_id: str) -> tuple[str, ...]:
    """The one-line warning an ungoverned project earns on every write (GAPS W4)."""
    from basic_memory.cli.record_notes import UNGOVERNED_NOTICE
    from basic_memory.vocabulary.model import load_vocabulary

    return () if load_vocabulary(external_id) is not None else (UNGOVERNED_NOTICE,)


def _write(coroutine: Coroutine[Any, Any, WriteOutcome], *, quiet: bool) -> WriteOutcome:
    """Run one verb's write, print its payload and its own notices, and report it.

    The corpus notice is **not** emitted here. `emit_notices` is called by each
    command function instead, which is where `tests/cli/test_notice_guard.py`
    looks for it — and it looks there deliberately: a guard over a shared helper
    proves nothing about whether the verbs reach it (GAPS T22).
    """
    try:
        outcome = run_with_cleanup(coroutine)
    except typer.Exit:
        raise
    except Exception as exc:
        raise fail(f"Error: {exc}")

    typer.echo(f"{outcome.record_id}  {outcome.note_type}  {outcome.detail}")
    typer.echo("1 record")
    if not quiet:
        for line in outcome.notices:
            typer.echo(line)
    return outcome


def _write_scope(outcome: WriteOutcome) -> ReadScope:
    """The scope a write's notice covers: the one project it wrote to."""
    return ReadScope(project=outcome.project, origin="write")


# --- bm edit ---


def _refuse_edit(record: "ExistingRecord") -> None:
    """Refuse an edit to a type that is not kept current, naming what to do instead.

    Both refusals point at a verb rather than at a rule. An agent that reads
    "a finding is immutable" files the correction somewhere else; one that reads
    the exact `bm new --supersedes` line writes the successor.
    """
    if record.note_type in KEPT_CURRENT_TYPES:
        return
    if record.note_type == "task":
        raise RecordVerbError(
            f"'{record.record_id}' is a task, and a task is closed rather than edited — "
            f"use 'bm done {record.record_id}' or 'bm mark {record.record_id} <status>'"
        )
    if record.note_type == "finding":
        raise RecordVerbError(
            f"'{record.record_id}' is a finding, and a finding is never rewritten — "
            f"record what replaced it with "
            f"'bm new finding \"<title>\" --supersedes {record.record_id}'"
        )
    raise RecordVerbError(
        f"'bm edit' changes records that are kept current "
        f"({', '.join(KEPT_CURRENT_TYPES)}); '{record.record_id}' is a {record.note_type}"
    )


def _next_body(current: str, body: Optional[str], *, may_open_editor: bool) -> str:
    """The body an edit writes: the flag, stdin, `$EDITOR`, or what is already there.

    `$EDITOR` opens on the record's current text and only when stdin is a
    terminal — an agent runs this verb with none attached, and an editor launched
    there hangs with no prompt to explain it (VERBS_PLAN D11).

    `may_open_editor` is false when the caller already stated a change: a
    `bm edit <id> --title "…"` asked for a title and nothing else, and opening an
    editor on the body would make the same command mean two different things
    depending on whether a terminal happened to be attached.
    """
    from basic_memory.cli.record_notes import body_from_editor

    if body == STDIN_BODY:
        return sys.stdin.read()
    if body is not None:
        return body
    if not may_open_editor or not sys.stdin.isatty():
        return current
    return body_from_editor(current)


async def edit_record(
    *, project_name: str, record_id: str, title: Optional[str], body: Optional[str]
) -> WriteOutcome:
    """Replace a kept-current record's title and body, keeping everything else."""
    from pathlib import Path

    from basic_memory.cli.record_notes import RecordNote
    from basic_memory.file_utils import remove_frontmatter

    stack, project, record = await _open_record(project_name, record_id)
    _refuse_edit(record)

    on_disk = Path(project.path, record.file_path)
    # Trigger: the record is indexed but its file is not on disk.
    # Why: an edit builds the replacement from the current body, and a missing
    #     file would silently make that body empty — a content deletion reported
    #     as a successful edit (the GAPS T12 shape, one layer up).
    # Outcome: name the path that is missing and refuse.
    if not on_disk.is_file():
        raise RecordVerbError(f"{record.record_id} is indexed but its file is missing: {on_disk}")

    current = remove_frontmatter(on_disk.read_text(encoding="utf-8"))
    result = await stack.update_note(
        project_external_id=project.external_id,
        entity_external_id=record.entity_external_id,
        # The file keeps the path it was created at, even when the title moves.
        # The name carries the id that other records link by; the slug beside it
        # is a human label, and renaming the file to chase a title would be a
        # move, with a permalink rewrite behind it (`.forked/schema.md` §8).
        data=RecordNote(
            title=title if title is not None else record.title,
            note_type=record.note_type,
            directory=Path(record.file_path).parent.as_posix(),
            record_file_path=record.file_path,
            content=_next_body(current, body, may_open_editor=title is None),
        ),
    )
    return WriteOutcome(
        record_id=record.record_id,
        note_type=record.note_type,
        detail=f"{project.path}/{result.file_path}",
        project=project.name,
        notices=(*result.notices, *_ungoverned_notices(project.external_id)),
    )


@app.command(name="edit")
def edit(
    record_id: Annotated[str, typer.Argument(help="The record's id, e.g. tnd-q8w3e1r5.")],
    title: Annotated[
        Optional[str],
        typer.Option("--title", help="Replace the record's title."),
    ] = None,
    body: Annotated[
        Optional[str],
        typer.Option(
            "--body",
            "-b",
            help=(
                "Replace the body. Use '-' to read it from stdin; omit both "
                "--title and --body to open $EDITOR."
            ),
        ),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option("--project", "-p", help="Project to write to. Defaults to .bm.yml."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """Change a record that is kept current: a guide, profile, state, or inbox note.

    Only the title and the body move. Every field set at creation stays set —
    a task is closed with `bm done`, and a finding is replaced by a successor
    written with `bm new --supersedes`.
    """
    from basic_memory.cli.record_notes import write_project_name

    try:
        project_name = write_project_name(project)
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    # Trigger: neither --title nor --body, and no terminal to open $EDITOR on.
    # Why: the edit would rewrite the record with exactly what it already says,
    #     which is a no-op write the caller did not ask for and cannot see.
    # Outcome: an addressing failure naming both ways to state the change.
    if title is None and body is None and not sys.stdin.isatty():
        raise fail("Error: nothing to change — pass --title or --body")

    outcome = _write(
        edit_record(project_name=project_name, record_id=record_id, title=title, body=body),
        quiet=quiet,
    )
    emit_notices(_write_scope(outcome), quiet=quiet, command="edit")
    if not quiet:
        typer.echo(EDIT_AFFORDANCE)


# --- bm mark and bm done ---


async def mark_record(*, project_name: str, record_id: str, status: str) -> WriteOutcome:
    """Set one task's status, leaving its body byte-identical."""
    from basic_memory.schemas.request import EditEntityRequest
    from basic_memory.vocabulary.model import load_vocabulary

    stack, project, record = await _open_record(project_name, record_id)

    if record.note_type != "task":
        raise RecordVerbError(
            f"only a task carries a status; '{record.record_id}' is a {record.note_type}"
        )

    # An ungoverned project declares no statuses, so there is nothing to check
    # against and the write goes through unchecked — an absent vocabulary means
    # ungoverned, never "use the defaults" (GAPS W4).
    vocabulary = load_vocabulary(project.external_id)
    if vocabulary is not None and status not in vocabulary.statuses:
        raise RecordVerbError(
            f"'{status}' is not a status this project declares. "
            f"Allowed values: {', '.join(vocabulary.statuses)}."
        )

    result = await stack.edit_note(
        project_external_id=project.external_id,
        entity_external_id=record.entity_external_id,
        # An empty append plus a metadata block is the frontmatter-only shape
        # `prepare_edit_entity_content` recognizes: it skips the content
        # operation outright, so the body is not reflowed by a status change.
        data=EditEntityRequest(operation="append", content="", metadata={"status": status}),
    )
    return WriteOutcome(
        record_id=record.record_id,
        note_type=record.note_type,
        detail=status,
        project=project.name,
        notices=(*result.notices, *_ungoverned_notices(project.external_id)),
    )


@app.command(name="mark")
def mark(
    record_id: Annotated[str, typer.Argument(help="The task's id, e.g. tnd-7k2m9x4p.")],
    status: Annotated[str, typer.Argument(help="One of the statuses this project declares.")],
    project: Annotated[
        Optional[str],
        typer.Option("--project", "-p", help="Project to write to. Defaults to .bm.yml."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """Set a task's status — open, doing, blocked, done, or dropped.

    A task's status is the only field any verb changes after creation. Nothing
    else about the record moves, and no other type has one.
    """
    from basic_memory.cli.record_notes import write_project_name

    try:
        project_name = write_project_name(project)
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    outcome = _write(
        mark_record(project_name=project_name, record_id=record_id, status=status), quiet=quiet
    )
    emit_notices(_write_scope(outcome), quiet=quiet, command="mark")
    if not quiet:
        typer.echo(MARK_AFFORDANCE)


@app.command(name="done")
def done(
    record_id: Annotated[str, typer.Argument(help="The task's id, e.g. tnd-7k2m9x4p.")],
    project: Annotated[
        Optional[str],
        typer.Option("--project", "-p", help="Project to write to. Defaults to .bm.yml."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """Close a task: exactly `bm mark <id> done`.

    It exists because closing work is the one status change an agent makes
    while it is already there finishing the work — the moment pruning is free.
    """
    from basic_memory.cli.record_notes import write_project_name

    try:
        project_name = write_project_name(project)
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    outcome = _write(
        mark_record(project_name=project_name, record_id=record_id, status=DONE_STATUS), quiet=quiet
    )
    emit_notices(_write_scope(outcome), quiet=quiet, command="done")
    if not quiet:
        typer.echo(MARK_AFFORDANCE)
