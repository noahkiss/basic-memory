"""Direct service-layer access for fast native CLI commands.

Native commands must talk to the repository/service layer directly and must
not reach through the MCP tool layer or the in-process FastAPI app: importing
either costs seconds of startup and ~100 MB of memory (see AGENTS.md,
"Measured baseline", and GAPS.md T18). This module is the supported way for a
native command to get a service object — it wires config → database →
repository → service with none of the API/MCP import graph on the path.

The boundary is guarded structurally: a test runs a native command in a
subprocess and asserts that ``basic_memory.api.app``, ``basic_memory.mcp.tools``,
``basic_memory.mcp.async_client``, ``basic_memory.mcp.clients``, ``fastapi``,
and ``dateparser`` never enter ``sys.modules``.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from basic_memory.vocabulary.glossary import SUPERSEDES_RELATION

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from basic_memory.models import Entity, Project
    from basic_memory.repository.entity_repository import (
        HygieneRecord,
        IndexedFile,
        PermalinkIntegrityIssue,
        StaleDoingRecord,
    )
    from basic_memory.repository.relation_repository import UnresolvedRelationReportRow
    from basic_memory.repository.violation_repository import ViolationRow
    from basic_memory.services.project_service import ProjectService


async def direct_project_service() -> "ProjectService":
    """Build a ProjectService wired straight to the database.

    Uses the same engine bootstrap as the API path (``get_or_create_db``, which
    also runs migrations), so a direct command sees exactly the state an
    API-routed command would. Callers run under ``run_with_cleanup``, which owns
    engine shutdown — the same lifecycle client-routed commands use.
    """
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.services.initialization import ensure_project_registry
    from basic_memory.services.project_service import ProjectService

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config, bootstrap=False)
    return ProjectService(repository=ProjectRepository(), session_maker=session_maker)


@dataclass(frozen=True, slots=True)
class ProjectRef:
    """A project's name and the store directory it owns."""

    name: str
    external_id: str


async def direct_project_refs(project_name: str | None) -> list[ProjectRef]:
    """Resolve one project to a ref, or every registered project when ``None``.

    ``None`` is what an unscoped read resolves to under GAPS W5-C, and `bm types`
    is the caller: it needs each project's ``external_id`` to find that project's
    ``vocabulary.yml``. Refs come back ordered by name, so a registry that did
    not change renders the same report twice.

    Raises ValueError for an unknown project: an unaddressable request is a
    failure, not an empty result (contract rule 5).
    """
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.services.initialization import ensure_project_registry

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config, bootstrap=False)
    async with db.scoped_session(session_maker) as session:
        repository = ProjectRepository()
        if project_name is None:
            projects = sorted(await repository.find_all(session), key=lambda row: row.name)
        else:
            project = await repository.get_by_name(session, project_name)
            if project is None:
                raise ValueError(f"Project not found: '{project_name}'")
            projects = [project]
        return [
            ProjectRef(name=project.name, external_id=project.external_id) for project in projects
        ]


@dataclass(frozen=True, slots=True)
class UnreadableVocabulary:
    """One project whose ``vocabulary.yml`` could not be parsed, and why."""

    project: str
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class RevalidationScan:
    """What one revalidation pass rechecked, and which projects it could not read."""

    revalidated: int
    unreadable: tuple[UnreadableVocabulary, ...] = ()
    # Projects whose untouched default snapshot was rewritten to the current
    # defaults on this pass (GAPS U39) — the notice layer names each one once.
    upgraded: tuple[str, ...] = ()


