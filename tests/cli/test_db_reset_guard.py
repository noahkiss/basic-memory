"""Tests for the bm reset unflushed-note-content guard (GAPS.md T12).

While a note_content row is pending/writing/failed, the database row is the
only copy of that note. `bm reset` must flush those rows to disk before it
deletes anything, and refuse (absent --force) when flushing leaves any behind.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import typer

from basic_memory import db, file_utils
from basic_memory.cli.commands.db import (
    _abort_or_warn_unflushed,
    _flush_unflushed_note_content,
    _unflushed_note_content,
)
from basic_memory.models import Entity
from basic_memory.models.knowledge import NoteContent


async def _seed_note(session_maker, project_id: int, name: str, status: str, content: str) -> str:
    """Create an entity + note_content row in the given write status; return file_path."""
    now = datetime.now(timezone.utc)
    file_path = f"t12/{name}.md"
    checksum = await file_utils.compute_checksum(content)
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=project_id,
            title=name,
            note_type="note",
            permalink=f"t12/{name}",
            file_path=file_path,
            content_type="text/markdown",
            created_at=now,
            updated_at=now,
        )
        session.add(entity)
        await session.flush()
        session.add(
            NoteContent(
                entity_id=entity.id,
                project_id=project_id,
                external_id=f"t12-{name}",
                file_path=file_path,
                markdown_content=content,
                db_version=1,
                db_checksum=checksum,
                file_write_status=status,
            )
        )
    return file_path


@pytest.mark.asyncio
async def test_unflushed_query_reports_only_unwritten_rows(session_maker, test_project):
    await _seed_note(session_maker, test_project.id, "stuck", "pending", "# stuck body")
    await _seed_note(session_maker, test_project.id, "written", "synced", "# written body")

    rows = await _unflushed_note_content(session_maker)

    assert rows == [(test_project.name, "t12/stuck.md", "pending")]


def test_abort_or_warn_refuses_without_force():
    with pytest.raises(typer.Exit) as excinfo:
        _abort_or_warn_unflushed([("proj", "a/b.md", "pending")], force=False)
    assert excinfo.value.exit_code == 1


def test_abort_or_warn_proceeds_with_force_or_clean():
    # Empty list: nothing to do either way.
    _abort_or_warn_unflushed([], force=False)
    # Rows + force: warns but does not raise — the caller accepts the loss.
    _abort_or_warn_unflushed([("proj", "a/b.md", "failed")], force=True)


@pytest.mark.asyncio
async def test_flush_writes_pending_content_to_disk(
    session_maker, test_project, app_config, config_manager
):
    """The guard's flush half: a pending row reaches disk and drops off the list."""
    body = "---\ntitle: stuck\n---\n# IRREPLACEABLE-T12-BODY\n"
    file_path = await _seed_note(session_maker, test_project.id, "stuck", "pending", body)

    remaining = await _flush_unflushed_note_content(app_config)

    assert remaining == []
    written = Path(test_project.path) / file_path
    assert written.exists(), "flush did not materialize the pending note to disk"
    assert "IRREPLACEABLE-T12-BODY" in written.read_text()
