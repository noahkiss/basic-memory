"""`bm new` — write one record (verbs item E).

The verb that creates everything else in the system, so three of its decisions
are load-bearing and none of them may be relaxed later:

- **`permalink` equals `id`, byte-for-byte** (`.forked/schema.md` §2). Edges bind
  to the permalink, so an id that is not also the permalink makes `[[tnd-…]]`
  land as a dangling relation and the record unreachable by its own name.
- **`review-by` is not written here.** `prepare_accepted_note_create` stamps it
  from the project's `review_months` (GAPS W5 item 1). Stamping it twice would
  validate one value and store another.
- **An unknown `--type` files an `inbox` record**, carrying `proposed-type`
  (GAPS W4). Agents propose a type; only a human enables one. Rejecting the
  write instead would send the content nowhere, which is the drop the escape
  hatch exists to prevent. The one exception is a project whose vocabulary
  declares no `inbox` type at all: there is nowhere to file the proposal, so the
  verb refuses and says so rather than failing in the checker (GAPS E2).

Scope is the **write** chain — `--project`, then the nearest `.bm.yml`, then the
default project (`project_marker.resolve_cli_project`). Reads go unscoped when
nothing pins them; a write cannot, because it needs a home.

No interactive prompts. Agents run this verb, and a prompt to an agent is a
hang. `$EDITOR` opens only when there is a terminal to open it on (VERBS_PLAN
D11).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import ReadScope
from basic_memory.vocabulary.glossary import PICKING_QUESTIONS

# Static affordance (GAPS W19 item 5). Static is the requirement, not a
# shortcut: a hint that appears only sometimes teaches the surface unreliably.
NEW_AFFORDANCE = "bm show <id> read it back · bm ls list what is here · bm done <id> close a task"

# `--body -` reads the note's body from stdin, the way `git commit -F -` does.
STDIN_BODY = "-"

# The status every task opens at. `bm mark` is what moves it; nothing else does
# (`.forked/schema.md` §4 — status is one of the four mutable things).
INITIAL_TASK_STATUS = "open"

# Where a `bm new` date came from, and how precisely it is known. `inline`
# because the writer stated it at the prompt rather than a tool deriving it, and
# `day` because that is the granularity a calendar date carries. Not `inferred`:
# that rung means nobody stated the date, and `bm doctor` reports every inferred
# date for human review — stamping it here would put the whole corpus in that
# pile on day one.
DATE_SOURCE = "inline"
DATE_CONFIDENCE = "day"

# The one date field each type carries, keyed by type (`.forked/schema.md` §2).
# `profile.since` is deliberately absent: it is optional, and a `since` this verb
# invented would claim a start date the writer never gave. `guide`, `state` and
# `inbox` have no date field at all — there is physically nowhere to put one.
_TYPE_DATE_FIELD = {"task": "opened", "finding": "event-date"}


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


def type_help() -> str:
    """The `--type` argument's help, naming every type with its picking question.

    Built from the shared glossary so the write path, the rejection message and
    `bm types` teach one vocabulary (GAPS W19's acceptance condition).
    """
    choices = ", ".join(f"{name} ({question})" for name, question in PICKING_QUESTIONS.items())
    return f"What kind of record this is: {choices}."


@dataclass(frozen=True, slots=True)
class NewRecordOutcome:
    """What `bm new` wrote, and what it has to say about it afterwards."""

    record_id: str
    note_type: str
    path: str
    project: str
    notices: tuple[str, ...]


def read_body(body: Optional[str]) -> str:
    """Resolve the note's body: the flag, stdin, `$EDITOR`, or nothing (D11).

    `$EDITOR` opens only when stdin is a terminal. An agent runs this verb with
    no terminal attached, and an editor launched there is a hang with no prompt
    to explain it — so the same invocation writes an empty body instead, which
    `bm edit` can fill in.
    """
    from basic_memory.cli.record_notes import body_from_editor

    if body == STDIN_BODY:
        return sys.stdin.read()
    if body is not None:
        return body
    if not sys.stdin.isatty():
        return ""
    return body_from_editor("")


def build_frontmatter(
    *,
    record_id: str,
    source: str,
    note_type: str,
    proposed_type: Optional[str],
    area: Optional[str],
    today: date,
) -> dict[str, str]:
    """The frontmatter block a new record carries, in a fixed key order.

    Fixed order is a GAPS W3 requirement: the history compares files byte for
    byte, and a key order that varies makes every write a diff to read past.
    """
    fields: dict[str, str] = {"id": record_id, "permalink": record_id, "source": source}

    date_field = _TYPE_DATE_FIELD.get(note_type)
    if date_field is not None:
        # The provenance triple travels with the date and never without it: a
        # date whose origin is unrecorded cannot be re-opened (§2), and the
        # checker rejects either half alone.
        fields[date_field] = today.isoformat()
        fields["date-source"] = DATE_SOURCE
        fields["date-confidence"] = DATE_CONFIDENCE

    if area is not None:
        fields["area"] = area
    if note_type == "task":
        fields["status"] = INITIAL_TASK_STATUS
    if proposed_type is not None:
        fields["proposed-type"] = proposed_type
    return fields


async def create_record(
    *,
    project_name: str,
    requested_type: str,
    title: str,
    body: str,
    source: str,
    area: Optional[str],
    supersedes: Optional[str],
) -> NewRecordOutcome:
    """Allocate an id, render the record, and write it through the local stack.

    One database bootstrap for the whole verb: the write stack owns the engine
    and the session maker, and the id allocation borrows the same session maker
    rather than opening a second one (the pool holds one connection).
    """
    # Deferred: the write stack and the record schema pull SQLAlchemy and
    # Pydantic, which must not load at CLI import time (AGENTS.md, baseline).
    from basic_memory import db
    from basic_memory.cli.record_notes import (
        UNGOVERNED_NOTICE,
        RecordNote,
        allocate_record_id,
        record_directory,
        record_exists,
        record_markdown,
        record_path,
        resolve_note_type,
        resolve_write_project,
    )
    from basic_memory.index.local_write_stack import direct_note_writer
    from basic_memory.vocabulary.model import load_vocabulary

    stack = await direct_note_writer()
    async with db.scoped_session(stack.session_maker) as session:
        project = await resolve_write_project(session, project_name)

        # Trigger: --supersedes names a well-formed id no record in this project holds.
        # Why: the funnel's supersession rule judges the *type* of the record being
        #     written, not whether the target exists, so a typo writes
        #     `- supersedes [[tnd-aaaa1111]]` and exits 0. The edge then reads as a
        #     real chain until `bm doctor` reports it as dangling — the author is
        #     told at the wrong moment, about a mistake only they can still
        #     remember making (GAPS E1).
        # Outcome: refuse before writing. A successor to a record that does not
        #     exist is a typo every time.
        if supersedes is not None and not await record_exists(
            session, project.project_id, supersedes
        ):
            raise ValueError(
                f"--supersedes names '{supersedes}', which is not a record in "
                f"project '{project.name}'"
            )

        # Resolved before an id is drawn: a type this project cannot file is a
        # refusal, and spending an id on a write that never happens leaves a gap
        # in the sequence for no reason (GAPS E2).
        vocabulary = load_vocabulary(project.external_id)
        note_type, proposed_type = resolve_note_type(
            requested_type, vocabulary, project=project.name
        )

        record_id = await allocate_record_id(session, project.project_id)

    file_path = record_path(note_type, record_id, title)

    content = record_markdown(
        build_frontmatter(
            record_id=record_id,
            source=source,
            note_type=note_type,
            proposed_type=proposed_type,
            area=area,
            today=date.today(),
        ),
        body,
        supersedes=supersedes,
    )
    result = await stack.write_note(
        project_external_id=project.external_id,
        data=RecordNote(
            title=title,
            note_type=note_type,
            directory=record_directory(note_type),
            record_file_path=file_path,
            content=content,
        ),
    )

    notices = list(result.notices)
    if proposed_type is not None:
        notices.append(
            f"note: '{proposed_type}' is not a type this project declares — filed as "
            f"inbox proposing it; run 'bm types' to see the set"
        )
    if vocabulary is None:
        notices.append(UNGOVERNED_NOTICE)

    return NewRecordOutcome(
        record_id=record_id,
        note_type=note_type,
        path=f"{project.path}/{result.file_path}",
        project=project.name,
        notices=tuple(notices),
    )


@app.command(name="new")
def new(
    note_type: Annotated[str, typer.Argument(metavar="TYPE", help=type_help())],
    title: Annotated[str, typer.Argument(help="What the record is about, in a few words.")],
    body: Annotated[
        Optional[str],
        typer.Option(
            "--body",
            "-b",
            help="The note's body. Use '-' to read it from stdin; omit it to open $EDITOR.",
        ),
    ] = None,
    source: Annotated[
        Optional[str],
        typer.Option("--source", help="Where the content came from, e.g. NOTES.md#L4,L8-L10."),
    ] = None,
    area: Annotated[
        Optional[str],
        typer.Option("--area", "-a", help="One of the areas this project declares."),
    ] = None,
    supersedes: Annotated[
        Optional[str],
        typer.Option("--supersedes", help="The finding this one replaces, by id."),
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
    """Write one record: a task, a guide, a finding, a profile, a state, an inbox note.

    The record gets an id, and its permalink is that id — that is what makes
    `[[tnd-…]]` resolve to it. A type this project does not declare is not an
    error: the record is filed as `inbox` proposing that type, for a human to
    promote.
    """
    from basic_memory.cli.record_notes import DEFAULT_SOURCE, write_project_name
    from basic_memory.vocabulary.ids import is_record_id

    # Trigger: --supersedes given a value that is not a record id.
    # Why: the edge is written as `[[<value>]]`, which resolves by permalink; a
    #     value that cannot be one lands as a dangling relation that reads as a
    #     real edge until `bm doctor` reports it.
    # Outcome: refuse before writing, naming the shape expected.
    if supersedes is not None and not is_record_id(supersedes):
        raise fail(f"Error: --supersedes takes a record id, got '{supersedes}'")

    # An unusable marker and an empty registry both arrive as ValueError, and
    # both are addressing failures — the request names no project that exists.
    try:
        project_name = write_project_name(project)
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    try:
        outcome = run_with_cleanup(
            create_record(
                project_name=project_name,
                requested_type=note_type,
                title=title,
                body=read_body(body),
                source=source if source is not None else DEFAULT_SOURCE,
                area=area,
                supersedes=supersedes,
            )
        )
    except typer.Exit:
        raise
    except Exception as exc:
        raise fail(f"Error: {exc}")

    typer.echo(f"{outcome.record_id}  {outcome.note_type}  {outcome.path}")
    typer.echo("1 record")

    if not quiet:
        for line in outcome.notices:
            typer.echo(line)
    emit_notices(ReadScope(project=outcome.project, origin="write"), quiet=quiet, command="new")
    if not quiet:
        typer.echo(NEW_AFFORDANCE)
