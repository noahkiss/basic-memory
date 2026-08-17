"""Common test fixtures."""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from basic_memory import db
from basic_memory.config import (
    ProjectConfig,
    BasicMemoryConfig,
    ConfigManager,
)
from basic_memory.config_models import default_fastembed_cache_dir
from basic_memory.db import DatabaseType
from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.index.watch_service import WatchService
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Base
from basic_memory.models.knowledge import Entity
from basic_memory.models.project import Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services import (
    EntityService,
    ProjectService,
)
from basic_memory.services.directory_service import DirectoryService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService

# Resolved once at import time, while HOME is still the real one. Every fixture
# that redirects HOME runs later, so this is the only point where the host's
# own cache location is observable.
_HOST_FASTEMBED_CACHE = default_fastembed_cache_dir()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_routing_env(monkeypatch) -> None:
    """Prevent command-routing env flags from leaking across tests."""
    monkeypatch.delenv("BASIC_MEMORY_FORCE_LOCAL", raising=False)
    monkeypatch.delenv("BASIC_MEMORY_FORCE_CLOUD", raising=False)
    monkeypatch.delenv("BASIC_MEMORY_EXPLICIT_ROUTING", raising=False)


@pytest.fixture(autouse=True)
def isolate_data_dir_env(monkeypatch) -> None:
    """Keep host data-dir env vars from leaking into tests.

    Why: GitHub Actions Ubuntu runners set ``XDG_CONFIG_HOME=/home/runner/.config``,
    and ``resolve_data_dir()`` honors it ahead of ``Path.home() / ".basic-memory"``.
    Without clearing it, tests that monkeypatch HOME still see the host XDG path
    and assertions against the tmp home directory fail.
    """
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    # Trigger: config_home redirects HOME at tmp_path, so the shared XDG cache
    # resolves to <tmp>/.cache/fastembed and a semantic test re-downloads the
    # 64 MB embedding model into a directory that dies with the run.
    # Why: the model is an immutable artifact keyed by model name (GAPS S1) —
    # it was deliberately moved outside the isolation boundary these fixtures
    # draw, and re-isolating it here undoes that fix.
    # Outcome: pin the real user cache, captured at import time before any
    # fixture patches HOME. Tests that exercise the resolution order clear this
    # variable themselves.
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", _HOST_FASTEMBED_CACHE)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_global_db_after_test() -> AsyncGenerator[None, None]:
    """Close any module-level DB engine created outside fixture ownership."""
    yield

    # Trigger: a test exercises production fallback routing instead of the
    # per-test engine fixture.
    # Why: that path stores an engine in basic_memory.db module state, and
    # a later fixture can overwrite the reference before it is disposed.
    # Outcome: close straggler aiosqlite worker threads before the loop closes.
    await db.shutdown_db()


@pytest.fixture
def logged_warnings() -> Generator[list[str], None, None]:
    """Collect loguru warnings for the duration of one test.

    The vocabulary funnel reports in record mode by logging, and loguru does not
    feed pytest's ``caplog``. A sink is the only way to read what it said. Shared
    because two paths record: the sync path and the watcher's move path (GAPS
    T22, GAPS T23).
    """
    collected: list[str] = []
    sink_id = logger.add(collected.append, level="WARNING")
    try:
        yield collected
    finally:
        logger.remove(sink_id)


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def config_home(tmp_path, monkeypatch) -> Path:
    # Patch HOME environment variable for the duration of the test
    monkeypatch.setenv("HOME", str(tmp_path))
    # On Windows, also set USERPROFILE
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Set BASIC_MEMORY_HOME to the test directory
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    return tmp_path


