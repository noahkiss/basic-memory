"""`bm edit`, `bm mark`, `bm done` — the three verbs that change a record.

They share a module because they share everything that matters: the write-chain
scope, the identity-verified lookup, the local write stack, and the shape of
what they print. What differs is one rule each, and each rule is a decision:

- **`bm edit` changes *content* on every record type but `finding`** — `task`,
  `plan`, `guide`, `profile`, `state`, `inbox` (`.forked/schema.md` §4,
  VERBS_PLAN D12, widened by GAPS U44). A task takes an edit in every status,
  `open` through `done`: an uneditable task goes stale and then gets quoted as
  fact, and the old repair — `bm done` plus a fresh `bm new` — split one item's
  history in two. Since W3 every write is a commit in the store repo, so an edit
  loses nothing. Status is still `bm mark`/`bm done` alone; `bm edit` does not
  touch it. A `finding` keeps the refusal, because correcting one in place
  destroys the evidence the record existed to hold — supersession is the answer
  when the world moved, and `--override` is the answer when the finding itself
  is wrong. A **relations-only** run — `--rel` with no `--title`, `--body` or
  `--set` — is exempt and accepted on every type (GAPS U18): an edge is a link,
  not a claim the record makes, and the pair worth linking is usually spotted
  after both records are written.
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

Every write here goes through `LocalNoteWriteStack`, so the history commit and
the vocabulary funnel happen exactly once, in the place item A put them. The
headline is deliberately not refreshed by any write (GAPS U24): it is composed
with `bm headline`, and a closing `bm mark`/`bm done` prints a prompt about it
instead of deriving it.
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
    from basic_memory.vocabulary.model import Vocabulary

# Static affordances (GAPS W19 item 5) — a fixed list per verb, never derived
# from what just happened.
EDIT_AFFORDANCE = "bm show <id> read it back · bm history dirty see uncommitted changes"
MARK_AFFORDANCE = "bm ls --status open what is still open · bm new record what you learned"

# The types whose whole point is that they say what is true now. Fixed by the
# schema, not by a project's vocabulary: a type is kept current or it is not, and
# that is a property of the type's temporal shape rather than of any one
# project's declarations.
KEPT_CURRENT_TYPES: tuple[str, ...] = ("plan", "guide", "profile", "state", "inbox")

# The types `bm edit` changes in place. A `task` is not kept current — it is
# opened and then closed — but it is still editable, because a task that has gone
# stale and cannot be corrected gets quoted as fact instead (GAPS U44, user's
# call 2026-08-21). Every type but `finding` and MCP's `note` is here.
EDITABLE_TYPES: tuple[str, ...] = ("task", *KEPT_CURRENT_TYPES)

# The one type that refuses a content edit by default. A finding is evidence: the
# correction is normally a successor written with `bm new --supersedes`, which
# keeps both halves of the reversal. `--override` is the way through, for the case
# supersession cannot express — the finding itself is wrong, not superseded.
EVIDENCE_TYPE = "finding"

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
    # The headline prompt a closing write earns (GAPS U24). The headline is
    # composed rather than derived, so the moment work stops being open is the
    # moment its one-liner may have gone stale — and the agent closing the task
    # is standing right there. None on every write that leaves the task open.
    headline_footer: Optional[str] = None


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


def _refuse_edit(record: "ExistingRecord", *, override: bool) -> None:
    """Refuse a *content* edit the record's type does not take, naming what to do instead.

    Only reached for an edit that changes what the record says — `--title`,
    `--body`, `--set`, or an `$EDITOR` session. A relations-only `--rel` run
    never gets here: it is allowed on every type (GAPS U18), because it adds an
    edge and rewrites no evidence.

    Both refusals point at a verb rather than at a rule. An agent that reads "a
    finding is immutable" files the correction somewhere else; one that reads the
    exact `bm new --supersedes` line writes the successor.

    Trigger: `--override` on a `finding`.
    Why: supersession keeps both halves of a reversal, which is right when the
        world moved and wrong when the finding was simply mistyped — a successor
        to a typo is two records saying one thing. The store's history holds the
        old text either way (W3), so nothing is destroyed by the rewrite.
    Outcome: the edit lands in place. Every other type ignores the flag.
    """
    if record.note_type in EDITABLE_TYPES:
        return
    if record.note_type == EVIDENCE_TYPE:
        if override:
            return
        raise RecordVerbError(
            f"'{record.record_id}' is a finding, and a finding is evidence — record the "
            f'correction with bm new finding "<title>" --supersedes {record.record_id}, '
            f"or pass --override to rewrite this one in place "
            f"(the store's history keeps the old text)."
        )
    # Everything left is outside the record schema — MCP's `note`, or a type an
    # ungoverned project invented. It names `--rel` because the reader has just
    # been told this record cannot be edited, and the one edit it *can* take is
    # the one they most often want next.
    raise RecordVerbError(
        f"'bm edit' changes the content of a record ({', '.join(EDITABLE_TYPES)}); "
        f"'{record.record_id}' is a {record.note_type} — "
        f"'bm edit {record.record_id} --rel <type>:<id>' on its own adds a link"
    )


def _override_notices(record: "ExistingRecord", *, override: bool) -> tuple[str, ...]:
    """The line `--override` earns on a type that never refused the edit (rule 4).

    Trigger: `--override` on anything but a `finding`.
    Why: the flag did nothing, and silently accepting it teaches an agent to pass
        it everywhere — which is how a flag that exists to be deliberate becomes
        boilerplate. It is a notice rather than an error because the edit itself
        is well-formed and the caller's intent is unambiguous (contract rule 5:
        this is content, not an addressing failure).
    Outcome: one notice after the payload, and the edit proceeds; `--quiet` drops
        it like any other notice.
    """
    if not override or record.note_type == EVIDENCE_TYPE:
        return ()
    return (f"--override has no effect on a {record.note_type} — only a finding refuses an edit",)


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
    override: bool = False,
) -> WriteOutcome:
    """Replace a record's title, body and declared fields, or add an edge.

    Every record type takes a content edit but `finding`, which refuses one
    unless `override` is set (GAPS U44). Only a `profile` has declared fields,
    and `fields` is the only frontmatter this verb writes — every set-once field
    stays as `bm new` wrote it, `status` included.

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
    # Why: the type refusal exists because a finding is evidence rather than a
    #     draft (D12, U44). An edge states nothing of the sort: it adds a link and
    #     rewrites nothing the record claims, and provenance is usually noticed on
    #     the read-back, after both records are written (GAPS U18). `fields` is
    #     read raw rather than parsed, so a malformed `--set` still reaches the
    #     type refusal it used to.
    # Outcome: a relations-only edit is allowed on every type; every other edit
    #     keeps the refusal.
    relations_only = bool(relations) and title is None and body is None and not fields
    if not relations_only:
        _refuse_edit(record, override=override)

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
        notices=(
            *result.notices,
            *_override_notices(record, override=override),
            *_ungoverned_notices(project.external_id),
        ),
    )


