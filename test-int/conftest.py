"""
Shared fixtures for integration tests.

Integration tests verify the complete flow: MCP Client → MCP Server → FastAPI → Database.
Unlike unit tests which use in-memory databases and mocks, integration tests use real SQLite
files and test the full application stack to ensure all components work together correctly.

## Architecture

The integration test setup creates this flow:

```
Test → MCP Client → MCP Server → HTTP Request (ASGITransport) → FastAPI App → Database
                                                                      ↑
                                                               Dependency overrides
                                                               point to test database
```

## Key Components

1. **Real SQLite Database**: Uses `DatabaseType.FILESYSTEM` with actual SQLite files
   in temporary directories instead of in-memory databases.

2. **Shared Database Connection**: Both MCP server and FastAPI app use the same
   database via dependency injection overrides.

3. **Project Session Management**: Initializes the MCP project session with test
   project configuration so tools know which project to operate on.

4. **Search Index Initialization**: Creates the FTS5 search index tables that
   the application requires for search functionality.

5. **Global Configuration Override**: Modifies the global `basic_memory_app_config`
   so MCP tools use test project settings instead of user configuration.

## Usage

Integration tests should include both `mcp_server` and `app` fixtures to ensure
the complete stack is wired correctly:

```python
@pytest.mark.asyncio
async def test_my_mcp_tool(mcp_server, app):
    async with Client(mcp_server) as client:
        result = await client.call_tool("tool_name", {"param": "value"})
        # Assert on results...
```

The `app` fixture ensures FastAPI dependency overrides are active, and
`mcp_server` provides the MCP server with proper project session initialization.
"""

import os
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy import text

from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import AsyncClient, ASGITransport

import basic_memory

from basic_memory.config import (
    BasicMemoryConfig,
    ProjectConfig,
    ConfigManager,
)
from basic_memory.db import engine_session_factory, DatabaseType
from basic_memory.models import Project
from basic_memory.models.base import Base
from basic_memory.repository.project_repository import ProjectRepository
from fastapi import FastAPI

from basic_memory.deps import get_engine_factory, get_app_config


# Import MCP tools so they're available for testing
from basic_memory.mcp import tools  # noqa: F401
from basic_memory.config_models import default_fastembed_cache_dir

# Resolved once at import time, while HOME is still the real one. Every fixture
# that redirects HOME runs later, so this is the only point where the host's
# own cache location is observable.
_HOST_FASTEMBED_CACHE = default_fastembed_cache_dir()


@pytest_asyncio.fixture(autouse=True)
async def cleanup_global_db_after_test() -> AsyncGenerator[None, None]:
    """Close any module-level DB engine created outside fixture ownership."""
    yield

    # Trigger: integration tests invoke CLI/MCP routes through the production
    # client fallback, bypassing this file's engine_factory fixture.
    # Why: those fallback engines live in basic_memory.db module state and can
    # otherwise leave a non-daemon aiosqlite worker alive after pytest finishes.
    # Outcome: every test boundary becomes a cleanup point for fallback engines.
    from basic_memory import db

    await db.shutdown_db()


@pytest.fixture(autouse=True)
def clean_routing_env(monkeypatch) -> None:
    """Keep CLI routing env mutations from leaking between integration tests."""
    # Trigger: CLI integration tests exercise long-running MCP entrypoints that set routing env.
    # Why: those commands normally own the process lifetime, but pytest keeps reusing it.
    # Outcome: every integration test starts from neutral routing unless it opts in explicitly.
    monkeypatch.delenv("BASIC_MEMORY_FORCE_LOCAL", raising=False)
    monkeypatch.delenv("BASIC_MEMORY_FORCE_CLOUD", raising=False)
    monkeypatch.delenv("BASIC_MEMORY_EXPLICIT_ROUTING", raising=False)


