"""What a move does to a note's search rows, and to files that are not notes.

The second is a real regression the GAPS T22 funnel move introduced: routing the
directory batch through the markdown-only accepted path left every binary in the
directory behind (GAPS T26). The first is not. GAPS T25 filed it as one, and
re-review found the premise wrong — the accept path materializes the moved file
and reindexes it synchronously, which rebuilds those rows. The test stays for the
one thing nothing else covers: the rows must carry the note's **new** path.

Both drive the real MCP path — ``move_note`` over the live ASGI app — because the
whole lesson of T22 is that a test against a layer proves nothing about what
callers get.
"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.mcp.tools import move_note, write_note
from basic_memory.models import Entity
from basic_memory.schemas.search import SearchItemType

NOTE_WITH_GRAPH = """\
# Observed Note

- [finding] the backup ran at midnight
- relates_to [[Some Other Note]]
"""


async def index_through_search_service(
    entity: Entity,
    *,
    search_service,
) -> None:
    """Index one note the way the local file pass does.

    Belt and braces, not a fixup: ``write_note`` already leaves these rows behind,
    because the accept path materializes the file and indexes it synchronously in
    the same request. Calling it again is idempotent — ``index_entity_data``
    deletes the note's rows and reinserts them — and it states plainly what the
    pre-move state is meant to be, so the positive control below cannot pass by
    accident.
    """
    await search_service.index_entity(entity)


async def search_rows(
    session_maker: async_sessionmaker[AsyncSession],
    search_service,
    item_type: SearchItemType,
) -> list:
    async with db.scoped_session(session_maker) as session:
        return await search_service.repository.search(
            search_item_types=[item_type],
            session=session,
        )


@pytest.mark.asyncio
async def test_move_keeps_observation_and_relation_search_rows(
    app,
    test_project,
    entity_repository,
    session_maker,
    search_service,
) -> None:
    """A move must leave the note's observations and relations searchable, at the new path.

    A behaviour test, not a regression test: what satisfies it is the accept
    path's own materialization reindex (GAPS T25, closed with no code change).
    What it pins down is that a move never leaves a stale ``file_path`` on these
    rows, which no other test asserts.
    """
    await write_note(
        project=test_project.name,
        title="Observed Note",
        directory="notes",
        content=NOTE_WITH_GRAPH,
    )

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "notes/Observed Note.md")
        assert entity is not None

    # Constraint: the test pool holds one connection. ``index_entity`` opens its
    # own session, so it must run after this one closes or it deadlocks.
    await index_through_search_service(entity, search_service=search_service)

    # Positive control: without this the assertions after the move could pass on
    # a note that never had these rows at all.
    before_observations = await search_rows(
        session_maker, search_service, SearchItemType.OBSERVATION
    )
    before_relations = await search_rows(session_maker, search_service, SearchItemType.RELATION)
    assert len(before_observations) == 1
    assert len(before_relations) == 1

    result = await move_note(
        identifier="notes/Observed Note.md",
        destination_path="archive/Observed Note.md",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(result, dict)
    assert result["moved"] is True

    after_observations = await search_rows(
        session_maker, search_service, SearchItemType.OBSERVATION
    )
    after_relations = await search_rows(session_maker, search_service, SearchItemType.RELATION)

    assert len(after_observations) == 1
    assert len(after_relations) == 1
    # The rows must follow the note, not merely survive it.
    assert after_observations[0].file_path == "archive/Observed Note.md"
    assert after_relations[0].file_path == "archive/Observed Note.md"
    assert "backup ran at midnight" in after_observations[0].content_snippet


@pytest.mark.asyncio
async def test_directory_move_carries_a_non_markdown_file(
    app,
    test_project,
    entity_repository,
    session_maker,
) -> None:
    """A directory move must take the binaries in the directory with it.

    The accepted note path is markdown-only and refuses anything else with a 415,
    which is right for a single ``move_note`` and wrong for a directory: refusing
    leaves the file behind while everything around it moves (GAPS T26).
    """
    project_home = Path(test_project.path)
    binary_path = project_home / "assets" / "diagram.png"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_bytes(b"\x89PNG\r\n\x1a\n not really a png")

    await write_note(
        project=test_project.name,
        title="Asset Note",
        directory="assets",
        content="# Asset Note\n\nA note that lives beside the image.\n",
    )

    async with db.scoped_session(session_maker) as session:
        await entity_repository.add(
            session,
            Entity(
                project_id=test_project.id,
                title="diagram.png",
                note_type="file",
                content_type="image/png",
                permalink="assets/diagram-png",
                file_path="assets/diagram.png",
                checksum="0" * 64,
            ),
        )

    result = await move_note(
        identifier="assets",
        destination_path="archive",
        is_directory=True,
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["total_files"] == 2
    assert result["failed_moves"] == 0
    assert result["successful_moves"] == 2

    assert (project_home / "archive" / "diagram.png").exists()
    assert not (project_home / "assets" / "diagram.png").exists()

    async with db.scoped_session(session_maker) as session:
        moved = await entity_repository.get_by_permalink(session, "assets/diagram-png")
        assert moved is not None
        assert moved.file_path == "archive/diagram.png"
        # The permalink is untouched: a binary has no frontmatter to derive one
        # from, and rewriting it would orphan every relation bound to it.
        assert moved.permalink == "assets/diagram-png"
