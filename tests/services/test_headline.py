"""The per-project headline file (GAPS W9, verbs item D).

Every test drives `refresh_headline` against a real session and a real file, and
the file's *bytes and mtime* are the assertions — the three consumer scripts read
line 1, line 2, and the mtime, and each of those has already failed in practice.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, Project
from basic_memory.services.headline import (
    MAX_HEADLINE_CHARS,
    headline_path,
    refresh_headline,
)
from basic_memory.vocabulary.model import vocabulary_path

# An mtime far enough in the past that no filesystem timestamp resolution can
# confuse "untouched" with "rewritten in the same tick".
STALE_MTIME = 1_600_000_000


@pytest_asyncio.fixture
async def add_task(session_maker: async_sessionmaker[AsyncSession], test_project: Project):
    """Add one record to the project, newest-first by the minutes given."""

    async def _add(
        title: str,
        *,
        status: str | None = "open",
        minutes_ago: int = 0,
        note_type: str = "task",
    ) -> None:
        metadata = {"type": note_type}
        if status is not None:
            metadata["status"] = status
        slug = title.lower().replace(" ", "-")
        async with db.scoped_session(session_maker) as session:
            session.add(
                Entity(
                    project_id=test_project.id,
                    title=title,
                    note_type=note_type,
                    permalink=slug,
                    file_path=f"{note_type}s/{slug}.md",
                    content_type="text/markdown",
                    entity_metadata=metadata,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
                )
            )

    return _add


async def refresh(session_maker, project: Project) -> bool:
    async with db.scoped_session(session_maker) as session:
        return await refresh_headline(session, project)


def govern(project: Project, **content) -> None:
    """Give the project a vocabulary file, which is what governs it."""
    path = vocabulary_path(project.external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(content), encoding="utf-8")


# --- What the headline says ---


@pytest.mark.asyncio
async def test_the_most_recently_updated_open_task_wins(session_maker, test_project, add_task):
    await add_task("Older task", minutes_ago=60)
    await add_task("Newest task", minutes_ago=1)

    assert await refresh(session_maker, test_project) is True
    assert "headline: Newest task" in headline_path(test_project.external_id).read_text()


@pytest.mark.asyncio
async def test_a_terminal_task_never_becomes_the_headline(session_maker, test_project, add_task):
    """A finished task is not what is next, however recently it was finished."""
    await add_task("Still open", minutes_ago=60)
    await add_task("Just finished", status="done", minutes_ago=1)
    await add_task("Abandoned", status="dropped", minutes_ago=0)

    await refresh(session_maker, test_project)

    assert "headline: Still open" in headline_path(test_project.external_id).read_text()


@pytest.mark.asyncio
async def test_a_task_with_no_status_still_counts_as_open(session_maker, test_project, add_task):
    """Hiding work over incomplete frontmatter suppresses the thing this shows."""
    await add_task("Statusless", status=None, minutes_ago=1)

    await refresh(session_maker, test_project)

    assert "headline: Statusless" in headline_path(test_project.external_id).read_text()


@pytest.mark.asyncio
async def test_other_record_types_are_ignored(session_maker, test_project, add_task):
    """Only `task` has a lifecycle, so only a task can answer "what is next"."""
    await add_task("A guide", note_type="guide", status=None, minutes_ago=0)
    await add_task("The task", minutes_ago=30)

    await refresh(session_maker, test_project)

    assert "headline: The task" in headline_path(test_project.external_id).read_text()


@pytest.mark.asyncio
async def test_the_title_is_truncated_to_the_statusline_limit(
    session_maker, test_project, add_task
):
    await add_task("A task title far longer than any statusline can show")

    await refresh(session_maker, test_project)

    line = headline_path(test_project.external_id).read_text().splitlines()[1]
    text = line.removeprefix("headline: ")
    assert len(text) <= MAX_HEADLINE_CHARS
    assert text == "A task title far longer than a"


@pytest.mark.asyncio
async def test_the_file_is_the_three_lines_every_consumer_parses(
    session_maker, test_project, add_task
):
    """The statusline checks line 1 is `---`; two other scripts read line 2 raw."""
    await add_task("Ship the verbs")

    await refresh(session_maker, test_project)

    lines = headline_path(test_project.external_id).read_text(encoding="utf-8").splitlines()
    assert lines == ["---", "headline: Ship the verbs", "---"]


# --- When it writes, and when it must not ---


@pytest.mark.asyncio
async def test_an_unchanged_headline_leaves_the_mtime_alone(session_maker, test_project, add_task):
    """W9's mtime trap: the overview script reads mtime as its staleness signal."""
    await add_task("Ship the verbs")
    await refresh(session_maker, test_project)
    path = headline_path(test_project.external_id)
    os.utime(path, (STALE_MTIME, STALE_MTIME))

    assert await refresh(session_maker, test_project) is False
    assert path.stat().st_mtime == STALE_MTIME


