"""Tests for `bm reindex` CLI wiring."""

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import typer
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.db as db_cmd  # noqa: F401


runner = CliRunner()

# `_reindex` skips a project whose directory is gone (GAPS U12), so a project
# stub needs a path that exists or every test below becomes a skip test.
EXISTING_DIR = tempfile.gettempdir()


def _stub_app_config(*, semantic_search_enabled: bool = True) -> SimpleNamespace:
    """Build the minimal config surface the CLI reindex path expects."""
    return SimpleNamespace(
        semantic_search_enabled=semantic_search_enabled,
        database_path=Path("/tmp/basic-memory.db"),
    )


def _configure_reindex_cli(monkeypatch, app_config: SimpleNamespace) -> None:
    """Keep CLI tests focused on reindex wiring instead of full app startup."""
    monkeypatch.setattr("basic_memory.cli.app.init_cli_logging", lambda: None)
    monkeypatch.setattr(
        "basic_memory.cli.app.CliContainer.create",
        lambda: SimpleNamespace(config=app_config),
    )
    monkeypatch.setattr(
        db_cmd,
        "ConfigManager",
        lambda: SimpleNamespace(config=app_config),
    )


def test_reindex_defaults_to_incremental_search_and_embeddings(monkeypatch):
    app_config = _stub_app_config()
    _configure_reindex_cli(monkeypatch, app_config)
    captured: dict[str, object] = {}

    async def _stub_reindex(app_config, *, search: bool, embeddings: bool, full: bool, project):
        captured.update(
            {
                "app_config": app_config,
                "search": search,
                "embeddings": embeddings,
                "full": full,
                "project": project,
            }
        )

    monkeypatch.setattr(db_cmd, "_reindex", _stub_reindex)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert captured == {
        "app_config": app_config,
        "search": True,
        "embeddings": True,
        "full": False,
        "project": None,
    }


def test_reindex_full_runs_full_search_and_embeddings(monkeypatch):
    app_config = _stub_app_config()
    _configure_reindex_cli(monkeypatch, app_config)
    captured: dict[str, object] = {}

    async def _stub_reindex(app_config, *, search: bool, embeddings: bool, full: bool, project):
        captured.update(
            {
                "search": search,
                "embeddings": embeddings,
                "full": full,
                "project": project,
            }
        )

    monkeypatch.setattr(db_cmd, "_reindex", _stub_reindex)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))

    result = runner.invoke(app, ["reindex", "--full"])

    assert result.exit_code == 0
    assert captured == {
        "search": True,
        "embeddings": True,
        "full": True,
        "project": None,
    }


def test_reindex_full_search_runs_search_only(monkeypatch):
    app_config = _stub_app_config()
    _configure_reindex_cli(monkeypatch, app_config)
    captured: dict[str, object] = {}

    async def _stub_reindex(app_config, *, search: bool, embeddings: bool, full: bool, project):
        captured.update(
            {
                "search": search,
                "embeddings": embeddings,
                "full": full,
                "project": project,
            }
        )

    monkeypatch.setattr(db_cmd, "_reindex", _stub_reindex)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))

    result = runner.invoke(app, ["reindex", "--full", "--search"])

    assert result.exit_code == 0
    assert captured == {
        "search": True,
        "embeddings": False,
        "full": True,
        "project": None,
    }


def test_reindex_embeddings_only_preserves_incremental_default(monkeypatch):
    app_config = _stub_app_config()
    _configure_reindex_cli(monkeypatch, app_config)
    captured: dict[str, object] = {}

    async def _stub_reindex(app_config, *, search: bool, embeddings: bool, full: bool, project):
        captured.update(
            {
                "search": search,
                "embeddings": embeddings,
                "full": full,
                "project": project,
            }
        )

    monkeypatch.setattr(db_cmd, "_reindex", _stub_reindex)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))

    result = runner.invoke(app, ["reindex", "--embeddings"])

    assert result.exit_code == 0
    assert captured == {
        "search": False,
        "embeddings": True,
        "full": False,
        "project": None,
    }