async def direct_revalidate_vocabulary(project_name: str | None = None) -> RevalidationScan:
    """Re-check records whose project's vocabulary changed, project by project.

    ``project_name`` pins one project; ``None`` covers every project in the
    registry, which is what an unscoped read means under GAPS W5-C. Both shapes
    exist because the notice that consumes this reads either one count or a
    roll-up depending on whether the cwd is marked.

    This is the trigger's only caller for now, and it is deliberately not wired
    into a scan or sync pass: violations go stale when the *vocabulary* changes,
    not when files do, so firing on sync would leave counts wrong for anyone who
    never syncs and right only by accident for everyone else. Reporting is
    GAPS W5 item 6's; this returns a count and prints nothing.

    Trigger: one project's ``vocabulary.yml`` is malformed (GAPS V-J2).
    Why: the raise stays — an unreadable vocabulary must never degrade into "not
        governed" (GAPS W4) — but a pass over every project must not lose the
        other projects to one typo. Before this, the first bad file aborted the
        whole pass and the notice riding on it printed nothing at all, for every
        verb and every project, with only a log line to say so.
    Outcome: that project is rechecked no further and named in ``unreadable``,
        the rest of the registry is revalidated as usual, and the caller decides
        what to say. The same per-project shape ``read_vocabularies`` uses for
        `bm brief` (GAPS W8 F1).

    Raises ValueError for an unknown project name: an unaddressable request is a
    failure, not an empty result (contract rule 5).
    """
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.services.initialization import ensure_project_registry
    from basic_memory.services.vocabulary_revalidation import revalidate_if_vocabulary_changed
    from basic_memory.vocabulary.model import VocabularyError, vocabulary_path

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config, bootstrap=False)
    async with db.scoped_session(session_maker) as session:
        repository = ProjectRepository()
        if project_name is None:
            projects = await repository.find_all(session)
        else:
            project = await repository.get_by_name(session, project_name)
            if project is None:
                raise ValueError(f"Project not found: '{project_name}'")
            projects = [project]

        revalidated = 0
        unreadable: list[UnreadableVocabulary] = []
        upgraded: list[str] = []
        for registered in projects:
            try:
                result = await revalidate_if_vocabulary_changed(session, registered)
            except VocabularyError as exc:
                unreadable.append(
                    UnreadableVocabulary(
                        project=registered.name,
                        path=str(vocabulary_path(registered.external_id)),
                        reason=str(exc),
                    )
                )
                continue
            revalidated += result.revalidated
            if result.upgraded:
                upgraded.append(registered.name)
        return RevalidationScan(
            revalidated=revalidated, unreadable=tuple(unreadable), upgraded=tuple(upgraded)
        )


# How long a `state` record may sit untouched before doctor asks about it.
# `vocabulary.yml` declares no staleness key — its allowed keys are types,
# statuses, areas, review_months and fields — so the number is fixed here and
# the report prints it, rather than being a threshold nobody can see.
STALE_STATE_DAYS = 30

# How long a record may sit in `doing` before doctor asks about it. Seven days
# because a week is one planning cycle: a record still claiming to be in flight
# after a whole cycle has passed is almost always abandoned rather than active —
# finished and never closed, or stalled and never marked blocked. Same reason as
# STALE_STATE_DAYS for living here: `vocabulary.yml` declares no staleness key,
# so the number is fixed in code and the report prints it.
STALE_DOING_DAYS = 7


@dataclass(frozen=True, slots=True)
class MissingFile:
    """One indexed record whose file is no longer on disk (GAPS U10)."""

    file_path: str
    permalink: str | None


@dataclass(frozen=True, slots=True)
class ProjectIntegrityReport:
    """One project's integrity findings: the checks that have right answers."""

    unresolved: "list[UnresolvedRelationReportRow]" = field(default_factory=list)
    permalink_issues: "list[PermalinkIntegrityIssue]" = field(default_factory=list)
    missing_files: list[MissingFile] = field(default_factory=list)
    errors: "list[ViolationRow]" = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return (
            len(self.unresolved)
            + len(self.permalink_issues)
            + len(self.missing_files)
            + len(self.errors)
        )


@dataclass(frozen=True, slots=True)
class ProjectHygieneReport:
    """One project's hygiene findings: the checks that need a person."""

    review_due: "list[HygieneRecord]" = field(default_factory=list)
    inferred_dates: "list[HygieneRecord]" = field(default_factory=list)
    stale_states: "list[HygieneRecord]" = field(default_factory=list)
    # Records left in `doing` past STALE_DOING_DAYS (GAPS U43). Its own list
    # rather than another `HygieneRecord` one: the row names the id and the
    # title, because every move it offers takes an id.
    stale_doing: "list[StaleDoingRecord]" = field(default_factory=list)
    inbox: "list[HygieneRecord]" = field(default_factory=list)
    advisories: "list[ViolationRow]" = field(default_factory=list)
    # A hand-edited vocabulary missing names the current defaults declare
    # (GAPS U39): the delta as one human line, or None when current. Hygiene,
    # not integrity — the human ignoring it is a decision, not a defect.
    vocabulary_outdated: "str | None" = None

    @property
    def issue_count(self) -> int:
        return (
            len(self.review_due)
            + len(self.inferred_dates)
            + len(self.stale_states)
            + len(self.stale_doing)
            + len(self.inbox)
            + len(self.advisories)
            + (1 if self.vocabulary_outdated else 0)
        )