def write_project_registry(projects: dict[str, str], default: str | None = None) -> Path:
    """Write the on-disk project registry the synchronous reader sees.

    The database owns the project registry (GAPS B2), and CLI-boundary code
    reads it synchronously through ``basic_memory.project_registry``, which
    opens the SQLite file directly. Unit tests run against an in-memory
    database, so tests that exercise that reader materialize a real file here.

    The destination is resolved when this is *called*, not when a fixture is
    set up, so it always lands in whatever data dir the test's patched
    environment currently names.
    """
    import sqlite3

    from basic_memory.config_models import DATABASE_NAME, resolve_data_dir
    from basic_memory.utils import generate_permalink

    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    database_path = data_dir / DATABASE_NAME

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS project ("
            "id INTEGER PRIMARY KEY, name TEXT, permalink TEXT, path TEXT, "
            "is_active INTEGER, is_default INTEGER)"
        )
        connection.execute("DELETE FROM project")
        for name, path in projects.items():
            connection.execute(
                "INSERT INTO project (name, permalink, path, is_active, is_default) "
                "VALUES (?, ?, ?, 1, ?)",
                (name, generate_permalink(name), path, 1 if name == default else None),
            )
        connection.commit()
    finally:
        connection.close()
    return database_path


@pytest.fixture
def write_registry_file(config_home):
    """Fixture wrapper around :func:`write_project_registry`.

    Depends on ``config_home`` so HOME is already redirected at the tmp dir
    before anything can be written — the writer resolves its destination from
    the environment, and an unpatched HOME would target the real data dir.
    """
    return write_project_registry


@pytest.fixture(scope="function")
def app_config(config_home, monkeypatch) -> BasicMemoryConfig:
    """Create test app configuration."""
    app_config = BasicMemoryConfig(
        env="test",
        update_permalinks_on_move=True,
        # Trigger: semantic_search_enabled defaults to True whenever fastembed/sqlite-vec
        #          are importable, which they are in dev and CI environments.
        # Why: with it on, every test that syncs pays the ONNX embedding stack (~5-7s per
        #      sync) — embeddings are covered by the dedicated semantic suites, which
        #      configure semantic_search_enabled explicitly themselves.
        # Outcome: non-semantic tests skip embedding work entirely.
        semantic_search_enabled=False,
    )

    return app_config


@pytest.fixture
def config_manager(app_config: BasicMemoryConfig, config_home: Path, monkeypatch) -> ConfigManager:
    # Invalidate config cache to ensure clean state for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    # Create a new ConfigManager that uses the test home directory
    config_manager = ConfigManager()
    # Update its paths to use the test directory
    config_manager.config_dir = config_home / ".basic-memory"
    config_manager.config_file = config_manager.config_dir / "config.json"
    config_manager.config_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the config file is written to disk
    config_manager.save_config(app_config)
    return config_manager


@pytest.fixture(scope="function")
def project_config(test_project):
    """Create test project configuration."""

    project_config = ProjectConfig(
        name=test_project.name,
        home=Path(test_project.path),
    )

    return project_config


@dataclass
class TestConfig:
    config_home: Path
    project_config: ProjectConfig
    app_config: BasicMemoryConfig
    config_manager: ConfigManager


@pytest.fixture
def test_config(config_home, project_config, app_config, config_manager) -> TestConfig:
    """All test configuration fixtures"""
    return TestConfig(config_home, project_config, app_config, config_manager)


@pytest_asyncio.fixture(scope="function")
async def engine_factory(
    app_config,
    config_manager,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """In-memory SQLite engine and session maker for tests."""
    from basic_memory.models.search import (
        CREATE_SEARCH_INDEX,
        CREATE_SQLITE_SEARCH_VECTOR_CHUNKS,
        CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY,
        CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE,
    )

    db_type = DatabaseType.MEMORY
    async with db.engine_session_factory(db_path=app_config.database_path, db_type=db_type) as (
        engine,
        session_maker,
    ):
        # Create all tables via ORM, then add search_index via FTS5 DDL
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(CREATE_SEARCH_INDEX)
            await conn.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS)
            await conn.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY)
            await conn.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE)

        # Yield after setup is complete
        yield engine, session_maker


@pytest_asyncio.fixture
async def session_maker(engine_factory) -> async_sessionmaker[AsyncSession]:
    """Get session maker for tests."""
    _, session_maker = engine_factory
    return session_maker


## Repositories