@pytest.mark.asyncio
async def test_reindex_project_full_uses_core_project_index_and_reports_summary(
    monkeypatch,
    session_maker,
):
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", path=EXISTING_DIR)
    project_index = AsyncMock(
        return_value=SimpleNamespace(
            total_files=3,
            enqueued_files=2,
            enqueued_batches=1,
            deleted_files=1,
        )
    )
    printed_lines: list[str] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [project]

    # _reindex imports its database/index dependencies at call time (#886),
    # so stubs target the source modules instead of db_cmd attributes.
    monkeypatch.setattr("basic_memory.services.initialization.ensure_project_registry", AsyncMock())
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project",
        project_index,
    )
    monkeypatch.setattr(
        db_cmd.console,
        "print",
        lambda message="", *args, **kwargs: printed_lines.append(str(message)),
    )

    await db_cmd._reindex(
        app_config,
        search=True,
        embeddings=False,
        full=True,
        project="foo",
    )

    project_index.assert_awaited_once()
    index_call = project_index.await_args
    assert index_call is not None
    assert index_call.args[0] == project
    assert index_call.kwargs["force_full"] is True
    # Search-only reindex must not run the embedding provider via the project index.
    assert index_call.kwargs["embeddings"] is False
    assert any("project index" in line for line in printed_lines)
    assert any("3 observed, 2 indexed, 1 deleted" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_embeddings_only_full_passes_force_full_to_vector_reindex(
    monkeypatch,
    session_maker,
):
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", path=EXISTING_DIR)
    printed_lines: list[str] = []
    vector_reindex_calls: list[dict[str, object]] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [project]

    class StubSearchService:
        def __init__(self, search_repository, entity_repository, file_service, *, session_maker):
            self.search_repository = search_repository
            self.entity_repository = entity_repository
            self.file_service = file_service
            self.session_maker = session_maker

        async def reindex_vectors(self, *, progress_callback=None, force_full: bool = False):
            vector_reindex_calls.append(
                {
                    "progress_callback": progress_callback,
                    "force_full": force_full,
                }
            )
            return {"total_entities": 2, "embedded": 2, "skipped": 0, "errors": 0}

    class SilentProgress:
        def __init__(self, *args, **kwargs):
            self.tasks: dict[int, SimpleNamespace] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_task(self, description, total=None):
            self.tasks[1] = SimpleNamespace(total=total, description=description)
            return 1

        def update(self, task_id, **kwargs):
            if "total" in kwargs:
                self.tasks[task_id].total = kwargs["total"]

    # _reindex imports its database/sync dependencies at call time (#886),
    # so stubs target the source modules instead of db_cmd attributes.
    monkeypatch.setattr("basic_memory.services.initialization.ensure_project_registry", AsyncMock())
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.repository.search_repository.create_search_repository",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "basic_memory.repository.EntityRepository", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "basic_memory.markdown.entity_parser.EntityParser",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "basic_memory.markdown.markdown_processor.MarkdownProcessor",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "basic_memory.services.file_service.FileService", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr("basic_memory.services.search_service.SearchService", StubSearchService)
    monkeypatch.setattr(db_cmd, "Progress", SilentProgress)
    monkeypatch.setattr(
        db_cmd.console,
        "print",
        lambda message="", *args, **kwargs: printed_lines.append(str(message)),
    )

    await db_cmd._reindex(
        app_config,
        search=False,
        embeddings=True,
        full=True,
        project="foo",
    )

    assert len(vector_reindex_calls) == 1
    assert vector_reindex_calls[0]["force_full"] is True
    assert callable(vector_reindex_calls[0]["progress_callback"])
    assert any("full rebuild" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_recovers_stuck_materializations_before_scan(monkeypatch, session_maker):
    """The scan reconciles deletes against the filesystem, so a note whose accepted
    file write a crash left stuck ('writing'/'pending'/'failed') would be destroyed
    as a missing file. Recovery must re-drive stuck rows before each project scan."""
    app_config = _stub_app_config()
    projects = [
        SimpleNamespace(id=1, name="foo", path=EXISTING_DIR),
        SimpleNamespace(id=2, name="bar", path=EXISTING_DIR),
    ]
    call_order: list[str] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return projects

    async def record_recover(project, session_maker_arg):
        assert session_maker_arg is session_maker
        call_order.append(f"recover:{project.name}")

    async def record_project_index(project, *, runtime_factory, force_full, embeddings):
        call_order.append(f"index:{project.name}")
        return SimpleNamespace(total_files=0, enqueued_files=0, enqueued_batches=0, deleted_files=0)

    # _reindex imports its database/index dependencies at call time (#886),
    # so stubs target the source modules instead of db_cmd attributes.
    monkeypatch.setattr("basic_memory.services.initialization.ensure_project_registry", AsyncMock())
    monkeypatch.setattr(
        "basic_memory.services.initialization.recover_project_materializations",
        record_recover,
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project",
        record_project_index,
    )
    monkeypatch.setattr(db_cmd.console, "print", lambda *args, **kwargs: None)

    await db_cmd._reindex(app_config, search=True, embeddings=False, full=False, project=None)

    # Recovery runs before the delete-reconciling scan, per project.
    assert call_order == ["recover:foo", "index:foo", "recover:bar", "index:bar"]


@pytest.mark.asyncio
async def test_reindex_full_does_not_double_embed(monkeypatch, session_maker):
    """A full reindex (search + embeddings) must embed once: the FTS rebuild runs
    with embeddings=False so only the explicit vector phase calls the provider."""
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", path=EXISTING_DIR)
    vector_reindex_calls: list[dict[str, object]] = []
    project_index = AsyncMock(
        return_value=SimpleNamespace(
            total_files=1, enqueued_files=1, enqueued_batches=1, deleted_files=0
        )
    )

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [project]

    class StubSearchService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def reindex_vectors(self, *, progress_callback=None, force_full: bool = False):
            vector_reindex_calls.append({"force_full": force_full})
            return {"total_entities": 1, "embedded": 1, "skipped": 0, "errors": 0}

    class SilentProgress:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

        def add_task(self, *args, **kwargs) -> int:
            return 1

        def update(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr("basic_memory.services.initialization.ensure_project_registry", AsyncMock())
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db", AsyncMock(return_value=(None, session_maker))
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project", project_index
    )
    monkeypatch.setattr(
        "basic_memory.repository.search_repository.create_search_repository",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("basic_memory.repository.EntityRepository", lambda *a, **k: object())
    monkeypatch.setattr(
        "basic_memory.markdown.entity_parser.EntityParser", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        "basic_memory.markdown.markdown_processor.MarkdownProcessor", lambda *a, **k: object()
    )
    monkeypatch.setattr("basic_memory.services.file_service.FileService", lambda *a, **k: object())
    monkeypatch.setattr("basic_memory.services.search_service.SearchService", StubSearchService)
    monkeypatch.setattr(db_cmd, "Progress", SilentProgress)
    monkeypatch.setattr(db_cmd.console, "print", lambda message="", *a, **k: None)

    await db_cmd._reindex(app_config, search=True, embeddings=True, full=True, project="foo")

    # FTS rebuild ran without embeddings; only the explicit phase embedded (once).
    project_index.assert_awaited_once()
    index_call = project_index.await_args
    assert index_call is not None
    assert index_call.kwargs["embeddings"] is False
    assert len(vector_reindex_calls) == 1
    assert vector_reindex_calls[0]["force_full"] is True


# --- A registered project whose directory has gone (GAPS U12) ---


@pytest.mark.asyncio
async def test_reindex_skips_a_missing_project_and_still_indexes_the_rest(
    monkeypatch,
    session_maker,
    tmp_path,
):
    """A bare `bm reindex` used to die on a raw FileNotFoundError and index nothing.

    The scan walks the project's path, so an absent directory aborted the whole
    run — healthy projects included. It must now name that project, skip it,
    finish the others, and exit non-zero.
    """
    app_config = _stub_app_config()
    gone = tmp_path / "moved-away"
    projects = [
        SimpleNamespace(id=1, name="gone", path=str(gone)),
        SimpleNamespace(id=2, name="healthy", path=EXISTING_DIR),
    ]
    project_index = AsyncMock(
        return_value=SimpleNamespace(
            total_files=1, enqueued_files=1, enqueued_batches=1, deleted_files=0
        )
    )
    printed_lines: list[str] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return projects

    monkeypatch.setattr("basic_memory.services.initialization.ensure_project_registry", AsyncMock())
    monkeypatch.setattr(
        "basic_memory.services.initialization.recover_project_materializations", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project",
        project_index,
    )
    monkeypatch.setattr(
        db_cmd.console,
        "print",
        lambda message="", *args, **kwargs: printed_lines.append(str(message)),
    )

    with pytest.raises(typer.Exit) as exc_info:
        await db_cmd._reindex(app_config, search=True, embeddings=False, full=False, project=None)

    assert exc_info.value.exit_code == 1
    # The healthy project was still indexed — that is the whole point.
    project_index.assert_awaited_once()
    assert project_index.await_args is not None
    assert project_index.await_args.args[0] is projects[1]
    # The skip names the project and the path, inline and again at the end.
    assert any(str(gone) in line for line in printed_lines)
    assert any("Reindex incomplete" in line and "gone" in line for line in printed_lines)
    assert not any("Reindex complete" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_says_complete_when_every_directory_is_there(
    monkeypatch,
    session_maker,
):
    """Control for the skip above: a healthy registry must not exit non-zero."""
    app_config = _stub_app_config()
    projects = [SimpleNamespace(id=1, name="healthy", path=EXISTING_DIR)]
    printed_lines: list[str] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return projects

    monkeypatch.setattr("basic_memory.services.initialization.ensure_project_registry", AsyncMock())
    monkeypatch.setattr(
        "basic_memory.services.initialization.recover_project_materializations", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project",
        AsyncMock(
            return_value=SimpleNamespace(
                total_files=1, enqueued_files=1, enqueued_batches=1, deleted_files=0
            )
        ),
    )
    monkeypatch.setattr(
        db_cmd.console,
        "print",
        lambda message="", *args, **kwargs: printed_lines.append(str(message)),
    )

    await db_cmd._reindex(app_config, search=True, embeddings=False, full=False, project=None)

    assert any("Reindex complete" in line for line in printed_lines)
    assert not any("Reindex incomplete" in line for line in printed_lines)