@pytest.mark.asyncio
async def test_a_real_change_does_move_the_mtime(session_maker, test_project, add_task):
    """Positive control for the test above: the skip is conditional, not total."""
    await add_task("Ship the verbs")
    await refresh(session_maker, test_project)
    path = headline_path(test_project.external_id)
    os.utime(path, (STALE_MTIME, STALE_MTIME))

    await add_task("Something newer", minutes_ago=0)
    assert await refresh(session_maker, test_project) is True
    assert path.stat().st_mtime != STALE_MTIME
    assert "headline: Something newer" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_no_open_work_removes_the_file(session_maker, test_project, add_task):
    """An empty headline would render a blank bar; absence lets consumers fall back."""
    await add_task("Ship the verbs")
    await refresh(session_maker, test_project)
    path = headline_path(test_project.external_id)
    assert path.is_file()

    async with db.scoped_session(session_maker) as session:
        entity = await session.scalar(select(Entity).where(Entity.title == "Ship the verbs"))
        assert entity is not None
        entity.entity_metadata = {"type": "task", "status": "done"}

    assert await refresh(session_maker, test_project) is True
    assert not path.exists()


@pytest.mark.asyncio
async def test_an_empty_corpus_writes_nothing(session_maker, test_project):
    assert await refresh(session_maker, test_project) is False
    assert not headline_path(test_project.external_id).exists()


@pytest.mark.asyncio
async def test_a_status_the_vocabulary_dropped_is_no_longer_terminal(
    session_maker, test_project, add_task
):
    """A governed project narrows the terminal set to the names it declares."""
    govern(test_project, statuses=["open", "doing", "done"])
    await add_task("Was dropped", status="dropped", minutes_ago=1)
    await add_task("Plainly open", minutes_ago=60)

    await refresh(session_maker, test_project)

    assert "headline: Was dropped" in headline_path(test_project.external_id).read_text()


@pytest.mark.asyncio
async def test_headline_path_sits_in_the_store(test_project: Project):
    """Decision D6: the file lives in the store, never beside a working dir's marker."""
    from basic_memory.store.history import store_path

    assert headline_path(test_project.external_id) == (
        store_path() / test_project.external_id / "headline.md"
    )
    assert Path(test_project.path) != store_path()


@pytest.mark.asyncio
async def test_a_shelved_task_never_becomes_the_headline(session_maker, test_project, add_task):
    """Parked work is not what is next, so the headline shows the next open task.

    Ungoverned: no vocabulary file, so `inactive_statuses()` answers with the
    defaults, which is the state most projects are in (GAPS U23).
    """
    await add_task("Still open", minutes_ago=60)
    await add_task("Set aside", status="shelved", minutes_ago=1)

    await refresh(session_maker, test_project)

    assert "headline: Still open" in headline_path(test_project.external_id).read_text()


@pytest.mark.asyncio
async def test_a_project_that_never_declared_shelved_treats_it_as_open(
    session_maker, test_project, add_task
):
    """The narrowing rule, in the direction that proves it is a narrowing.

    A project whose vocabulary omits `shelved` has no parked state, so a task
    carrying that status is off-vocabulary rather than parked — and hiding it
    would suppress work over a fault `bm doctor` already reports.
    """
    govern(test_project, statuses=["open", "doing", "done"])
    await add_task("Plainly open", minutes_ago=60)
    await add_task("Set aside", status="shelved", minutes_ago=1)

    await refresh(session_maker, test_project)

    assert "headline: Set aside" in headline_path(test_project.external_id).read_text()


@pytest.mark.asyncio
async def test_only_shelved_work_leaves_no_headline(session_maker, test_project, add_task):
    """No open work is a real answer, and the file says it by not existing."""
    await add_task("Set aside", status="shelved", minutes_ago=1)

    await refresh(session_maker, test_project)

    assert not headline_path(test_project.external_id).exists()