@app.command(name="edit")
def edit(
    record_id: Annotated[str, typer.Argument(help="The record's id, e.g. finding-q8w3e1r5.")],
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
                "Add a link to another record, e.g. --rel derived_from:task-q8w3e1r5. "
                "On its own it works on every record type, including a task and a "
                "finding. Repeatable; run 'bm types' to see the relation types this "
                "project declares."
            ),
        ),
    ] = None,
    override: Annotated[
        bool,
        typer.Option(
            "--override",
            help=(
                "Rewrite a finding in place instead of superseding it. No effect on any other type."
            ),
        ),
    ] = False,
    project: Annotated[
        Optional[str],
        typer.Option("--project", "-p", help="Project to write to. Defaults to .bm.yml."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """Change a record's title, body or declared fields, or add a link to it.

    Every type takes an edit — a task in any status, `open` through `done`
    included — except a finding, which is evidence: correct one by writing its
    successor with `bm new finding --supersedes <id>`, or pass `--override` to
    rewrite it in place. `--set` writes a declared field on a profile, and
    `--rel <type>:<id>` on its own adds a link to any record. Every other field
    set at creation stays set, `status` included: `bm mark` and `bm done` are
    what move it.
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
    #     `--override` is deliberately absent from this list: it lifts a refusal
    #     and states no change of its own, so `--override` alone is that same
    #     no-op (GAPS U44).
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
            override=override,
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

    if record.note_type not in ("task", "plan"):
        raise RecordVerbError(
            f"only a task or a plan carries a status; '{record.record_id}' is a {record.note_type}"
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
        headline_footer=_headline_footer(project.external_id, status, vocabulary),
    )


def _headline_footer(
    external_id: str, status: str, vocabulary: "Vocabulary | None"
) -> Optional[str]:
    """The headline prompt a write that takes a task out of "open" earns (GAPS U24).

    Only a closing status asks: marking a task `doing` changes nothing about
    what is next, while `done`, `dropped` and `shelved` are exactly the moments
    the composed headline goes stale. An ungoverned project judges "closing" by
    the default statuses, the same reading `bm brief`'s shelved count uses.
    """
    from basic_memory.services.headline import MAX_HEADLINE_CHARS, read_headline
    from basic_memory.vocabulary.model import inactive_statuses

    if status not in inactive_statuses(vocabulary):
        return None
    current = read_headline(external_id)
    if current is None:
        return f'no headline set — bm headline "<text>" (max {MAX_HEADLINE_CHARS} chars)'
    return (
        f'headline: "{current}" — still right? bm headline "<text>" updates it, '
        f'bm headline "" clears it'
    )


@app.command(name="mark")
def mark(
    record_id: Annotated[str, typer.Argument(help="The task's id, e.g. task-7k2m9x4p.")],
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
        # Closing work is the moment the composed headline may have gone stale,
        # and this line is what keeps it fresh without derivation (GAPS U24).
        if outcome.headline_footer:
            typer.echo(outcome.headline_footer)
        typer.echo(MARK_AFFORDANCE)


@app.command(name="done")
def done(
    record_id: Annotated[str, typer.Argument(help="The task's id, e.g. task-7k2m9x4p.")],
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
        # Same prompt `bm mark` prints on a closing status: `done` always is one.
        if outcome.headline_footer:
            typer.echo(outcome.headline_footer)
        typer.echo(MARK_AFFORDANCE)
