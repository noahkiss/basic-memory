"""`bm ls`, `bm show`, `bm path` — the three read verbs over records.

Read-only, and deliberately the cheapest verbs in the set: they resolve a scope,
run one indexed query, and print. Nothing here may pull the API, the MCP tool
layer, fastapi, or dateparser onto its path (AGENTS.md, "Measured baseline").

Three decisions are load-bearing and are stated where they are implemented:

- **Scope follows GAPS W5-C** (`--project` > nearest `.bm.yml` > every project).
  An unscoped `bm ls` is a roll-up and says which project each row came from,
  because a list that mixes projects without naming them is not actionable.
- **Identity is verified, never inferred** (GAPS T9/T10, `.forked/schema.md` §8).
  After resolving an id, the permalink that came back must equal the id asked
  for. A title that happens to match is not-found.
- **`bm path` prints the path and nothing else** (VERBS_PLAN D9). It exists for
  `$EDITOR "$(bm path tnd-x)"`, and a count line, a notice, or an affordance
  would land inside that command substitution. See `docs/OUTPUT_CONTRACT.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.scope import resolve_read_scope
from basic_memory.project_marker import MarkerError

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

    from basic_memory.models import Entity, Project

# The relation that carries supersession. One direction only: the successor owns
# the edge and the predecessor is never touched (`.forked/schema.md` §5).
SUPERSEDES = "supersedes"

# Two spaces between columns, so values line up without box drawing
# (output contract rule 1).
COLUMN_GAP = 2

# What a record with no value in a column prints. A blank would make the columns
# ambiguous to read; a sentinel that looks like data would be worse.
NO_VALUE = "-"

# Static affordances (GAPS W19 item 5). Static is the requirement, not a
# shortcut: W19's correction rules out conditional hints and per-session memory,
# because a hint that appears only sometimes teaches the surface unreliably.
LS_AFFORDANCE = "bm show <id> read the full entry · bm new record something worth finding again"
SHOW_AFFORDANCE = "bm edit <id> change it · bm path <id> print its file path"


class RecordNotFound(LookupError):
    """No record in scope carries the requested id."""


class AmbiguousRecord(LookupError):
    """The id resolves in more than one project, and no project was named."""


@dataclass(frozen=True, slots=True)
class RecordRow:
    """One printed row of `bm ls`."""

    project: str
    record_id: str
    note_type: str
    status: str
    title: str


@dataclass(frozen=True, slots=True)
class RecordListing:
    """What `bm ls` found, and whether `--limit` cut the answer short."""

    rows: list[RecordRow]
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Supersession:
    """A successor record that supersedes the one being shown."""

    record_id: str
    event_date: str

    def describe(self) -> str:
        when = f" ({self.event_date})" if self.event_date else ""
        return f"superseded by {self.record_id}{when}"


@dataclass(frozen=True, slots=True)
class ResolvedRecord:
    """One record, located: which project holds it, where its file is, and its successors."""

    project: str
    record_id: str
    path: Path
    superseded_by: tuple[Supersession, ...] = ()


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


# --- Data access ---
#
# These do what `cli/direct.py` does for the other native verbs: config →
# database → repository, with none of the API/MCP import graph on the path. They
# live here rather than in `direct.py` only because that file is open in another
# change; they belong there once it settles.


async def _projects_in_scope(session: "AsyncSession", project_name: str | None) -> list["Project"]:
    """Every registered project, or the one named. Ordered by id, which is stable.

    Raises ValueError for an unknown name: a request that cannot be scoped is an
    addressing failure, never an empty result (contract rule 5).
    """
    from basic_memory.repository.project_repository import ProjectRepository

    repository = ProjectRepository()
    if project_name is None:
        return sorted(await repository.find_all(session), key=lambda row: row.id)

    project = await repository.get_by_name(session, project_name)
    if project is None:
        raise ValueError(f"Project not found: '{project_name}'")
    return [project]


async def load_records(
    project_name: str | None,
    *,
    note_type: str | None = None,
    status: str | None = None,
    area: str | None = None,
    limit: int | None = None,
) -> RecordListing:
    """Gather the rows `bm ls` prints, across one project or all of them."""
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository.entity_repository import list_records
    from basic_memory.services.initialization import ensure_project_registry

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config)

    async with db.scoped_session(session_maker) as session:
        projects = await _projects_in_scope(session, project_name)
        names = {project.id: project.name for project in projects}
        # One row past the limit: that row is the whole evidence for "more
        # records match", and it costs a row rather than a second COUNT.
        found = await list_records(
            session,
            list(names),
            note_type=note_type,
            status=status,
            area=area,
            limit=None if limit is None else limit + 1,
        )

    truncated = limit is not None and len(found) > limit
    kept = found[:limit] if truncated else found
    return RecordListing(
        rows=[
            RecordRow(
                project=names[row.project_id],
                record_id=row.permalink,
                note_type=row.note_type,
                status=row.status or NO_VALUE,
                title=row.title,
            )
            for row in kept
        ],
        truncated=truncated,
    )


async def load_record(project_name: str | None, record_id: str) -> ResolvedRecord:
    """Locate one record by id, verifying identity (GAPS T9/T10).

    Raises RecordNotFound when no project in scope holds that permalink, and
    AmbiguousRecord when an unscoped lookup finds it in more than one.
    """
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.services.initialization import ensure_project_registry

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config)

    async with db.scoped_session(session_maker) as session:
        found: list[ResolvedRecord] = []
        for project in await _projects_in_scope(session, project_name):
            entity = await EntityRepository(project_id=project.id).get_by_permalink(
                session, record_id
            )
            # Trigger: a lookup that returned a row whose permalink is not the id.
            # Why: BM's resolver legitimately matches on title and file path, so a
            #     non-empty result is not by itself proof of identity (GAPS T10).
            # Outcome: treat it as not-found rather than as a near-match.
            if entity is None or entity.permalink != record_id:
                continue
            found.append(
                ResolvedRecord(
                    project=project.name,
                    # `record_id`, not `entity.permalink`: the guard above proves
                    # they are equal, and the column is nullable in the model.
                    record_id=record_id,
                    path=Path(project.path) / entity.file_path,
                    superseded_by=_supersessions(entity),
                )
            )

    if not found:
        raise RecordNotFound(record_id)
    if len(found) > 1:
        raise AmbiguousRecord(", ".join(sorted(record.project for record in found)))
    return found[0]


def _supersessions(entity: "Entity") -> tuple[Supersession, ...]:
    """The successors that supersede ``entity``, oldest first.

    Derived from the incoming edge, never stored on this record: a
    `superseded-by:` field would be a second copy of an edge the successor
    already owns, and the two drift the moment one is written without the other
    (`.forked/schema.md` §5).
    """
    successors = [
        Supersession(
            record_id=relation.from_entity.permalink,
            event_date=str((relation.from_entity.entity_metadata or {}).get("event-date") or ""),
        )
        for relation in entity.incoming_relations
        if relation.relation_type == SUPERSEDES
        and relation.from_entity is not None
        and relation.from_entity.permalink
    ]
    return tuple(sorted(successors, key=lambda item: (item.event_date, item.record_id)))


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
            load_records(scope.project, note_type=note_type, status=status, area=area, limit=limit)
        )
    except typer.Exit:
        raise
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    for line in render_rows(listing.rows, name_projects=not scope.is_pinned):
        typer.echo(line)

    # Contract rule 3: the count closes the listing, on its own line. Rule 5: an
    # empty result is a result — `0 records`, exit 0.
    typer.echo(f"{len(listing.rows)} records")

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
    """
    try:
        scope = resolve_read_scope(project)
    except MarkerError as exc:
        raise fail(f"Error: {exc}")

    try:
        record = run_with_cleanup(load_record(scope.project, record_id))
    except typer.Exit:
        raise
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
    typer.echo(record.path.read_bytes(), nl=False)

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
        record = run_with_cleanup(load_record(scope.project, record_id))
    except typer.Exit:
        raise
    except ValueError as exc:
        raise fail(f"Error: {exc}")
    except RecordNotFound:
        raise fail(f"Error: no record '{record_id}' in scope")
    except AmbiguousRecord as exc:
        raise fail(f"Error: '{record_id}' is in more than one project ({exc}) — name one with -p")

    typer.echo(record.path)
