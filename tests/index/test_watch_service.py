"""Regression tests for local watcher batch isolation and config re-reads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.index.watch_service import WatchService
from basic_memory.models import Project


@pytest.mark.asyncio
async def test_handle_changes_isolated_contains_one_project_failure(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
) -> None:
    """One project's handler failure must not abort or drop other projects' batches."""
    handled: list[str] = []

    class FailingWatchService(WatchService):
        async def handle_changes(self, project, changes) -> None:  # type: ignore[override]
            handled.append(project.name)
            if project.name == "boom":
                raise RuntimeError("indexing boom")

    watch_service = FailingWatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )

    boom = SimpleNamespace(name="boom")
    healthy = SimpleNamespace(name="healthy")

    # Mirror _watch_projects_cycle: gather isolated handlers for every project batch.
    await asyncio.gather(
        watch_service._handle_changes_isolated(cast(Project, boom), set()),
        watch_service._handle_changes_isolated(cast(Project, healthy), set()),
    )

    # The failing project did not prevent the healthy project from being handled,
    # and the error was recorded rather than propagated out of gather().
    assert set(handled) == {"boom", "healthy"}
    assert watch_service.state.error_count == 1
    assert watch_service.state.recent_events[0].status == "error"


@pytest.mark.asyncio
async def test_project_is_registered_rereads_current_registry(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
) -> None:
    """A project deleted from the registry after startup must not be treated as registered."""
    from basic_memory import db

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )

    assert await watch_service._project_is_registered(test_project) is True

    # Simulate `bm project remove` deleting the row after the watcher started.
    async with db.scoped_session(session_maker) as session:
        await project_repository.delete(session, test_project.id)

    # The guard re-queries the registry rather than a startup snapshot.
    assert await watch_service._project_is_registered(test_project) is False