@pytest.fixture(autouse=True)
def isolate_data_dir_env(monkeypatch) -> None:
    """Keep host data-dir env vars from leaking into integration tests.

    Why: GitHub Actions Ubuntu runners set ``XDG_CONFIG_HOME=/home/runner/.config``,
    and ``resolve_data_dir()`` honors it ahead of ``Path.home() / ".basic-memory"``.
    Without clearing it, the MCP tool process reads config.json and the registry
    database from the host XDG path instead of the tmp dir the fixtures wrote to,
    so ``test-project`` does not exist as far as the tool call is concerned.
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
    # fixture patches HOME. Mirrors tests/conftest.py.
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", _HOST_FASTEMBED_CACHE)


@pytest_asyncio.fixture
async def engine_factory(
    app_config,
    config_manager,
    tmp_path,
) -> AsyncGenerator[tuple, None]:
    """Create engine and session factory backed by a fresh on-disk SQLite database."""
    from basic_memory.models.search import CREATE_SEARCH_INDEX
    from basic_memory import db

    # The fixture DB must live where config says the app DB lives: native CLI
    # commands on the direct path (cli/direct.py, T18) resolve the engine from
    # config.database_path rather than the app's dependency_overrides, and a
    # fixture DB at a private path would make the two halves of one test read
    # different databases.
    db_path = app_config.database_path
    db_type = DatabaseType.FILESYSTEM

    async with engine_session_factory(db_path, db_type) as (engine, session_maker):
        # Create all tables via ORM
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Drop any SearchIndex ORM table, then create FTS5 virtual table
        async with db.scoped_session(session_maker) as session:
            await session.execute(text("DROP TABLE IF EXISTS search_index"))
            await session.execute(CREATE_SEARCH_INDEX)
            # Stamp the ORM-created schema at alembic head. A direct-path CLI
            # command (cli/direct.py) that recreates the module engine runs
            # get_or_create_db's automatic migrations; without the stamp,
            # alembic replays every revision against tables that already exist.
            alembic_cfg = Config()
            alembic_cfg.set_main_option(
                "script_location", str(Path(basic_memory.__file__).parent / "alembic")
            )
            head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
            await session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
                )
            )
            await session.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                {"head": head},
            )
            await session.commit()

        yield engine, session_maker


@pytest_asyncio.fixture
async def test_project(config_home, engine_factory) -> Project:
    """Create a test project."""
    project_data = {
        "name": "test-project",
        "description": "Project used for integration tests",
        "path": str(config_home),
        "is_active": True,
        "is_default": True,
    }

    _, session_maker = engine_factory
    project_repository = ProjectRepository()
    from basic_memory import db

    async with db.scoped_session(session_maker) as session:
        project = await project_repository.create(session, project_data)
    return project


@pytest.fixture
def config_home(tmp_path, monkeypatch) -> Path:
    # Patch both HOME and USERPROFILE so Path.home() returns the test dir on
    # every platform — Path.home() reads HOME on POSIX and USERPROFILE on
    # Windows, and ConfigManager.data_dir_path now goes through Path.home()
    # via resolve_data_dir(). Must mirror tests/conftest.py:config_home.
    monkeypatch.setenv("HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Set BASIC_MEMORY_HOME to the test directory
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    # A `.bm.yml` in the checkout would otherwise pin every CLI run here to a
    # project this config never registers (GAPS T32).
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def app_config(
    config_home,
    tmp_path,
    monkeypatch,
) -> BasicMemoryConfig:
    """Create test app configuration."""
    # Disable cloud mode for CLI tests
    monkeypatch.setenv("BASIC_MEMORY_CLOUD_MODE", "false")

    app_config = BasicMemoryConfig(
        env="test",
        update_permalinks_on_move=True,
        index_changes=False,  # Disable file indexing in tests - prevents lifespan from starting blocking task
        # Trigger: semantic_search_enabled defaults to True whenever fastembed/sqlite-vec
        #          are importable, which they are in dev and CI environments.
        # Why: with it on, every test that syncs pays the ONNX embedding stack (~5-7s per
        #      sync) — embeddings are covered by test-int/semantic/, which configures
        #      semantic_search_enabled explicitly in its own conftest.
        # Outcome: non-semantic integration tests skip embedding work entirely.
        semantic_search_enabled=False,
    )
    return app_config


@pytest.fixture
def config_manager(app_config: BasicMemoryConfig, config_home) -> ConfigManager:
    # Invalidate config cache to ensure clean state for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    config_manager = ConfigManager()
    # Update its paths to use the test directory
    config_manager.config_dir = config_home / ".basic-memory"
    config_manager.config_file = config_manager.config_dir / "config.json"
    config_manager.config_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the config file is written to disk
    config_manager.save_config(app_config)
    return config_manager


@pytest.fixture
def project_config(test_project):
    """Create test project configuration."""

    project_config = ProjectConfig(
        name=test_project.name,
        home=Path(test_project.path),
    )

    return project_config


@pytest.fixture
def app(
    app_config, project_config, engine_factory, test_project, config_manager
) -> Generator[FastAPI, None, None]:
    """Create test FastAPI application with single project."""

    # Import the FastAPI app AFTER the config_manager has written the test config to disk
    # This ensures that when the app's lifespan manager runs, it reads the correct test config
    from basic_memory.api.app import app as fastapi_app

    app = fastapi_app
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    app.dependency_overrides[get_app_config] = lambda: app_config
    try:
        yield app
    finally:
        # Restore overrides so one test's injected dependencies don't leak into
        # subsequent tests that use the same global FastAPI app instance.
        app.dependency_overrides = previous_overrides


@pytest_asyncio.fixture
async def search_service(engine_factory, test_project, app_config):
    """Create and initialize search service for integration tests."""
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.services.file_service import FileService
    from basic_memory.services.search_service import SearchService
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown import EntityParser

    from basic_memory.repository.search_repository import create_search_repository

    _, session_maker = engine_factory

    search_repository = create_search_repository(
        session_maker, project_id=test_project.id, app_config=app_config
    )

    entity_repository = EntityRepository(project_id=test_project.id)

    # Create file service
    entity_parser = EntityParser(Path(test_project.path))
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(Path(test_project.path), markdown_processor)

    # Create and initialize search service
    service = SearchService(
        search_repository,
        entity_repository,
        file_service,
        session_maker=session_maker,
    )
    await service.init_search_index()
    return service


@pytest.fixture
def mcp_server(config_manager, search_service):
    # Import mcp instance
    from basic_memory.mcp.server import mcp as server

    # Import mcp tools to register them
    import basic_memory.mcp.tools  # noqa: F401

    # Import resources to register them
    import basic_memory.mcp.resources  # noqa: F401

    # Import prompts to register them
    import basic_memory.mcp.prompts  # noqa: F401

    return server


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
