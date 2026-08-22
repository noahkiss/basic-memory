"""Fixtures for the `bm web` board server (GAPS U41).

The corpus is seeded straight into the file-backed database the server opens,
the way `tests/cli/test_record_read_commands.py` seeds one: real project rows,
real files on disk, deterministic record ids. Deterministic matters more here
than anywhere else — the assertions are about which card lands in which column,
and a random id cannot be written into an expectation.

Projects live under `store/<external-id>/`, which is where every project's files
actually live in this fork. The record page prints a store-relative path, and a
project seeded somewhere else could not prove that.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ALPHA = "alpha"
BETA = "beta"
GAMMA = "gamma"

# Fixed UUID4-shaped external ids: the store directory is named after them, and
# the store-relative path assertion has to be able to name it.
ALPHA_ID = "11111111-1111-4111-8111-111111111111"
BETA_ID = "22222222-2222-4222-8222-222222222222"
GAMMA_ID = "33333333-3333-4333-8333-333333333333"

# The id both projects hold, which is what makes an unscoped lookup ambiguous.
SHARED_ID = "tnd-both0008"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch) -> Path:
    """Point config, store and database at a temporary home.

    The server reads `ConfigManager` inside its own lifespan, so there is no
    dependency override to install — redirecting HOME is what makes the app
    under test open this test's database instead of the developer's.
    """
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def no_embeddings(monkeypatch) -> None:
    """Keep the 64 MB ONNX embedding stack off the search path; FTS is enough here."""
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")


def frontmatter(record_id: str, note_type: str, title: str, **extra: Any) -> dict[str, Any]:
    """A minimal record header: id and permalink equal, byte for byte."""
    return {
        "id": record_id,
        "permalink": record_id,
        "type": note_type,
        "title": title,
        "source": "cli",
        **extra,
    }


def note(record_id: str, note_type: str, title: str, body: str = "", **extra: Any) -> dict:
    """One record: the metadata the index carries and the file the page renders."""
    metadata = frontmatter(record_id, note_type, title, **extra)
    rendered = "\n".join(f"{key}: {value}" for key, value in metadata.items())
    return {
        "metadata": metadata,
        "file_path": f"{note_type}s/{record_id}--seed.md",
        "title": title,
        "content": f"---\n{rendered}\n---\n\n{body or title}\n",
    }


async def _seed(
    corpus: Mapping[str, Sequence[dict]],
    external_ids: Mapping[str, str],
    relations: Sequence[tuple[str, tuple[str, str], tuple[str, str]]],
) -> None:
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.models import Relation
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.repository.search_index_row import SearchIndexRow
    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
    from basic_memory.schemas.search import SearchItemType
    from basic_memory.store.history import store_path

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    try:
        # Plain values, captured inside the session: the ORM instances expire
        # when the scope commits, and reading them afterwards would detach.
        indexable: dict[int, list[SearchIndexRow]] = {}
        async with db.scoped_session(session_maker) as session:
            repository = ProjectRepository()
            by_id: dict[tuple[str, str], Any] = {}
            for project_name, entries in corpus.items():
                external_id = external_ids[project_name]
                home = store_path() / external_id
                home.mkdir(parents=True, exist_ok=True)
                project = await repository.create(
                    session,
                    {
                        "name": project_name,
                        "external_id": external_id,
                        "path": str(home),
                        "is_active": True,
                        "is_default": False,
                    },
                )
                entities = EntityRepository(project_id=project.id)
                for entry in entries:
                    metadata = entry["metadata"]
                    stamped = datetime.now(timezone.utc)
                    entity = await entities.create(
                        session,
                        {
                            "project_id": project.id,
                            "title": entry["title"],
                            "note_type": metadata["type"],
                            "permalink": metadata["permalink"],
                            "file_path": entry["file_path"],
                            "content_type": "text/markdown",
                            "entity_metadata": metadata,
                            "created_at": stamped,
                            "updated_at": stamped,
                        },
                    )
                    by_id[(project_name, str(entity.permalink))] = entity
                    indexable.setdefault(project.id, []).append(
                        SearchIndexRow(
                            project_id=project.id,
                            id=entity.id,
                            entity_id=entity.id,
                            type=SearchItemType.ENTITY.value,
                            title=entry["title"],
                            # The title again: the board's search is a title
                            # search, and an unqualified FTS query reads both
                            # this column and `title`.
                            content_stems=entry["title"],
                            permalink=metadata["permalink"],
                            file_path=entry["file_path"],
                            metadata={"note_type": metadata["type"]},
                            created_at=stamped,
                            updated_at=stamped,
                        )
                    )

                    target = home / entry["file_path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(entry["content"], encoding="utf-8")

            for relation_type, source_key, target_key in relations:
                source, target = by_id[source_key], by_id[target_key]
                session.add(
                    Relation(
                        project_id=source.project_id,
                        from_id=source.id,
                        to_id=target.id,
                        to_name=target.permalink,
                        relation_type=relation_type,
                    )
                )
            if relations:
                await session.flush()

        # Indexed after the seeding transaction closes: `index_item` opens its
        # own session, and the pool holds one connection.
        for project_id, rows in indexable.items():
            search = SQLiteSearchRepository(session_maker, project_id=project_id)
            await search.init_search_index()
            for row in rows:
                await search.index_item(row)
    finally:
        await db.shutdown_db()


ALPHA_RECORDS = [
    note("tnd-open0001", "task", "Move backups off-container", status="open", area="ops"),
    note("tnd-doin0002", "task", "Rotate the deploy key", status="doing", area="ops"),
    note("tnd-done0003", "task", "Close the old bucket", status="done", area="ops"),
    note("plan-camp0004", "plan", "The migration campaign", status="open"),
    note(
        "tnd-find0005",
        "finding",
        "In-container backup cannot work",
        body="The container is **the thing** being backed up. See [[tnd-open0001]].",
        **{"event-date": "2026-07-26", "area": "infra"},
    ),
    note("tnd-inbo0006", "inbox", "Unfiled thing"),
    note("tnd-shel0007", "task", "Someday work", status="shelved"),
    note(SHARED_ID, "task", "Shared id in alpha", status="open"),
]

BETA_RECORDS = [
    note("tnd-beta0009", "task", "Beta work", status="open"),
    note(SHARED_ID, "task", "Shared id in beta", status="open"),
    # The corpus's only blocked record, and it is in beta rather than alpha on
    # purpose: the overview gives a blocked project a different edge from a
    # merely busy one, and two projects that both had blocked work could not
    # tell those two edges apart.
    note("tnd-blok0010", "task", "Beta is stuck", status="blocked"),
]

# A registered project holding nothing with a lifecycle. The overview draws it
# as a quiet card, and the rule it proves is that no project is ever hidden for
# having no live work.
GAMMA_RECORDS = [
    note("tnd-quie0011", "finding", "Gamma keeps notes and nothing else"),
]


@pytest.fixture
def corpus() -> None:
    """Three projects, one shared record id, one headline, one relation.

    Between them the lanes cover every edge the overview draws: alpha has work
    in flight and nothing stuck, beta has something stuck, gamma has neither.
    """
    import asyncio

    from basic_memory.services.headline import set_headline

    asyncio.run(
        _seed(
            {ALPHA: ALPHA_RECORDS, BETA: BETA_RECORDS, GAMMA: GAMMA_RECORDS},
            {ALPHA: ALPHA_ID, BETA: BETA_ID, GAMMA: GAMMA_ID},
            # The finding points at a task, so the finding's page shows an
            # outgoing reference and the task's page shows the incoming one.
            [("relates_to", (ALPHA, "tnd-find0005"), (ALPHA, "tnd-open0001"))],
        )
    )
    set_headline(ALPHA_ID, "ship the board")


@pytest_asyncio.fixture
async def client(corpus) -> AsyncGenerator[AsyncClient, None]:
    """The board server, driven in-process over ASGI.

    `lifespan_context` is entered by hand because `ASGITransport` does not run
    lifespan events — and this app does all of its setup there, so a client
    without it would hit routes with no session maker on `app.state`.
    """
    from basic_memory.web.app import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://board") as http:
            yield http
