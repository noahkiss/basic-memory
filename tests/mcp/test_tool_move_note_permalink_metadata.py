"""A move that rewrites the permalink must rewrite the frontmatter mirror (GAPS T28).

``entity_metadata`` is the file's frontmatter, permalink included, and
``find_permalink_integrity_issues`` calls it **drift** when that copy disagrees
with the ``permalink`` column. Both move paths used to update the column alone,
so every routine move manufactured the signal the id check exists to raise on a
hand-edited ``permalink:`` — noise on any corpus that has ever been tidied.

This drives the real MCP path (``move_note`` over the live ASGI app). The
scan/watcher path has its own regression test in
``tests/index/test_local_project_index.py``.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.mcp.tools import move_note, write_note
from basic_memory.repository.entity_repository import EntityRepository

NOTE = "# Movable Note\n\nA note whose permalink follows its path.\n"


@pytest.mark.asyncio
async def test_accepted_move_keeps_the_frontmatter_permalink_mirror_current(
    app,
    app_config,
    test_project,
    entity_repository,
    session_maker,
) -> None:
    """After a permalink-rewriting move, the mirror agrees and no drift is reported."""
    app_config.update_permalinks_on_move = True

    await write_note(
        project=test_project.name,
        title="Movable Note",
        directory="notes",
        content=NOTE,
    )

    async with db.scoped_session(session_maker) as session:
        before = await entity_repository.get_by_file_path(session, "notes/Movable Note.md")
        assert before is not None
        # Positive control: the mirror carries a permalink before the move, so
        # an assertion about it after the move cannot pass on an absent key.
        assert before.entity_metadata["permalink"] == before.permalink
        original_permalink = before.permalink

    result = await move_note(
        identifier="notes/Movable Note.md",
        destination_path="archive/Movable Note.md",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(result, dict)
    assert result["moved"] is True

    async with db.scoped_session(session_maker) as session:
        moved = await entity_repository.get_by_file_path(session, "archive/Movable Note.md")
        assert moved is not None
        issues = await EntityRepository(project_id=test_project.id).find_permalink_integrity_issues(
            session
        )

    # The move did rewrite identity — without this the rest is vacuous.
    assert moved.permalink != original_permalink
    assert moved.entity_metadata["permalink"] == moved.permalink

    moved_file = Path(test_project.path, "archive/Movable Note.md").read_text(encoding="utf-8")
    assert f"permalink: {moved.permalink}" in moved_file

    assert [issue for issue in issues if issue.issue == "drift"] == []


@pytest.mark.asyncio
async def test_permalink_drift_is_still_reported_for_a_hand_edit(
    app,
    app_config,
    test_project,
    entity_repository,
    session_maker,
) -> None:
    """Positive control for the check itself: a stale mirror still reads as drift.

    Without this, "no drift after a move" could mean the query stopped working
    rather than the move stopped lying.
    """
    app_config.update_permalinks_on_move = True

    await write_note(
        project=test_project.name,
        title="Edited Note",
        directory="notes",
        content=NOTE,
    )

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "notes/Edited Note.md")
        assert entity is not None
        # Stand in for a human editing `permalink:` after first index.
        await session.execute(
            text(
                "UPDATE entity SET entity_metadata = json_set(entity_metadata, "
                "'$.permalink', :edited) WHERE id = :id"
            ),
            {"edited": "notes/hand-edited", "id": entity.id},
        )
        issues = await EntityRepository(project_id=test_project.id).find_permalink_integrity_issues(
            session
        )

    drift = [issue for issue in issues if issue.issue == "drift"]
    assert [issue.frontmatter_permalink for issue in drift] == ["notes/hand-edited"]
