"""Integration-style tests for the initialization service.

Goal: avoid brittle deep mocking; assert real behavior using the existing
test config fixtures.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.indexing.project_index_coordinator import ProjectIndexCoordinatorResult
from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.initialization import (
    ensure_initialization,
    ensure_project_registry,
    initialize_app,
    initialize_database,
    initialize_file_indexing,
)


@pytest.mark.asyncio
async def test_initialize_database_creates_engine_and_allows_queries(app_config: BasicMemoryConfig):
    await db.shutdown_db()
    try:
        await initialize_database(app_config)

        engine, session_maker = await db.get_or_create_db(app_config.database_path)
        assert engine is not None
        assert session_maker is not None

        # Smoke query on the initialized DB
        async with db.scoped_session(session_maker) as session:
            result = await session.execute(db.text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_ensure_project_registry_imports_legacy_config_projects_into_empty_db(
    app_config: BasicMemoryConfig, config_manager, config_home
):
    """A pre-B2 config.json's legacy registry imports once into an empty DB (GAPS B2).

    ``legacy_config_registry()`` reads the raw config.json file directly, so the
    legacy shape is written straight to disk here rather than through
    ``BasicMemoryConfig`` — the model no longer declares ``projects``/
    ``default_project`` at all.
    """
    import json

    await db.shutdown_db()
    try:
        proj_a = config_home / "proj-a"
        proj_b = config_home / "proj-b"
        legacy_data = {
            "env": "test",
            "projects": {
                "proj-a": str(proj_a),
                "proj-b": str(proj_b),
            },
            "default_project": "proj-b",
        }
        config_manager.config_file.write_text(json.dumps(legacy_data, indent=2))

        await initialize_database(app_config)
        await ensure_project_registry(app_config)

        _, session_maker = await db.get_or_create_db(
            app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
        )
        repo = ProjectRepository()

        async with db.scoped_session(session_maker) as session:
            active = await repo.get_active_projects(session)
            names = {p.name for p in active}
            assert names == {"proj-a", "proj-b"}

            default = await repo.get_default_project(session)
            assert default is not None
            assert default.name == "proj-b"

        # Absolute legacy paths are created on import.
        assert proj_a.is_dir()
        assert proj_b.is_dir()
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_ensure_project_registry_leaves_populated_registry_untouched(
    app_config: BasicMemoryConfig, config_manager, config_home
):
    """A populated DB registry is never touched, no matter what config.json still names."""
    import json

    await db.shutdown_db()
    try:
        await initialize_database(app_config)

        repository = ProjectRepository()
        _, session_maker = await db.get_or_create_db(
            app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
        )
        seeded_path = config_home / "seeded"
        async with db.scoped_session(session_maker) as session:
            await repository.create(
                session,
                {
                    "name": "seeded",
                    "path": str(seeded_path),
                    "is_active": True,
                    "is_default": True,
                },
            )

        # config.json still declares a completely different legacy registry.
        legacy_data = {
            "env": "test",
            "projects": {"other": str(config_home / "other")},
            "default_project": "other",
        }
        config_manager.config_file.write_text(json.dumps(legacy_data, indent=2))

        await ensure_project_registry(app_config)

        async with db.scoped_session(session_maker) as session:
            active = await repository.get_active_projects(session)
            assert {p.name for p in active} == {"seeded"}
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_ensure_project_registry_bootstraps_main_when_nothing_exists(
    app_config: BasicMemoryConfig, config_manager, config_home
):
    """A genuinely fresh install (no DB rows, no legacy config) gets one bootstrap
    project 'main' at BASIC_MEMORY_HOME, flagged default."""
    import basic_memory.config as config_module

    # The legacy-registry capture is process-global state; an earlier test's
    # legacy projects must not leak into this "nothing configured" case.
    config_module._LEGACY_PROJECTS = {}
    config_module._LEGACY_DEFAULT_PROJECT = None

    bootstrap_home = config_home / "basic-memory"  # set by the config_home fixture

    await db.shutdown_db()
    try:
        await initialize_database(app_config)
        await ensure_project_registry(app_config)

        _, session_maker = await db.get_or_create_db(
            app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
        )
        repo = ProjectRepository()
        async with db.scoped_session(session_maker) as session:
            active = await repo.get_active_projects(session)
            assert {p.name for p in active} == {"main"}

            default = await repo.get_default_project(session)
            assert default is not None
            assert default.name == "main"
            assert Path(default.path) == bootstrap_home
    finally:
        await db.shutdown_db()


def test_ensure_initialization_runs_and_cleans_up(app_config: BasicMemoryConfig, config_manager):
    # ensure_initialization uses asyncio.run; keep this test synchronous.
    ensure_initialization(app_config)

    # Must be cleaned up to avoid hanging processes.
    assert db._engine is None  # pyright: ignore [reportPrivateUsage]
    assert db._session_maker is None  # pyright: ignore [reportPrivateUsage]


@pytest.mark.asyncio
async def test_initialize_app_warns_on_frontmatter_permalink_precedence(
    app_config: BasicMemoryConfig, monkeypatch
):
    app_config.ensure_frontmatter_on_sync = True
    app_config.disable_permalinks = True

    init_db_mock = AsyncMock()
    ensure_registry_mock = AsyncMock()
    monkeypatch.setattr("basic_memory.services.initialization.initialize_database", init_db_mock)
    monkeypatch.setattr(
        "basic_memory.services.initialization.ensure_project_registry",
        ensure_registry_mock,
    )

    warnings: list[str] = []

    def capture_warning(message: str) -> None:
        warnings.append(message)

    monkeypatch.setattr("basic_memory.services.initialization.logger.warning", capture_warning)

    await initialize_app(app_config)

    assert init_db_mock.await_count == 1
    assert ensure_registry_mock.await_count == 1
    assert any(
        "ensure_frontmatter_on_sync=True overrides disable_permalinks=True" in message
        for message in warnings
    )


class _FakeWatchService:
    """Captures init kwargs so tests can assert what the real service receives."""

    last_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs):
        _FakeWatchService.last_kwargs = kwargs

    async def run(self):
        return None


def _disable_test_env_short_circuit(monkeypatch) -> None:
    """Bypass ``is_test_env`` so ``initialize_file_indexing`` actually runs.

    ``is_test_env`` returns True whenever pytest is running, which would cause
    ``initialize_file_indexing`` to return before constructing a WatchService.
    """
    monkeypatch.setattr(BasicMemoryConfig, "is_test_env", property(lambda self: False))


@pytest.mark.asyncio
async def test_initialize_file_indexing_passes_constrained_project_to_watch_service(
    app_config: BasicMemoryConfig, monkeypatch
):
    """``BASIC_MEMORY_MCP_PROJECT`` must reach the watch service, not just the
    one-shot background indexing. Otherwise multiple ``basic-memory mcp --project X``
    processes each spawn a watcher over every project and race on file writes.
    """
    _disable_test_env_short_circuit(monkeypatch)
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "target-project")
    monkeypatch.setattr("basic_memory.index.watch_service.WatchService", _FakeWatchService)
    _FakeWatchService.last_kwargs = {}

    await initialize_file_indexing(app_config, quiet=True)

    assert _FakeWatchService.last_kwargs.get("constrained_project") == "target-project"


@pytest.mark.asyncio
async def test_initialize_file_indexing_no_constraint_when_env_unset(
    app_config: BasicMemoryConfig, monkeypatch
):
    """With no env var set, the watch service is unconstrained."""
    _disable_test_env_short_circuit(monkeypatch)
    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    monkeypatch.setattr("basic_memory.index.watch_service.WatchService", _FakeWatchService)
    _FakeWatchService.last_kwargs = {}

    await initialize_file_indexing(app_config, quiet=True)

    assert _FakeWatchService.last_kwargs.get("constrained_project") is None


@pytest.mark.asyncio
async def test_initialize_file_indexing_wires_event_index_runtime_by_default(
    app_config: BasicMemoryConfig, monkeypatch
):
    """Watcher event indexing is the default startup path."""
    _disable_test_env_short_circuit(monkeypatch)
    monkeypatch.setattr("basic_memory.index.watch_service.WatchService", _FakeWatchService)
    _FakeWatchService.last_kwargs = {}

    await initialize_file_indexing(app_config, quiet=True)

    assert isinstance(
        _FakeWatchService.last_kwargs.get("event_index_runtime_factory"),
        LocalWatchEventIndexRuntimeFactory,
    )


@pytest.mark.asyncio
async def test_initialize_file_indexing_uses_project_index_runtime_for_initial_sync_by_default(
    app_config: BasicMemoryConfig, config_home, monkeypatch
):
    """Default startup indexing uses project fanout instead of the legacy sync scan."""
    await db.shutdown_db()
    try:
        project_dir = config_home / "event-startup"
        project_dir.mkdir(parents=True, exist_ok=True)
        updated = app_config

        await initialize_database(updated)
        _, session_maker = await db.get_or_create_db(
            updated.database_path, db_type=db.DatabaseType.FILESYSTEM
        )
        async with db.scoped_session(session_maker) as session:
            await ProjectRepository().create(
                session,
                {
                    "name": "event-startup",
                    "path": str(project_dir),
                    "is_active": True,
                    "is_default": True,
                },
            )

        _disable_test_env_short_circuit(monkeypatch)
        monkeypatch.setattr("basic_memory.index.watch_service.WatchService", _FakeWatchService)

        original_create_task = asyncio.create_task
        created_coroutines = []

        class _CapturedTask:
            """Stand-in task: the scheduler holds a strong ref and registers a
            done callback, so the fake must accept both."""

            def add_done_callback(self, callback) -> None:  # noqa: ANN001
                del callback

        def capture_task(coro):
            coroutine_name = getattr(getattr(coro, "cr_code", None), "co_name", "")
            if coroutine_name == "index_project_background":
                created_coroutines.append(coro)
                return _CapturedTask()
            return original_create_task(coro)

        class RecordingProjectIndexRuntimeFactory:
            async def runtime_for_project(self, project):  # noqa: ANN001
                return f"runtime:{project.name}"

        project_index_calls: list[tuple[str, str, bool]] = []

        async def run_project_index_for_project(  # noqa: ANN001
            project, *, runtime_factory, force_full=False
        ):
            runtime = await runtime_factory.runtime_for_project(project)
            project_index_calls.append((project.name, runtime, force_full))
            return ProjectIndexCoordinatorResult(
                total_files=0,
                enqueued_files=0,
                enqueued_batches=0,
                deleted_files=0,
            )

        monkeypatch.setattr(
            "basic_memory.services.initialization.asyncio.create_task", capture_task
        )
        monkeypatch.setattr(
            "basic_memory.index.local_project.LocalProjectIndexRuntimeFactory",
            RecordingProjectIndexRuntimeFactory,
        )
        monkeypatch.setattr(
            "basic_memory.index.local_project.run_local_project_index_for_project",
            run_project_index_for_project,
        )

        await initialize_file_indexing(updated, quiet=True)

        assert len(created_coroutines) == 1
        await created_coroutines[0]

        assert project_index_calls == [("event-startup", "runtime:event-startup", False)]
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_initialize_file_indexing_skips_project_with_non_absolute_path(
    app_config: BasicMemoryConfig, config_home, monkeypatch
):
    """Projects without an absolute local path are excluded from background indexing (issue #949).

    A project row with ``path=""`` is not recognized as cloud, yet ``Path("")``
    resolves to the process cwd. Indexing it would inject frontmatter into
    unrelated files, so it must be skipped.
    """
    await db.shutdown_db()
    try:
        good = config_home / "good"
        good.mkdir(parents=True, exist_ok=True)

        updated = app_config

        await initialize_database(updated)
        _, session_maker = await db.get_or_create_db(
            updated.database_path, db_type=db.DatabaseType.FILESYSTEM
        )
        async with db.scoped_session(session_maker) as session:
            repository = ProjectRepository()
            await repository.create(
                session,
                {
                    "name": "good",
                    "path": str(good),
                    "is_active": True,
                    "is_default": True,
                },
            )
            # Empty (cwd-relative) path is not locally syncable.
            await repository.create(
                session,
                {
                    "name": "empty-path",
                    "path": "",
                    "is_active": True,
                    "is_default": False,
                },
            )

        _disable_test_env_short_circuit(monkeypatch)
        monkeypatch.setattr("basic_memory.index.watch_service.WatchService", _FakeWatchService)

        infos: list[str] = []
        monkeypatch.setattr(
            "basic_memory.services.initialization.logger.info",
            lambda message, *args, **kwargs: infos.append(message),
        )

        await initialize_file_indexing(updated, quiet=True)

        skip_logs = [m for m in infos if "not locally indexable" in m]
        assert skip_logs, "expected a skip log for the empty-path project"
        assert "empty-path" in skip_logs[0]
        assert "good" not in skip_logs[0]
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_initialize_app_no_precedence_warning_when_not_conflicting(
    app_config: BasicMemoryConfig, monkeypatch
):
    app_config.ensure_frontmatter_on_sync = False
    app_config.disable_permalinks = True

    monkeypatch.setattr(
        "basic_memory.services.initialization.initialize_database",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "basic_memory.services.initialization.ensure_project_registry",
        AsyncMock(),
    )

    warnings: list[str] = []

    def capture_warning(message: str) -> None:
        warnings.append(message)

    monkeypatch.setattr("basic_memory.services.initialization.logger.warning", capture_warning)

    await initialize_app(app_config)

    assert not any(
        "ensure_frontmatter_on_sync=True overrides disable_permalinks=True" in message
        for message in warnings
    )


@pytest.mark.asyncio
async def test_recover_project_materializations_writes_stuck_file(
    session_maker,
    test_project,
    sample_entity,
):
    """Startup recovery re-writes a note whose file write a crash left unfinished."""
    from datetime import UTC, datetime

    from basic_memory.repository.note_content_repository import (
        AcceptedNoteContentWrite,
        NoteContentRepository,
    )
    from basic_memory.services.initialization import recover_project_materializations

    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=sample_entity.id,
                markdown_content="# Recovered on startup\n",
                db_version=1,
                db_checksum="db-checksum-1",
                last_source="api",
                updated_at=datetime.now(UTC),
            ),
        )
        row = await repository.select_by_id(session, sample_entity.id)
        assert row is not None
        row.file_write_status = "writing"
        await session.flush()

    await recover_project_materializations(test_project, session_maker)

    from pathlib import Path

    written = Path(test_project.path) / sample_entity.file_path
    assert written.read_text(encoding="utf-8") == "# Recovered on startup\n"

    async with db.scoped_session(session_maker) as session:
        row = await repository.get_by_entity_id(session, sample_entity.id)
    assert row is not None
    assert row.file_write_status == "synced"
