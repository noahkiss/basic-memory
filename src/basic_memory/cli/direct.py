"""Direct service-layer access for fast native CLI commands.

Native commands must talk to the repository/service layer directly and must
not reach through the MCP tool layer or the in-process FastAPI app: importing
either costs seconds of startup and ~100 MB of memory (see AGENTS.md,
"Measured baseline", and GAPS.md T18). This module is the supported way for a
native command to get a service object — it wires config → database →
repository → service with none of the API/MCP import graph on the path.

The boundary is guarded structurally: a test runs a native command in a
subprocess and asserts that ``basic_memory.api.app``, ``basic_memory.mcp.tools``,
``fastapi``, and ``dateparser`` never enter ``sys.modules``.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from basic_memory.repository.entity_repository import (
        HygieneRecord,
        PermalinkIntegrityIssue,
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
    await ensure_project_registry(config)
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
    await ensure_project_registry(config)
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


async def direct_revalidate_vocabulary(project_name: str | None = None) -> int:
    """Re-check records whose project's vocabulary changed. Return how many were checked.

    ``project_name`` pins one project; ``None`` covers every project in the
    registry, which is what an unscoped read means under GAPS W5-C. Both shapes
    exist because the notice that consumes this reads either one count or a
    roll-up depending on whether the cwd is marked.

    This is the trigger's only caller for now, and it is deliberately not wired
    into a scan or sync pass: violations go stale when the *vocabulary* changes,
    not when files do, so firing on sync would leave counts wrong for anyone who
    never syncs and right only by accident for everyone else. Reporting is
    GAPS W5 item 6's; this returns a number and prints nothing.

    Raises ValueError for an unknown project name, and ``VocabularyError`` for a
    malformed ``vocabulary.yml`` — an unreadable vocabulary must not degrade into
    "not governed" (GAPS W4).
    """
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.services.initialization import ensure_project_registry
    from basic_memory.services.vocabulary_revalidation import revalidate_if_vocabulary_changed

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config)
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
        for registered in projects:
            revalidated += await revalidate_if_vocabulary_changed(session, registered)
        return revalidated


# How long a `state` record may sit untouched before doctor asks about it.
# `vocabulary.yml` declares no staleness key — its allowed keys are types,
# statuses, areas, review_months and fields — so the number is fixed here and
# the report prints it, rather than being a threshold nobody can see.
STALE_STATE_DAYS = 30


@dataclass(frozen=True, slots=True)
class ProjectIntegrityReport:
    """One project's integrity findings: the checks that have right answers."""

    unresolved: "list[UnresolvedRelationReportRow]" = field(default_factory=list)
    permalink_issues: "list[PermalinkIntegrityIssue]" = field(default_factory=list)
    errors: "list[ViolationRow]" = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return len(self.unresolved) + len(self.permalink_issues) + len(self.errors)


@dataclass(frozen=True, slots=True)
class ProjectHygieneReport:
    """One project's hygiene findings: the checks that need a person."""

    review_due: "list[HygieneRecord]" = field(default_factory=list)
    inferred_dates: "list[HygieneRecord]" = field(default_factory=list)
    stale_states: "list[HygieneRecord]" = field(default_factory=list)
    inbox: "list[HygieneRecord]" = field(default_factory=list)
    advisories: "list[ViolationRow]" = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return (
            len(self.review_due)
            + len(self.inferred_dates)
            + len(self.stale_states)
            + len(self.inbox)
            + len(self.advisories)
        )


@dataclass(frozen=True, slots=True)
class ProjectDoctorReport:
    """Everything ``bm doctor`` prints about one project."""

    project_name: str
    integrity: ProjectIntegrityReport = field(default_factory=ProjectIntegrityReport)
    hygiene: ProjectHygieneReport = field(default_factory=ProjectHygieneReport)


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
    await ensure_project_registry(config)

    review_cutoff = today if today is not None else date.today()
    # Local-aware, because that is how the rows were stamped: SQLite keeps the
    # wall clock and drops the offset, so an aware UTC bound would compare
    # against local wall times and shift the window by the offset.
    stale_before = datetime.now().astimezone() - timedelta(days=STALE_STATE_DAYS)

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
                    errors=await violations.list_for_project(session, project.id, severity="error"),
                )

            hygiene = ProjectHygieneReport()
            if include_hygiene:
                hygiene = ProjectHygieneReport(
                    review_due=await entities.find_review_due_records(session, review_cutoff),
                    inferred_dates=await entities.find_inferred_date_records(session),
                    stale_states=await entities.find_stale_state_records(session, stale_before),
                    inbox=await entities.find_inbox_records(session),
                    advisories=await violations.list_for_project(
                        session, project.id, severity="advisory"
                    ),
                )

            reports.append(
                ProjectDoctorReport(
                    project_name=project.name,
                    integrity=integrity,
                    hygiene=hygiene,
                )
            )
        return reports
