import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from basic_memory.api.app import app as fastapi_app
from basic_memory.deps import get_engine_factory, get_app_config


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch) -> Path:
    """Isolate tests from user's HOME directory.

    This prevents tests from reading/writing to ~/.basic-memory/.bmignore
    or other user-specific configuration.

    Sets BASIC_MEMORY_HOME to tmp_path directly so the default project
    writes files to tmp_path, which is where tests expect to find them.
    """
    # Clear config cache to ensure fresh config for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    monkeypatch.setenv("HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Set to tmp_path directly (not tmp_path/basic-memory) so default project
    # home is tmp_path - tests expect to find imported files there
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path))
    # The verbs resolve their project from the nearest `.bm.yml` above the
    # working directory. A developer who runs `bm` from this checkout keeps
    # one there, and it names a project no test config registers — so a test
    # that inherits the repo root as cwd fails on the marker, not on its
    # subject. Every CLI test starts in tmp_path (GAPS T32).
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def silent_notices(monkeypatch) -> None:
    """Answer the per-command notice with an empty corpus (GAPS W5-B).

    Every project-touching verb now ends by counting what is outstanding, which
    opens a database. Left real, each CLI test would pay a migration for a line
    it is not testing, and any test asserting on the last output line would break
    the moment the count is non-zero.

    ``tests/cli/test_notices.py`` overrides this where the notice *is* the
    subject, and its gather tests call the real function by name, so they are not
    affected by this patch on the module attribute.
    """
    from basic_memory.cli import notices

    async def no_counts(scope) -> notices.NoticeCounts:
        return notices.NoticeCounts()

    monkeypatch.setattr(notices, "gather_notice_counts", no_counts)


@pytest.fixture
def bootstrapped_registry(isolated_home) -> None:
    """Run the real first-run bootstrap against this test's data dir.

    The database owns the project registry (GAPS B2), and synchronous
    CLI-boundary code — ``get_project_config()`` in `bm format`, `.bm.yml`
    resolution — reads it straight off disk. Tests that exercise those paths
    need a genuinely migrated database with a project in it, so this calls the
    same ``ensure_project_registry`` the first real command would.
    """
    import asyncio

    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.services.initialization import ensure_project_registry

    async def _bootstrap() -> None:
        try:
            await ensure_project_registry(ConfigManager().config)
        finally:
            await db.shutdown_db()

    asyncio.run(_bootstrap())


@pytest_asyncio.fixture
async def app(
    app_config, project_config, engine_factory, test_config, aiolib
) -> AsyncGenerator[FastAPI, None]:
    """Create test FastAPI application."""
    app = fastapi_app
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_app_config] = lambda: app_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    try:
        yield app
    finally:
        # Trigger: CLI tests share the module-level FastAPI app with API/MCP tests.
        # Why: leaving per-test dependency overrides installed lets later commands
        # talk to stale engines that no cleanup fixture owns.
        # Outcome: keep CLI app wiring isolated to the requesting test.
        app.dependency_overrides = previous_overrides


@pytest_asyncio.fixture
async def client(app: FastAPI, aiolib) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def cli_env(project_config, client, test_config):
    """Set up CLI environment with correct project session."""
    return {"project_config": project_config, "client": client}
