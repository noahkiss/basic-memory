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

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from basic_memory.repository.entity_repository import PermalinkIntegrityIssue
    from basic_memory.repository.relation_repository import UnresolvedRelationReportRow
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


async def direct_project_ref(project_name: str | None) -> ProjectRef:
    """Resolve a project to its name and ``external_id``, the store dir it owns.

    ``store/<external_id>/`` is where a project's ``vocabulary.yml`` lives (GAPS
    W4, decided 2026-08-10), so any verb that reads the vocabulary needs this id.

    ``project_name`` is None when the CLI chain found nothing to resolve — which
    on a fresh install means the registry did not exist yet when the chain ran.
    ``ensure_project_registry`` below creates it, so the default is asked for
    here rather than before bootstrap, where the answer is always None.

    Raises ValueError for an unknown project, and for a registry with no default:
    an unaddressable request is a failure, not an empty result (contract rule 5).
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
            project = await repository.get_default_project(session)
            if project is None:
                raise ValueError("No default project is set")
        else:
            project = await repository.get_by_name(session, project_name)
            if project is None:
                raise ValueError(f"Project not found: '{project_name}'")
        return ProjectRef(name=project.name, external_id=project.external_id)


async def direct_corpus_integrity_report(
    project_name: str,
) -> "tuple[list[UnresolvedRelationReportRow], list[PermalinkIntegrityIssue]]":
    """Fetch a project's dangling forward references and permalink-invariant issues.

    Raises ValueError for an unknown project so the CLI can fail loudly instead
    of reporting a clean corpus for a name that resolves to nothing.
    """
    # Deferred: the service layer pulls SQLAlchemy + Alembic, which must not
    # load at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.repository.relation_repository import RelationRepository
    from basic_memory.services.initialization import ensure_project_registry

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config)
    async with db.scoped_session(session_maker) as session:
        project = await ProjectRepository().get_by_name(session, project_name)
        if project is None:
            raise ValueError(f"Project not found: '{project_name}'")
        unresolved = await RelationRepository(
            project_id=project.id
        ).find_unresolved_relation_report(session)
        permalink_issues = await EntityRepository(
            project_id=project.id
        ).find_permalink_integrity_issues(session)
        return unresolved, permalink_issues