def _missing_files(project_path: Path, indexed: "Sequence[IndexedFile]") -> list[MissingFile]:
    """Which indexed rows have no file behind them any more (GAPS U10).

    Files are the source of truth, so a row without one is stale index, not a
    record: `bm ls` still lists it, search still returns it, and every count
    still includes it — while `bm show` on the same id exits 1 saying the file is
    gone. One `stat` per row is the only honest check, and the rows are already
    in memory from the query beside it.

    Ordering follows the query (file path ascending), so a corpus that did not
    change reports the same rows twice.
    """
    return [
        MissingFile(file_path=row.file_path, permalink=row.permalink)
        for row in indexed
        if not (project_path / row.file_path).exists()
    ]


@dataclass(frozen=True, slots=True)
class ProjectDoctorReport:
    """Everything ``bm doctor`` prints about one project."""

    project_name: str
    integrity: ProjectIntegrityReport = field(default_factory=ProjectIntegrityReport)
    hygiene: ProjectHygieneReport = field(default_factory=ProjectHygieneReport)
    # True when this pass rewrote an untouched default snapshot to the current
    # defaults (GAPS U39). Informational — never counted as an issue.
    vocabulary_upgraded: bool = False


async def direct_doctor_report(
    project_names: Sequence[str] | None = None,
    *,
    include_integrity: bool = True,
    include_hygiene: bool = True,
    today: date | None = None,
) -> list[ProjectDoctorReport]:
    """Gather ``bm doctor``'s findings for one project, several, or all of them.

    ``project_names`` of ``None`` means every registered project, which is what
    an unscoped read resolves to (GAPS W5-C). Reports come back ordered by
    project name, so a corpus that did not change prints the same report twice.

    A group the caller did not ask for is not queried, so ``--only`` is a saving
    rather than a filter over work already done.

    Raises ValueError for an unknown project name: a request that cannot be
    scoped is an addressing failure, never an empty result (contract rule 5).
    """
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.repository.relation_repository import RelationRepository
    from basic_memory.repository.violation_repository import ViolationRepository
    from basic_memory.services.initialization import ensure_project_registry

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config, bootstrap=False)

    review_cutoff = today if today is not None else date.today()
    # Local-aware, because that is how the rows were stamped: SQLite keeps the
    # wall clock and drops the offset, so an aware UTC bound would compare
    # against local wall times and shift the window by the offset.
    now = datetime.now().astimezone()
    stale_before = now - timedelta(days=STALE_STATE_DAYS)
    # One instant for both staleness windows and for the day count on the rows,
    # so a report cannot say a record is 8 days old against a cutoff taken a
    # moment earlier.
    doing_stale_before = now - timedelta(days=STALE_DOING_DAYS)

    async with db.scoped_session(session_maker) as session:
        repository = ProjectRepository()
        if project_names is None:
            projects = sorted(await repository.find_all(session), key=lambda row: row.name)
        else:
            projects = []
            for name in project_names:
                project = await repository.get_by_name(session, name)
                if project is None:
                    raise ValueError(f"Project not found: '{name}'")
                projects.append(project)

        reports: list[ProjectDoctorReport] = []
        for project in projects:
            entities = EntityRepository(project_id=project.id)
            violations = ViolationRepository(project_id=project.id)

            integrity = ProjectIntegrityReport()
            if include_integrity:
                integrity = ProjectIntegrityReport(
                    unresolved=list(
                        await RelationRepository(
                            project_id=project.id
                        ).find_unresolved_relation_report(session)
                    ),
                    permalink_issues=await entities.find_permalink_integrity_issues(session),
                    missing_files=_missing_files(
                        Path(project.path), await entities.list_indexed_files(session)
                    ),
                    errors=await violations.list_for_project(session, project.id, severity="error"),
                )

            # The doctor is the gardener (GAPS W2), so U39 runs here as well as
            # on the notice path: an untouched default snapshot is upgraded
            # before the hygiene report reads it, and a hand-edited file that
            # lags the defaults becomes one hygiene line. Unreadable files are
            # the revalidation pass's to report; here they just skip the check.
            vocabulary_upgraded = False
            vocabulary_line: str | None = None
            if include_hygiene:
                from basic_memory.vocabulary.model import (
                    VocabularyError,
                    defaults_delta,
                    load_vocabulary,
                    matches_superseded_defaults,
                    upgrade_snapshot_vocabulary,
                )

                try:
                    vocabulary = load_vocabulary(project.external_id)
                except VocabularyError:
                    vocabulary = None
                if vocabulary is not None:
                    if matches_superseded_defaults(vocabulary):
                        # False = already canonical; only a real rewrite is news.
                        vocabulary_upgraded = upgrade_snapshot_vocabulary(project.external_id)
                    else:
                        delta = defaults_delta(vocabulary)
                        if not delta.empty:
                            vocabulary_line = (
                                f"vocabulary predates current defaults: missing "
                                f"{delta.describe()} — add them to vocabulary.yml or run "
                                f"'bm project vocab-sync {project.name}'"
                            )

            hygiene = ProjectHygieneReport()
            if include_hygiene:
                hygiene = ProjectHygieneReport(
                    review_due=await entities.find_review_due_records(session, review_cutoff),
                    inferred_dates=await entities.find_inferred_date_records(session),
                    stale_states=await entities.find_stale_state_records(session, stale_before),
                    stale_doing=await entities.find_stale_doing_records(
                        session, doing_stale_before, now
                    ),
                    inbox=await entities.find_inbox_records(session),
                    advisories=await violations.list_for_project(
                        session, project.id, severity="advisory"
                    ),
                    vocabulary_outdated=vocabulary_line,
                )

            reports.append(
                ProjectDoctorReport(
                    project_name=project.name,
                    integrity=integrity,
                    hygiene=hygiene,
                    vocabulary_upgraded=vocabulary_upgraded,
                )
            )
        return reports