@pytest_asyncio.fixture(scope="function")
async def entity_repository(
    session_maker: async_sessionmaker[AsyncSession], test_project: Project
) -> EntityRepository:
    """Create an EntityRepository instance with project context."""
    return EntityRepository(project_id=test_project.id)


@pytest_asyncio.fixture(scope="function")
async def observation_repository(
    session_maker: async_sessionmaker[AsyncSession], test_project: Project
) -> ObservationRepository:
    """Create an ObservationRepository instance with project context."""
    return ObservationRepository(project_id=test_project.id)


@pytest_asyncio.fixture(scope="function")
async def relation_repository(
    session_maker: async_sessionmaker[AsyncSession], test_project: Project
) -> RelationRepository:
    """Create a RelationRepository instance with project context."""
    return RelationRepository(project_id=test_project.id)


@pytest_asyncio.fixture(scope="function")
async def project_repository(
    session_maker: async_sessionmaker[AsyncSession],
) -> ProjectRepository:
    """Create a ProjectRepository instance."""
    return ProjectRepository()


@pytest_asyncio.fixture(scope="function")
async def test_project(config_home, engine_factory) -> Project:
    """Create a test project to be used as context for other repositories."""
    project_data = {
        "name": "test-project",
        "description": "Project used as context for tests",
        "path": str(config_home),
        "is_active": True,
        "is_default": True,  # Explicitly set as the default project (for cli operations)
    }
    engine, session_maker = engine_factory
    project_repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        project = await project_repository.create(session, project_data)
    return project


## Services


@pytest_asyncio.fixture
async def entity_service(
    entity_repository: EntityRepository,
    observation_repository: ObservationRepository,
    relation_repository: RelationRepository,
    entity_parser: EntityParser,
    file_service: FileService,
    link_resolver: LinkResolver,
    app_config: BasicMemoryConfig,
    session_maker: async_sessionmaker[AsyncSession],
) -> EntityService:
    """Create EntityService."""
    return EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config,
        session_maker=session_maker,
    )


@pytest.fixture
def file_service(
    project_config: ProjectConfig, markdown_processor: MarkdownProcessor
) -> FileService:
    """Create FileService instance."""
    return FileService(project_config.home, markdown_processor)


@pytest.fixture
def markdown_processor(entity_parser: EntityParser) -> MarkdownProcessor:
    """Create writer instance."""
    return MarkdownProcessor(entity_parser)


@pytest.fixture
def link_resolver(
    entity_repository: EntityRepository,
    search_service: SearchService,
    session_maker: async_sessionmaker[AsyncSession],
    app_config: BasicMemoryConfig,
):
    """Create parser instance."""
    return LinkResolver(
        entity_repository, search_service, session_maker=session_maker, app_config=app_config
    )


@pytest.fixture
def entity_parser(project_config):
    """Create parser instance."""
    return EntityParser(project_config.home)


@pytest_asyncio.fixture
async def directory_service(
    entity_repository,
    project_config,
    session_maker: async_sessionmaker[AsyncSession],
) -> DirectoryService:
    """Create directory service for testing."""
    return DirectoryService(
        entity_repository=entity_repository,
        session_maker=session_maker,
    )


@pytest_asyncio.fixture
async def search_repository(session_maker, test_project: Project, app_config: BasicMemoryConfig):
    """Create a SearchRepository instance with project context"""
    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

    return SQLiteSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config,
    )


@pytest_asyncio.fixture
async def search_service(
    search_repository,
    entity_repository: EntityRepository,
    file_service: FileService,
    session_maker: async_sessionmaker[AsyncSession],
) -> SearchService:
    """Create and initialize search service"""
    service = SearchService(
        search_repository,
        entity_repository,
        file_service,
        session_maker=session_maker,
    )
    await service.init_search_index()
    return service


