"""`bm edit`, `bm mark`, `bm done` — the three verbs that change a record.

They share a module because they share everything that matters: the write-chain
scope, the identity-verified lookup, the local write stack, and the shape of
what they print. What differs is one rule each, and each rule is a decision:

- **`bm edit` changes *content* only on the kept-current types** — `guide`,
  `profile`, `state`, `inbox` (`.forked/schema.md` §4, VERBS_PLAN D12). On a
  `task` it names `bm done`/`bm mark`; on a `finding` it names supersession. A
  finding is provisional by construction, so correcting one in place destroys the
  evidence the record existed to hold. A **relations-only** run — `--rel` with no
  `--title`, `--body` or `--set` — is exempt and accepted on every type (GAPS
  U18): an edge is a link, not a claim the record makes, and the pair worth
  linking is usually spotted after both records are written.
- **`--set name=value` writes a declared field, and only on a `profile`**
  (`.forked/schema.md` §4 item 4, GAPS V-J1). A profile is the one type that
  accretes facts, and its project's declared fields are where they land. Every
  set-once field is refused by name, so the flag cannot become a way back into
  the fields `bm new` owns.
- **`--rel <type>:<id>` adds an edge and removes none** (GAPS U14). The edge lands
  in the body's `## Relations` section, which is where `bm new` writes one and
  where the markdown parser reads one; the relation type is the project's
  vocabulary to declare, and the target must be a record the project holds.
  Nothing else removes an edge either: `--body` replaces the prose and carries
  that section across (GAPS U17), because a body edit says nothing about the
  links. `$EDITOR` is the one exception, and deliberately — it opens on the whole
  body, relations included, so what the user saved is what they meant.
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
from collections.abc import Mapping, Sequence
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

# The one type whose declared fields are mutable (`.forked/schema.md` §1 table and
# §4 item 4). A profile accretes facts about a subject; on every other type the
# frontmatter is what `bm new` wrote and nothing else (GAPS V-J1).
FIELD_BEARING_TYPE = "profile"

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
    # What the verb changed: `bm edit` puts the record's project-relative path
    # here (GAPS U11), `bm mark` and `bm done` put the new status.
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
    """Refuse a *content* edit to a type that is not kept current, naming what to do instead.

    Only reached for an edit that changes what the record says — `--title`,
    `--body`, `--set`, or an `$EDITOR` session. A relations-only `--rel` run
    never gets here: it is allowed on every type (GAPS U18), because it adds an
    edge and rewrites no evidence.

    All three refusals point at a verb rather than at a rule. An agent that reads
    "a finding is immutable" files the correction somewhere else; one that reads
    the exact `bm new --supersedes` line writes the successor. Each also names
    `--rel`, because the reader has just been told this record cannot be edited
    and the one edit it *can* take is the one they most often want next.
    """
    if record.note_type in KEPT_CURRENT_TYPES:
        return
    if record.note_type == "task":
        raise RecordVerbError(
            f"'{record.record_id}' is a task, and a task is closed rather than edited — "
            f"use 'bm done {record.record_id}' or 'bm mark {record.record_id} <status>'; "
            f"'bm edit {record.record_id} --rel <type>:<id>' on its own adds a link"
        )
    if record.note_type == "finding":
        raise RecordVerbError(
            f"'{record.record_id}' is a finding, and a finding is never rewritten — "
            f"record what replaced it with "
            f"'bm new finding \"<title>\" --supersedes {record.record_id}'; "
            f"'bm edit {record.record_id} --rel <type>:<id>' on its own adds a link"
        )
    raise RecordVerbError(
        f"'bm edit' changes the content of records that are kept current "
        f"({', '.join(KEPT_CURRENT_TYPES)}); '{record.record_id}' is a {record.note_type} — "
        f"'bm edit {record.record_id} --rel <type>:<id>' on its own adds a link"
    )


def parse_field_assignments(assignments: Sequence[str]) -> dict[str, str]:
    """Turn `--set name=value` arguments into the frontmatter they write.

    Later assignments to the same name win, the way a repeated flag reads. The
    value is taken verbatim after the first `=`, so a date or a URL survives.

    Trigger: an argument with no `=`, no name, or no value.
    Why: `--set since` and `--set owner=` both look like a change and are not
        one. An empty value cannot clear the field either — the update path
        merges keys and never drops them, so it would write `owner: ''` and
        leave a key the checker reads as absent.
    Outcome: refuse, showing the shape expected.
    """
    fields: dict[str, str] = {}
    for assignment in assignments:
        name, separator, value = assignment.partition("=")
        if not separator or not name.strip() or not value.strip():
            raise RecordVerbError(f"--set takes 'name=value', got '{assignment}'")
        fields[name.strip()] = value.strip()
    return fields


def _refuse_field_updates(
    record: "ExistingRecord", project: "WriteProject", fields: Mapping[str, str]
) -> None:
    """Refuse a `--set` the schema or the project's vocabulary does not allow.

    Four refusals, each naming what to do instead. The *values* are not judged
    here: the accepted write path runs `check_frontmatter`, which owns the date
    and enum rules, and a second copy of them here would be a second answer to
    the same question (GAPS V-J1).
    """
    from basic_memory.vocabulary.checker import SET_ONCE_FIELDS
    from basic_memory.vocabulary.model import load_vocabulary

    if record.note_type != FIELD_BEARING_TYPE:
        raise RecordVerbError(
            f"only a {FIELD_BEARING_TYPE} carries declared fields; "
            f"'{record.record_id}' is a {record.note_type}"
        )

    # An absent vocabulary means ungoverned, never "use the defaults" (GAPS W4),
    # and an ungoverned project declares no fields — so there is no such thing as
    # a declared field to set, and inventing one here would write a key nothing
    # in the project ever validates.
    vocabulary = load_vocabulary(project.external_id)
    if vocabulary is None:
        raise RecordVerbError(
            f"project '{project.name}' declares no vocabulary, so it declares no fields — "
            f"run 'bm types' to see what a governed project declares"
        )

    for name in fields:
        if name in SET_ONCE_FIELDS:
            raise RecordVerbError(
                f"'{name}' is set once and cannot change. Set-once fields are written once, "
                f"by 'bm new', and never edited."
            )
        if name not in vocabulary.fields:
            declared = ", ".join(sorted(vocabulary.fields)) or "none"
            raise RecordVerbError(
                f"'{name}' is not a field project '{project.name}' declares "
                f"(declared: {declared}) — run 'bm types', and declare it in "
                f"vocabulary.yml if it belongs there"
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
    *,
    project_name: str,
    record_id: str,
    title: Optional[str],
    body: Optional[str],
    fields: Sequence[str] = (),
    relations: Sequence[tuple[str, str]] = (),
) -> WriteOutcome:
    """Replace a kept-current record's title, body and declared fields, or add an edge.

    Only a `profile` has declared fields, and `fields` is the only frontmatter
    this verb writes — every set-once field stays as `bm new` wrote it.

    A record's edges are facts somebody recorded, so nothing here removes one:

    - `--rel` *appends* to the body's `## Relations` section rather than replacing
      it (GAPS U14), and a relations-only run is accepted on every type, including
      a task and a finding (GAPS U18);
    - `--body` replaces the prose and carries that section over (GAPS U17).
    """
    from pathlib import Path

    from basic_memory import db
    from basic_memory.cli.record_notes import (
        RecordNote,
        append_relations,
        carry_relations,
        check_relation_types,
        record_exists,
    )
    from basic_memory.file_utils import remove_frontmatter
    from basic_memory.vocabulary.model import load_vocabulary

    stack, project, record = await _open_record(project_name, record_id)

    # Trigger: `--rel` and nothing else — no `--title`, no `--body`, no `--set`.
    # Why: the type refusal exists because a task is closed rather than revised
    #     and a finding is evidence rather than a draft (D12). An edge states
    #     neither: it adds a link and rewrites nothing the record claims, and
    #     provenance is usually noticed on the read-back, after both records are
    #     written (GAPS U18). `fields` is read raw rather than parsed, so a
    #     malformed `--set` still reaches the type refusal it used to.
    # Outcome: a relations-only edit is allowed on every type; every other edit
    #     keeps the refusal.
    relations_only = bool(relations) and title is None and body is None and not fields
    if not relations_only:
        _refuse_edit(record)

    updates = parse_field_assignments(fields)
    if updates:
        _refuse_field_updates(record, project, updates)

    if relations:
        vocabulary = load_vocabulary(project.external_id)
        try:
            check_relation_types(relations, vocabulary, project=project.name)
        except ValueError as exc:
            raise RecordVerbError(str(exc)) from exc
        # The same refusal `bm new` makes, for the same reason: an edge to a
        # record that does not exist reads as real until `bm doctor` reports it
        # dangling, long after the author could still remember the typo (E1).
        async with db.scoped_session(stack.session_maker) as session:
            for _, target in relations:
                if not await record_exists(session, project.project_id, target):
                    raise RecordVerbError(
                        f"--rel names '{target}', which is not a record in project '{project.name}'"
                    )

    on_disk = Path(project.path, record.file_path)
    # Trigger: the record is indexed but its file is not on disk.
    # Why: an edit builds the replacement from the current body, and a missing
    #     file would silently make that body empty — a content deletion reported
    #     as a successful edit (the GAPS T12 shape, one layer up).
    # Outcome: name the path that is missing and refuse.
    if not on_disk.is_file():
        raise RecordVerbError(f"{record.record_id} is indexed but its file is missing: {on_disk}")

    current = remove_frontmatter(on_disk.read_text(encoding="utf-8"))
    # `not relations`: a run that stated an edge and nothing else has said what it
    # came for, and opening an editor on the body would make the same command mean
    # two things depending on whether a terminal happened to be attached.
    next_body = _next_body(
        current, body, may_open_editor=title is None and not updates and not relations
    )
    # Trigger: `--body` (or `--body -`) stated a replacement for the prose.
    # Why: the record's edges live in that same body, and a body edit says nothing
    #     about them (GAPS U17). The `$EDITOR` path is deliberately not here: the
    #     editor opens on the whole body, relations included, so what the user
    #     saved is what they meant.
    # Outcome: the existing `## Relations` section is carried onto the new prose.
    if body is not None:
        next_body = carry_relations(current, next_body)
    if relations:
        next_body = append_relations(next_body, relations)

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
            content=next_body,
            # Merged over the record's existing frontmatter by
            # `prepare_update_entity_content`, so an unmentioned field keeps the
            # value it had. `None` rather than `{}`: an empty mapping would still
            # take the metadata branch for no reason.
            entity_metadata=updates or None,
        ),
    )
    return WriteOutcome(
        record_id=record.record_id,
        note_type=record.note_type,
        # Project-relative, the form the history subject line uses and the form
        # `bm new` prints (GAPS U11). The absolute path was the longest field on
        # the line and the one field guaranteed to differ between machines; the
        # project's home is store-derived, so the reader never chose it. `bm path
        # <id>` is the way to get the absolute one.
        detail=result.file_path,
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
                "Replace the body, keeping the record's '## Relations' section. "
                "Use '-' to read it from stdin; omit both --title and --body to "
                "open $EDITOR."
            ),
        ),
    ] = None,
    set_fields: Annotated[
        Optional[list[str]],
        typer.Option(
            "--set",
            metavar="NAME=VALUE",
            help=(
                "Set a field this project declares, on a profile. Repeatable; "
                "run 'bm types' to see the declared fields."
            ),
        ),
    ] = None,
    rel: Annotated[
        Optional[list[str]],
        typer.Option(
            "--rel",
            metavar="TYPE:ID",
            help=(
                "Add a link to another record, e.g. --rel derived_from:tnd-q8w3e1r5. "
                "On its own it works on every record type, including a task and a "
                "finding. Repeatable; run 'bm types' to see the relation types this "
                "project declares."
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
    """Change a record that is kept current, or add a link to any record.

    The title and the body move on a guide, profile, state or inbox note, and
    `--set` writes a declared field on a profile. `--rel <type>:<id>` on its own
    adds a link to another record and works on every type, a task and a finding
    included. Every field set at creation stays set — a task is closed with
    `bm done`, and a finding is replaced by a successor written with
    `bm new --supersedes`.
    """
    from basic_memory.cli.record_notes import parse_relations, write_project_name

    try:
        project_name = write_project_name(project)
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    # A flag-shape error, refused before a database opens — the same place
    # `bm new` refuses it.
    try:
        relations = parse_relations(rel or ())
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    # Trigger: no --title, --body, --set or --rel, and no terminal to open $EDITOR on.
    # Why: the edit would rewrite the record with exactly what it already says,
    #     which is a no-op write the caller did not ask for and cannot see.
    # Outcome: an addressing failure naming every way to state the change.
    if title is None and body is None and not set_fields and not rel and not sys.stdin.isatty():
        raise fail("Error: nothing to change — pass --title, --body, --set or --rel")

    outcome = _write(
        edit_record(
            project_name=project_name,
            record_id=record_id,
            title=title,
            body=body,
            fields=set_fields or (),
            relations=relations,
        ),
        quiet=quiet,
    )
    emit_notices(_write_scope(outcome), quiet=quiet, command="edit")
    if not quiet:
        typer.echo(EDIT_AFFORDANCE)


# --- bm mark and bm done ---


async def mark_record(*, project_name: str, record_id: str, status: str) -> WriteOutcome:
    """Set one task's status, leaving its body byte-identical."""
    from basic_memory.schemas.request import EditEntityRequest
    from basic_memory.vocabulary.model import load_vocabulary, vocabulary_path

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
        # The message names the file, because the fix is a human's one-line edit
        # to it and nothing else can make it. A project governed before a status
        # was added to the defaults — `shelved`, GAPS U23 — has a full
        # `statuses:` list of its own, and a present key replaces the defaults
        # outright. `bm` must not edit that file: humans extend the vocabulary,
        # agents only select from it (GAPS W4). So it says where to go.
        raise RecordVerbError(
            f"'{status}' is not a status project '{project.name}' declares. "
            f"Allowed: {', '.join(vocabulary.statuses)}. "
            f"Add it to {vocabulary_path(project.external_id)} to enable."
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
    """Set a task's status — open, doing, blocked, shelved, done, or dropped.

    A task's status is the only field any verb changes after creation. Nothing
    else about the record moves, and no other type has one.

    `shelved` parks a task: it stops being open work without being dropped, so
    `bm brief` counts it rather than listing it. `bm mark <id> open` revives it.
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