# --- Records: what `bm ls`, `bm show` and `bm path` read ---
#
# Identity is verified, never inferred (GAPS T9/T10, `.forked/schema.md` §8):
# BM's resolver legitimately matches on title and file path, so a row that came
# back is not by itself the row that was asked for.

# The relation that carries supersession. One direction only: the successor owns
# the edge and the predecessor is never touched (`.forked/schema.md` §5).
SUPERSEDES = SUPERSEDES_RELATION

# What a record with no value in a column prints. A blank would make the columns
# ambiguous to read; a sentinel that looks like data would be worse.
NO_VALUE = "-"

# What the status column reads for a record some other record supersedes
# (GAPS U3). It goes in the status column rather than in a new one because that
# column is where a reader looks to ask "is this row still live", and a superseded
# record is not — whatever else it says. `bm ls` printed the same blank column for
# a dead finding as for a live one, and the only way to tell them apart was to
# read every other finding's body looking for a relation naming this one.
#
# The marker wins over a declared status when a record somehow carries both. That
# is unreachable in a governed project — only a finding may be superseded and a
# finding may not carry a status — and where it is reachable, "not live" is the
# more important of the two facts. `bm show` prints the status and the successor
# both, which is where the detail belongs.
SUPERSEDED = "superseded"


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
class BoardRow:
    """One task line of the bare-`bm` board (GAPS U37)."""

    record_id: str
    status: str
    title: str


@dataclass(frozen=True, slots=True)
class BoardData:
    """What the board prints for one project: live tasks and the two counts."""

    project: str
    external_id: str
    rows: list[BoardRow]
    shelved: int
    inbox: int


@dataclass(frozen=True, slots=True)
class Supersession:
    """A successor record that supersedes the one being shown."""

    record_id: str
    event_date: str

    def describe(self) -> str:
        when = f" ({self.event_date})" if self.event_date else ""
        return f"superseded by {self.record_id}{when}"


@dataclass(frozen=True, slots=True)
class IncomingReference:
    """One record that points at the record being shown (GAPS U32).

    The hn-app audit's gap: a correction carries `derived_from` to the record it
    overturns, but the stale record shows nothing — a reader landing on it via
    search sees a confidently wrong claim with no pointer forward. The incoming
    edge is the pointer, so `bm show` renders it from this row.

    `status` is set only when the pointing record has a lifecycle (task or
    plan, GAPS U38): a plan shown from one of its stages should say where the
    stage stands, and a statusless type has nothing honest to print there.
    """

    relation_type: str
    record_id: str
    title: str
    status: str | None = None

    def describe(self) -> str:
        # The title is context, not payload — cut it before it becomes a second
        # record body inside the first one's output.
        title = self.title if len(self.title) <= 60 else self.title[:59].rstrip() + "…"
        stamped = f" ({self.status})" if self.status else ""
        return f'← {self.relation_type} by {self.record_id}{stamped} "{title}"'