@pytest_asyncio.fixture(scope="function")
async def sample_entity(
    entity_repository: EntityRepository,
    session_maker: async_sessionmaker[AsyncSession],
) -> Entity:
    """Create a sample entity for testing."""
    entity_data = {
        "project_id": entity_repository.project_id,
        "title": "Test Entity",
        "note_type": "test",
        "permalink": "test/test-entity",
        "file_path": "test/test_entity.md",
        "content_type": "text/markdown",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    async with db.scoped_session(session_maker) as session:
        return await entity_repository.create(session, entity_data)


@pytest_asyncio.fixture
async def project_service(
    project_repository: ProjectRepository,
    file_service: FileService,
    session_maker: async_sessionmaker[AsyncSession],
) -> ProjectService:
    """Create ProjectService with repository and file service for directory operations."""
    return ProjectService(
        repository=project_repository,
        file_service=file_service,
        session_maker=session_maker,
    )


@pytest_asyncio.fixture
async def full_entity(sample_entity, entity_repository, file_service, entity_service) -> Entity:
    """Create a search test entity."""

    # Create test entity
    entity, created = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Search_Entity",
            directory="test",
            note_type="test",
            content=dedent("""
                ## Observations
                - [tech] Tech note
                - [design] Design note

                ## Relations
                - out1 [[Test Entity]]
                - out2 [[Test Entity]]
                """),
        )
    )
    return entity


@pytest_asyncio.fixture
async def test_graph(
    entity_repository,
    relation_repository,
    observation_repository,
    search_service,
    file_service,
    entity_service,
    session_maker,
):
    """Create a test knowledge graph with entities, relations and observations."""

    # Create some test entities in reverse order so they will be linked
    deeper, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Deeper Entity",
            note_type="deeper",
            directory="test",
            content=dedent("""
                # Deeper Entity
                """),
        )
    )

    deep, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Deep Entity",
            note_type="deep",
            directory="test",
            content=dedent("""
                # Deep Entity
                - deeper_connection [[Deeper Entity]]
                """),
        )
    )

    connected_2, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Connected Entity 2",
            note_type="test",
            directory="test",
            content=dedent("""
                # Connected Entity 2
                - deep_connection [[Deep Entity]]
                """),
        )
    )

    connected_1, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Connected Entity 1",
            note_type="test",
            directory="test",
            content=dedent("""
                # Connected Entity 1
                - [note] Connected 1 note
                - connected_to [[Connected Entity 2]]
                """),
        )
    )

    root, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Root",
            note_type="test",
            directory="test",
            content=dedent("""
                # Root Entity
                - [note] Root note 1
                - [tech] Root tech note
                - connects_to [[Connected Entity 1]]
                """),
        )
    )

    # get latest
    async with db.scoped_session(session_maker) as session:
        entities = await entity_repository.find_all(session)
        relations = await relation_repository.find_all(session)

    # Index everything for search
    for entity in entities:
        await search_service.index_entity(entity)

    return {
        "root": root,
        "connected1": connected_1,
        "connected2": connected_2,
        "deep": deep,
        "observations": [e.observations for e in entities],
        "relations": relations,
    }


@pytest.fixture
def watch_service(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker: async_sessionmaker[AsyncSession],
) -> WatchService:
    """Create the event-index local watcher for tests."""
    return WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )


@pytest.fixture
def test_files(project_config, project_root) -> dict[str, Path]:
    """Copy test files into the project directory.

    Returns a dict mapping file names to their paths in the project dir.
    """
    # Source files relative to tests directory
    source_files = {
        "pdf": Path(project_root / "tests/Non-MarkdownFileSupport.pdf"),
        "image": Path(project_root / "tests/Screenshot.png"),
    }

    # Create copies in temp project directory
    project_files = {}
    for name, src_path in source_files.items():
        # Read source file
        content = src_path.read_bytes()

        # Create destination path and ensure parent dirs exist
        dest_path = project_config.home / src_path.name
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        dest_path.write_bytes(content)
        project_files[name] = dest_path

    return project_files


@pytest_asyncio.fixture
async def indexed_files(project_repository, session_maker, test_project, test_files):
    """Index copied fixture files through the local project-index runner."""
    runner = LocalProjectIndexRunner(
        project_repository=project_repository,
        session_maker=session_maker,
    )
    await runner.index_project(test_project.id, force_full=True)
    return test_files
