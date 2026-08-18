"""The integrity check that finds records whose file is gone (GAPS U10).

`rm` on a record's file leaves its row behind. `bm ls` still lists it, every
count still includes it, and `bm show` on the same id exits 1 saying the file is
missing — while `bm doctor` reported "No issues". These cover the two halves of
the check: the query that lists what is indexed, and the stat loop that decides
which of those rows have nothing behind them.

Real files under the project's own path, no mocks: the whole check is a
filesystem cross-check, so a stubbed `exists()` would prove nothing.
"""

from datetime import datetime
from pathlib import Path

import pytest

from basic_memory import db
from basic_memory.cli.direct import _missing_files
from basic_memory.models import Project
from basic_memory.repository.entity_repository import EntityRepository


async def index_record(session_maker, project: Project, file_path: str) -> None:
    """Index one markdown record, without writing its file."""
    stamped = datetime.now().astimezone()
    async with db.scoped_session(session_maker) as session:
        await EntityRepository(project_id=project.id).create(
            session,
            {
                "project_id": project.id,
                "title": file_path,
                "note_type": "finding",
                "permalink": file_path.removesuffix(".md"),
                "file_path": file_path,
                "content_type": "text/markdown",
                "entity_metadata": {"type": "finding"},
                "created_at": stamped,
                "updated_at": stamped,
            },
        )


def write_file(project: Project, file_path: str) -> None:
    target = Path(project.path) / file_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\ntype: finding\n---\n\nbody\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_list_indexed_files_returns_every_row_ordered_by_path(
    session_maker, test_project: Project
):
    """The query is deliberately unfiltered — the disk decides, not a predicate."""
    await index_record(session_maker, test_project, "findings/b.md")
    await index_record(session_maker, test_project, "findings/a.md")

    async with db.scoped_session(session_maker) as session:
        rows = await EntityRepository(project_id=test_project.id).list_indexed_files(session)

    assert [row.file_path for row in rows] == ["findings/a.md", "findings/b.md"]
    assert [row.permalink for row in rows] == ["findings/a", "findings/b"]


@pytest.mark.asyncio
async def test_missing_files_reports_only_the_row_whose_file_is_gone(
    session_maker, test_project: Project
):
    """A record on disk is clean; one deleted underneath its row is not."""
    await index_record(session_maker, test_project, "findings/kept.md")
    await index_record(session_maker, test_project, "findings/deleted.md")
    write_file(test_project, "findings/kept.md")
    write_file(test_project, "findings/deleted.md")
    (Path(test_project.path) / "findings/deleted.md").unlink()

    async with db.scoped_session(session_maker) as session:
        rows = await EntityRepository(project_id=test_project.id).list_indexed_files(session)

    missing = _missing_files(Path(test_project.path), rows)

    assert [row.file_path for row in missing] == ["findings/deleted.md"]
    assert missing[0].permalink == "findings/deleted"


@pytest.mark.asyncio
async def test_missing_files_reports_nothing_when_every_file_is_present(
    session_maker, test_project: Project
):
    """Positive control's other half: a whole corpus on disk reports clean.

    Without this, the check above could be passing because the stat loop reports
    everything, which would make `bm doctor` cry wolf on every healthy project.
    """
    await index_record(session_maker, test_project, "findings/one.md")
    await index_record(session_maker, test_project, "findings/two.md")
    write_file(test_project, "findings/one.md")
    write_file(test_project, "findings/two.md")

    async with db.scoped_session(session_maker) as session:
        rows = await EntityRepository(project_id=test_project.id).list_indexed_files(session)

    assert _missing_files(Path(test_project.path), rows) == []
