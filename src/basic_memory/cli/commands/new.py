"""`bm new` — write one record (verbs item E).

The verb that creates everything else in the system, so three of its decisions
are load-bearing and none of them may be relaxed later:

- **`permalink` equals `id`, byte-for-byte** (`.design/schema.md` §2). Edges bind
  to the permalink, so an id that is not also the permalink makes `[[tnd-…]]`
  land as a dangling relation and the record unreachable by its own name.
- **`review-by` is not invented here.** Absent `--review-by`,
  `prepare_accepted_note_create` stamps it from the project's `review_months`
  (GAPS W5 item 1). Stamping it twice would validate one value and store another.
- **A date the writer states beats a date bm stamps, and bm says which it is.**
  `--opened` / `--event-date` require `--date-source`; with no date flag the verb
  stamps today and declares it `inferred`, because the fidelity ladder has no rung
  for a clock reading and the highest rung is a lie (GAPS U1).
- **An unknown `--type` files an `inbox` record**, carrying `proposed-type`
  (GAPS W4). Agents propose a type; only a human enables one. Rejecting the
  write instead would send the content nowhere, which is the drop the escape
  hatch exists to prevent. The one exception is a project whose vocabulary
  declares no `inbox` type at all: there is nowhere to file the proposal, so the
  verb refuses and says so rather than failing in the checker (GAPS E2).
  Before the hatch fires, an *alias* the vocabulary declares resolves to its
  canonical type and the record stamps that type (GAPS U25) — `bm new decision`
  writes a `finding`, with a notice naming the alias.

Scope is the **write** chain — `--project`, then the nearest `.bm.yml`, then the
default project (`project_marker.resolve_cli_project`). Reads go unscoped when
nothing pins them; a write cannot, because it needs a home.

No interactive prompts. Agents run this verb, and a prompt to an agent is a
hang. `$EDITOR` opens only when there is a terminal to open it on (VERBS_PLAN
D11).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import ReadScope
from basic_memory.vocabulary.glossary import (
    DATE_CONFIDENCES,
    DATE_SOURCES,
    PICKING_QUESTIONS,
    REF_BEARING_SOURCES,
    SUPERSEDES_RELATION,
)

# Static affordance (GAPS W19 item 5). Static is the requirement, not a
# shortcut: a hint that appears only sometimes teaches the surface unreliably.
NEW_AFFORDANCE = "bm show <id> read it back · bm ls list what is here · bm done <id> close a task"

# The status every task opens at. `bm mark` is what moves it; nothing else does
# (`.design/schema.md` §4 — status is one of the four mutable things).
INITIAL_TASK_STATUS = "open"

# Where a date bm invented came from, when the writer stated none. `inferred` is
# the ladder's lowest rung and the only truthful one here: the ladder has no rung
# for "read off the clock", and `inline` — what this verb used to write
# unconditionally — claims the source text carried the date, which is precisely
# the laundering the ladder exists to prevent (GAPS U1).
#
# The cost is deliberate. `bm doctor`'s hygiene group reports every inferred date
# for human review, so a record written with no date flags lands in that pile.
# That is the correct signal now that `--opened` / `--event-date` with
# `--date-source` can state a real date instead; before them the flags did not
# exist and the pile would have been the whole corpus.
DEFAULT_DATE_SOURCE = "inferred"

# `day` whichever rung the date came from: confidence is how *precise* the date
# is, not how it was learned, and a calendar date is precise to the day.
DEFAULT_DATE_CONFIDENCE = "day"

# The one date field each type carries, keyed by type (`.design/schema.md` §2).
# `profile.since` is deliberately absent: it is optional, and a `since` this verb
# invented would claim a start date the writer never gave. `guide`, `state` and
# `inbox` have no date field at all — there is physically nowhere to put one.
_TYPE_DATE_FIELD = {"task": "opened", "plan": "opened", "finding": "event-date"}

# Which flag writes which frontmatter key, and the types that carry it (§2/§3).
#
# `--review-by` sits here with the other two but takes no provenance: it is an
# appointment the writer sets for the future, not a claim about when something
# happened, so there is nothing to say about where it came from.
_DATE_FLAGS: Mapping[str, tuple[str, frozenset[str]]] = {
    "--opened": ("opened", frozenset({"task", "plan"})),
    "--event-date": ("event-date", frozenset({"finding"})),
    "--review-by": ("review-by", frozenset({"finding", "guide"})),
}

# The shape the schema's date fields take. The regex alone is not enough —
# `2026-02-30` matches it and is not a day — and `date.fromisoformat` alone is not
# either, because it also accepts `20260817` and `2026-W33-1`.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


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


# --- Dates the writer states, rather than dates bm invents (GAPS U1) ---


@dataclass(frozen=True, slots=True)
class DateOptions:
    """The date flags one `bm new` invocation carried, unvalidated."""

    opened: Optional[str] = None
    event_date: Optional[str] = None
    review_by: Optional[str] = None
    date_source: Optional[str] = None
    date_confidence: Optional[str] = None
    date_ref: Optional[str] = None


def is_iso_date(value: str) -> bool:
    """True for a real ``YYYY-MM-DD`` calendar date, and nothing else."""
    if not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_date_flags(options: DateOptions) -> None:
    """Refuse a malformed date or an undeclared ladder value before anything is written.

    Checked at the flag level rather than in the checker so the message names the
    *flag* the writer typed. Both ladders are read from `vocabulary/glossary.py`,
    which is the schema's one copy of them — a second list here is what U1's
    prose duplication already proved goes stale.
    """
    for flag, value in (
        ("--opened", options.opened),
        ("--event-date", options.event_date),
        ("--review-by", options.review_by),
    ):
        if value is not None and not is_iso_date(value):
            raise ValueError(f"{flag} takes a date as YYYY-MM-DD, got '{value}'")

    for flag, value, allowed in (
        ("--date-source", options.date_source, DATE_SOURCES),
        ("--date-confidence", options.date_confidence, DATE_CONFIDENCES),
    ):
        if value is not None and value not in allowed:
            raise ValueError(f"{flag} takes one of {', '.join(allowed)}, got '{value}'")


def date_fields(note_type: str, options: DateOptions, *, today: date) -> dict[str, str]:
    """The date and provenance keys a record of ``note_type`` carries, in key order.

    Raises ValueError for a flag the type does not carry — a date has exactly one
    legal name per type, and there is physically nowhere to put one on a guide, a
    state, or an inbox record (§2) — and for a stated date with no
    `--date-source`, which is the whole point of being allowed to state a date.
    """
    for flag, value in (
        ("--opened", options.opened),
        ("--event-date", options.event_date),
        ("--review-by", options.review_by),
    ):
        if value is None:
            continue
        key, owners = _DATE_FLAGS[flag]
        if note_type not in owners:
            carriers = " or ".join(sorted(owners))
            raise ValueError(
                f"{flag} writes '{key}', which only a {carriers} carries; "
                f"this record is a {note_type}"
            )

    date_field = _TYPE_DATE_FIELD.get(note_type)
    fields: dict[str, str] = {}

    if date_field is None:
        for flag, value in (
            ("--date-source", options.date_source),
            ("--date-confidence", options.date_confidence),
            ("--date-ref", options.date_ref),
        ):
            if value is not None:
                raise ValueError(
                    f"{flag} records where a date came from, and a {note_type} "
                    f"carries no date field"
                )
    else:
        stated = options.opened if date_field == "opened" else options.event_date

        # Trigger: a stated date with no --date-source.
        # Why: the flag exists so a migrated record can say the date came from the
        #     source text rather than from bm's clock. Accepting a stated date
        #     without it would stamp the default rung on a date the writer *did*
        #     know the origin of, which reopens U1 through the other door.
        # Outcome: refuse, naming the field and the ladder.
        if stated is not None and options.date_source is None:
            raise ValueError(
                f"--date-source is required with a stated date: it is what says where "
                f"'{date_field}' came from. Allowed values: {', '.join(DATE_SOURCES)}"
            )

        source = options.date_source or DEFAULT_DATE_SOURCE
        if source in REF_BEARING_SOURCES and options.date_ref is None:
            raise ValueError(
                f"--date-source '{source}' names re-openable evidence, so --date-ref is "
                "required: a session id with a line for a transcript, a commit sha for git"
            )
        if options.date_ref is not None and source not in REF_BEARING_SOURCES:
            allowed = " or ".join(sorted(REF_BEARING_SOURCES))
            raise ValueError(
                f"--date-ref points at evidence to re-open, and '{source}' points at none; "
                f"it is allowed only with --date-source {allowed}"
            )

        # The provenance triple travels with the date and never without it: a date
        # whose origin is unrecorded cannot be re-opened (§2), and the checker
        # rejects either half alone.
        fields[date_field] = stated if stated is not None else today.isoformat()
        fields["date-source"] = source
        fields["date-confidence"] = options.date_confidence or DEFAULT_DATE_CONFIDENCE
        if options.date_ref is not None:
            fields["date-ref"] = options.date_ref

    # After the triple, which is the order `.design/schema.md` §3 writes it in.
    # Absent, the write path stamps it from the project's `review_months`
    # (GAPS W5 item 1); stated here, that stamp stands down.
    if options.review_by is not None:
        fields["review-by"] = options.review_by
    return fields


@dataclass(frozen=True, slots=True)
class NewRecordOutcome:
    """What `bm new` wrote, and what it has to say about it afterwards."""

    record_id: str
    note_type: str
    # Project-relative — `findings/tnd-…--slug.md`, not the absolute path (U11).
    path: str
    project: str
    notices: tuple[str, ...]


def read_body(body: Optional[str]) -> str:
    """Resolve the note's body: the flag, stdin, `$EDITOR`, or nothing (D11).

    `$EDITOR` opens only when stdin is a terminal. An agent runs this verb with
    no terminal attached, and an editor launched there is a hang with no prompt
    to explain it — so the same invocation writes an empty body instead, which
    `bm edit` can fill in.

    `--body -` reads stdin, the way `git commit -F -` does. It is also the only
    route a shell never parsed, which is why a body holding backticks or `$(`
    belongs on it (GAPS U46).
    """
    from basic_memory.cli.record_notes import STDIN_BODY, body_from_editor

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
    dates: Mapping[str, str],
) -> dict[str, str]:
    """The frontmatter block a new record carries, in a fixed key order.

    Fixed order is a GAPS W3 requirement: the history compares files byte for
    byte, and a key order that varies makes every write a diff to read past.

    ``dates`` comes from `date_fields`, already validated against ``note_type``.
    """
    fields: dict[str, str] = {"id": record_id, "permalink": record_id, "source": source}
    fields.update(dates)

    if area is not None:
        fields["area"] = area
    # A plan opens the way a task does: the two share the status lifecycle
    # (GAPS U38), and a plan born without one would fail its own checker.
    if note_type in ("task", "plan"):
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
    relations: tuple[tuple[str, str], ...],
    dates: DateOptions,
) -> NewRecordOutcome:
    """Allocate an id, render the record, and write it through the local stack.

    One database bootstrap for the whole verb: the write stack owns the engine
    and the session maker, and the id allocation borrows the same session maker
    rather than opening a second one (the pool holds one connection).

    ``supersedes`` is sugar for ``--rel supersedes:<id>`` and joins ``relations``
    here, so there is one edge-writing path rather than two (GAPS U14). It keeps
    its own flag name in every refusal, because the writer typed that flag and
    not the other one.
    """
    # Deferred: the write stack and the record schema pull SQLAlchemy and
    # Pydantic, which must not load at CLI import time (AGENTS.md, baseline).
    from basic_memory import db
    from basic_memory.cli.record_notes import (
        UNGOVERNED_NOTICE,
        RecordNote,
        allocate_record_id,
        check_relation_types,
        declared_types,
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

        vocabulary = load_vocabulary(project.external_id)

        # An edge's *type* is the project's vocabulary to declare (GAPS U14),
        # judged before the database is asked anything about its target.
        #
        # `--supersedes` is exempt, and only `--supersedes` is: supersession is
        # *schema* vocabulary (`glossary.SUPERSEDES_RELATION`), already governed
        # by the checker's `supersedes-not-on-type` rule, so a project that
        # narrowed its `relations:` list has not thereby turned the flag off.
        check_relation_types(relations, vocabulary, project=project.name)
        edges: list[tuple[str, tuple[str, str]]] = [("--rel", edge) for edge in relations]
        if supersedes is not None:
            edges.insert(0, ("--supersedes", (SUPERSEDES_RELATION, supersedes)))

        # Trigger: --rel or --supersedes names a well-formed id no record in this
        #     project holds.
        # Why: the funnel's supersession rule judges the *type* of the record being
        #     written, not whether the target exists, so a typo writes
        #     `- supersedes [[tnd-aaaa1111]]` and exits 0. The edge then reads as a
        #     real chain until `bm doctor` reports it as dangling — the author is
        #     told at the wrong moment, about a mistake only they can still
        #     remember making (GAPS E1).
        # Outcome: refuse before writing. An edge to a record that does not exist
        #     is a typo every time.
        for flag, (_, target) in edges:
            if not await record_exists(session, project.project_id, target):
                raise ValueError(
                    f"{flag} names '{target}', which is not a record in project '{project.name}'"
                )

        # Resolved before an id is drawn: a type this project cannot file is a
        # refusal, and spending an id on a write that never happens leaves a gap
        # in the sequence for no reason (GAPS E2).
        resolved = resolve_note_type(requested_type, vocabulary, project=project.name)
        note_type, proposed_type = resolved.note_type, resolved.proposed_type

        # Resolved before an id is drawn, for E2's reason: a date flag the record's
        # type cannot carry is a refusal, and the type is only known once the
        # escape hatch has had its say — `--opened` on an undeclared type files as
        # `inbox`, which carries no date at all (GAPS U1).
        stamped_dates = date_fields(note_type, dates, today=date.today())

        record_id = await allocate_record_id(session, project.project_id, note_type)

    file_path = record_path(note_type, record_id, title)

    content = record_markdown(
        build_frontmatter(
            record_id=record_id,
            source=source,
            note_type=note_type,
            proposed_type=proposed_type,
            area=area,
            dates=stamped_dates,
        ),
        body,
        relations=[edge for _, edge in edges],
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
    # An alias resolved: say so once, so the writer learns the canonical name
    # without being corrected (GAPS U25). The record itself stamped the type.
    if resolved.alias_of is not None:
        notices.append(
            f"{note_type} recorded (alias: {resolved.alias_of} is an alias for {note_type})"
        )
    if proposed_type is not None:
        # Name the declared set inline (GAPS U25): the fallback is deliberate,
        # but a writer who only sees `inbox` in the payload line should not need
        # a second command to learn what would have landed as itself.
        names = ", ".join(name for name in declared_types(vocabulary) if name != note_type)
        notices.append(
            f"no type '{proposed_type}' — filed as inbox proposing it "
            f"(types: {names} · bm types for detail)"
        )
    if vocabulary is None:
        notices.append(UNGOVERNED_NOTICE)

    return NewRecordOutcome(
        record_id=record_id,
        note_type=note_type,
        # Store-relative, the form the history subject line already uses (GAPS
        # U11). The absolute path was the longest field on the payload line, the
        # one field guaranteed to differ between machines, and one the reader
        # never chose — a project's home is store-derived. `bm path <id>` is the
        # way to get the absolute one.
        path=result.file_path,
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
            help=(
                "The note's body. A body with backticks or $( must come from stdin: "
                "'--body -' with a quoted heredoc (<<'EOF'), or the shell rewrites it "
                "first. Omit it to open $EDITOR."
            ),
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
    rel: Annotated[
        Optional[list[str]],
        typer.Option(
            "--rel",
            metavar="TYPE:ID",
            help=(
                "Link this record to another one, e.g. --rel derived_from:task-q8w3e1r5. "
                "Repeatable; run 'bm types' to see the relation types this project declares."
            ),
        ),
    ] = None,
    opened: Annotated[
        Optional[str],
        typer.Option(
            "--opened",
            metavar="YYYY-MM-DD",
            help="The day a task was opened. Needs --date-source. Defaults to today.",
        ),
    ] = None,
    event_date: Annotated[
        Optional[str],
        typer.Option(
            "--event-date",
            metavar="YYYY-MM-DD",
            help="The day a finding's subject happened. Needs --date-source. Defaults to today.",
        ),
    ] = None,
    review_by: Annotated[
        Optional[str],
        typer.Option(
            "--review-by",
            metavar="YYYY-MM-DD",
            help=(
                "The day a finding or guide needs a second look. "
                "Defaults to the project's review_months out from today."
            ),
        ),
    ] = None,
    date_source: Annotated[
        Optional[str],
        typer.Option(
            "--date-source",
            help=(
                f"How you know the date: {', '.join(DATE_SOURCES)}. Required whenever "
                f"you state one; defaults to '{DEFAULT_DATE_SOURCE}' for the date bm stamps."
            ),
        ),
    ] = None,
    date_confidence: Annotated[
        Optional[str],
        typer.Option(
            "--date-confidence",
            help=(
                f"How precise the date is: {', '.join(DATE_CONFIDENCES)}. "
                f"Defaults to '{DEFAULT_DATE_CONFIDENCE}'."
            ),
        ),
    ] = None,
    date_ref: Annotated[
        Optional[str],
        typer.Option(
            "--date-ref",
            help=(
                "The evidence the date came from: a commit sha for git, "
                "<session-id>#L<line> for a transcript. Required for those two, "
                "and refused for the other rungs."
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
    """Write one record: a task, a guide, a finding, a profile, a state, an inbox note.

    The record gets an id, and its permalink is that id — that is what makes
    `[[tnd-…]]` resolve to it. A type this project does not declare is not an
    error: the record is filed as `inbox` proposing that type, for a human to
    promote.

    Link it to what it came out of with `--rel <type>:<id>`, repeated as often as
    it takes. The relation types are the ones this project declares, and the
    target has to be a record this project already holds.

    Dates: state the real one when you know it — `--opened` on a task,
    `--event-date` on a finding — and say where it came from with
    `--date-source`. With no date flag the record gets today's date declared
    `inferred`, which is what puts it in `bm doctor`'s review pile rather than
    passing a guess off as a date read from the source.

    A body with backticks or `$(` must come from stdin: `--body -` with a quoted
    heredoc (`<<'EOF'`), or the shell rewrites it first.
    """
    from basic_memory.cli.record_notes import (
        DEFAULT_SOURCE,
        parse_relations,
        shell_mangled_notices,
        write_project_name,
    )
    from basic_memory.vocabulary.ids import is_record_id

    # Trigger: --supersedes given a value that is not a record id.
    # Why: the edge is written as `[[<value>]]`, which resolves by permalink; a
    #     value that cannot be one lands as a dangling relation that reads as a
    #     real edge until `bm doctor` reports it.
    # Outcome: refuse before writing, naming the shape expected.
    if supersedes is not None and not is_record_id(supersedes):
        raise fail(f"Error: --supersedes takes a record id, got '{supersedes}'")

    # `--rel` is a flag-shape error the same way: refused before a database opens.
    try:
        relations = parse_relations(rel or ())
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    dates = DateOptions(
        opened=opened,
        event_date=event_date,
        review_by=review_by,
        date_source=date_source,
        date_confidence=date_confidence,
        date_ref=date_ref,
    )
    # A malformed date and an undeclared ladder value are both flag errors, so
    # they are refused here rather than after a database is opened.
    try:
        validate_date_flags(dates)
    except ValueError as exc:
        raise fail(f"Error: {exc}")

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
                relations=relations,
                dates=dates,
            )
        )
    except typer.Exit:
        raise
    except Exception as exc:
        raise fail(f"Error: {exc}")

    typer.echo(f"{outcome.record_id}  {outcome.note_type}  {outcome.path}")
    typer.echo("1 record")

    if not quiet:
        # The shell notice reads the flag the caller typed, not the body that
        # arrived: `--body -` and an absent `--body` came from stdin and from
        # `$EDITOR`, and neither passed through a shell (GAPS U46).
        for line in (*shell_mangled_notices(body), *outcome.notices):
            typer.echo(line)
    emit_notices(ReadScope(project=outcome.project, origin="write"), quiet=quiet, command="new")
    if not quiet:
        typer.echo(NEW_AFFORDANCE)