@dataclass(frozen=True, slots=True)
class OutgoingReference:
    """One lifecycle record the shown record points at (GAPS U38).

    Only task and plan targets earn a row: the block exists so a plan's stage
    list reads as a live checklist, and a reference without a status would be
    noise under it. Ordered by relation insertion, which follows the body's own
    top-to-bottom order — the checklist prints in the order the plan states.
    """

    relation_type: str
    record_id: str
    title: str
    status: str

    def describe(self) -> str:
        title = self.title if len(self.title) <= 60 else self.title[:59].rstrip() + "…"
        return f'→ {self.record_id} ({self.status}) "{title}"'


@dataclass(frozen=True, slots=True)
class ResolvedRecord:
    """One record, located: which project holds it, where its file is, and its successors."""

    project: str
    record_id: str
    path: Path
    superseded_by: tuple[Supersession, ...] = ()
    referenced_by: tuple[IncomingReference, ...] = ()
    references: tuple[OutgoingReference, ...] = ()


async def projects_in_scope(session: "AsyncSession", project_name: str | None) -> list["Project"]:
    """Every registered project, or the one named. Ordered by id, which is stable.

    Session-taking on purpose: the CLI wrappers below open a session per call,
    but `bm web` holds one open for the life of a request and passes it in, so
    the scoping rule has one implementation rather than two.

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


async def direct_record_listing(
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
    await ensure_project_registry(config, bootstrap=False)

    async with db.scoped_session(session_maker) as session:
        projects = await projects_in_scope(session, project_name)
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
                status=SUPERSEDED if row.superseded else (row.status or NO_VALUE),
                title=row.title,
            )
            for row in kept
        ],
        truncated=truncated,
    )


async def direct_board(project_name: str) -> BoardData:
    """Gather what bare `bm` prints: live tasks plus the parked and inbox counts.

    The board is always pinned — a "what's open here" view has no meaning as an
    all-projects roll-up, so the caller resolves the project before asking
    (GAPS U37). Live means neither terminal nor parked, and a task with no
    status counts as open for the same reason `bm brief` counts it: hiding open
    work over a schema fault the notice already reports would be a second,
    silent penalty.
    """
    # Deferred: the service layer pulls SQLAlchemy, which must not load at CLI
    # import time — only when a command actually runs (#886).
    from sqlalchemy import case, func, or_, select

    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.models.knowledge import Entity
    from basic_memory.services.initialization import ensure_project_registry
    from basic_memory.vocabulary.model import PARKED_STATUSES, inactive_statuses

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config, bootstrap=False)

    async with db.scoped_session(session_maker) as session:
        (project,) = await projects_in_scope(session, project_name)

        status = Entity.entity_metadata["status"].as_string()
        is_live = or_(status.is_(None), status.not_in(sorted(inactive_statuses())))

        # Working order, not alphabetical: what is in motion, then what is
        # stuck, then what is waiting — recency breaking ties within each.
        # Plans ride inline with tasks (GAPS U38): the `plan-` id prefix labels
        # them, and within a status rank a plan sorts first because it is the
        # higher-altitude item its stages roll up into.
        rank = case((status == "doing", 0), (status == "blocked", 1), else_=2)
        altitude = case((Entity.note_type == "plan", 0), else_=1)
        found = await session.execute(
            select(Entity.permalink, status, Entity.title)
            .where(
                Entity.project_id == project.id,
                Entity.note_type.in_(["task", "plan"]),
                is_live,
            )
            .order_by(rank, altitude, Entity.updated_at.desc())
        )
        rows = [
            BoardRow(record_id=permalink, status=row_status or "open", title=title)
            for permalink, row_status, title in found
        ]

        async def count(*conditions) -> int:
            return (
                await session.scalar(
                    select(func.count())
                    .select_from(Entity)
                    .where(Entity.project_id == project.id, *conditions)
                )
            ) or 0

        shelved = await count(
            Entity.note_type.in_(["task", "plan"]), status.in_(sorted(PARKED_STATUSES))
        )
        inbox = await count(Entity.note_type == "inbox")

    return BoardData(
        project=project.name,
        external_id=project.external_id,
        rows=rows,
        shelved=shelved,
        inbox=inbox,
    )


async def resolve_record(
    session: "AsyncSession", project_name: str | None, record_id: str
) -> ResolvedRecord:
    """Locate one record by id on a caller-owned session, verifying identity.

    Session-taking so the CLI wrapper and `bm web` share one resolver: the web
    server holds a session open for the whole request and must not open a second
    one inside it — the pool holds a single connection.

    Raises RecordNotFound when no project in scope holds that permalink, and
    AmbiguousRecord when an unscoped lookup finds it in more than one.
    """
    from basic_memory.repository.entity_repository import EntityRepository

    found: list[ResolvedRecord] = []
    for project in await projects_in_scope(session, project_name):
        entity = await EntityRepository(project_id=project.id).get_by_permalink(session, record_id)
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
                referenced_by=_incoming_references(entity),
                references=_outgoing_references(entity),
            )
        )

    if not found:
        raise RecordNotFound(record_id)
    if len(found) > 1:
        raise AmbiguousRecord(", ".join(sorted(record.project for record in found)))
    return found[0]


async def direct_record(project_name: str | None, record_id: str) -> ResolvedRecord:
    """Locate one record by id, verifying identity (GAPS T9/T10).

    Raises RecordNotFound when no project in scope holds that permalink, and
    AmbiguousRecord when an unscoped lookup finds it in more than one.
    """
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.services.initialization import ensure_project_registry

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config, bootstrap=False)

    async with db.scoped_session(session_maker) as session:
        return await resolve_record(session, project_name, record_id)


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


# The types whose records carry a status (GAPS U38). Shared by both reference
# builders so "which references get a stamp" has one answer.
_LIFECYCLE_TYPES: frozenset[str] = frozenset({"task", "plan"})


def _lifecycle_status(entity: "Entity | None") -> str | None:
    """The status stamp a reference to ``entity`` earns, or None for no stamp.

    A lifecycle record with no status is stamped `open`, the same reading brief
    and the board give it: hiding it over a schema fault would misreport the
    checklist this exists to render.
    """
    if entity is None or entity.note_type not in _LIFECYCLE_TYPES:
        return None
    metadata = entity.entity_metadata or {}
    status = metadata.get("status")
    return str(status) if status else "open"


def _outgoing_references(entity: "Entity") -> tuple[OutgoingReference, ...]:
    """The lifecycle records ``entity`` points at, in body order (GAPS U38).

    Relation rows are read in insertion order, which follows the body top to
    bottom — a plan's stage list therefore prints in the plan's own order, not
    alphabetically. Only task/plan targets appear: the block is a checklist,
    and a row with no status has nothing to check.
    """
    references = []
    seen: set[str] = set()
    for relation in entity.outgoing_relations:
        target = relation.to_entity
        status = _lifecycle_status(target)
        if status is None or target is None or not target.permalink:
            continue
        # One row per target: the body may link a stage twice (a wikilink and a
        # `--rel part_of`), and the checklist needs each stage once.
        if target.permalink in seen:
            continue
        seen.add(target.permalink)
        references.append(
            OutgoingReference(
                relation_type=relation.relation_type,
                record_id=target.permalink,
                title=target.title or "",
                status=status,
            )
        )
    return tuple(references)


def _incoming_references(entity: "Entity") -> tuple[IncomingReference, ...]:
    """Every other record pointing at ``entity``, minus supersessions (GAPS U32).

    Supersession keeps its own richer rendering above — repeating it here would
    print the same edge twice. Read from the same eagerly-loaded collection
    `_supersessions` uses, never from a rescan of files. Sorted by relation type
    then id, so an unchanged graph renders the same block twice.
    """
    references = [
        IncomingReference(
            relation_type=relation.relation_type,
            record_id=relation.from_entity.permalink,
            title=relation.from_entity.title or "",
            status=_lifecycle_status(relation.from_entity),
        )
        for relation in entity.incoming_relations
        if relation.relation_type != SUPERSEDES
        and relation.from_entity is not None
        and relation.from_entity.permalink
    ]
    return tuple(sorted(references, key=lambda item: (item.relation_type, item.record_id)))
