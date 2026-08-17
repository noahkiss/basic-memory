"""Tests for the four hygiene queries `bm doctor` reports (GAPS W2, W5 item 5).

Each check gets a record that must match and a record that must not, because a
query over `json_extract` fails silently in both directions: a wrong JSON path
returns NULL and reports a clean corpus, and a loose predicate reports every
record in the project.
"""

from datetime import date, datetime, timedelta

import pytest

from basic_memory import db
from basic_memory.models import Project
from basic_memory.repository.entity_repository import EntityRepository


async def make_record(
    session_maker,
    project: Project,
    file_path: str,
    metadata: dict,
    *,
    updated_at: datetime | None = None,
) -> None:
    """Index one markdown record with the frontmatter mirror the checks read."""
    stamped = updated_at or datetime.now().astimezone()
    async with db.scoped_session(session_maker) as session:
        await EntityRepository(project_id=project.id).create(
            session,
            {
                "project_id": project.id,
                "title": file_path,
                "note_type": str(metadata.get("type", "note")),
                "permalink": file_path.removesuffix(".md"),
                "file_path": file_path,
                "content_type": "text/markdown",
                "entity_metadata": metadata,
                "created_at": stamped,
                "updated_at": stamped,
            },
        )


@pytest.mark.asyncio
async def test_find_review_due_records_reports_only_expired_reviews(
    session_maker, test_project: Project
):
    """A review-by in the past matches; today's and tomorrow's do not."""
    today = date(2026, 8, 16)
    await make_record(session_maker, test_project, "old.md", {"review-by": "2025-01-31"})
    await make_record(session_maker, test_project, "due.md", {"review-by": "2026-08-15"})
    await make_record(session_maker, test_project, "today.md", {"review-by": "2026-08-16"})
    await make_record(session_maker, test_project, "later.md", {"review-by": "2027-01-01"})
    await make_record(session_maker, test_project, "none.md", {"type": "note"})

    async with db.scoped_session(session_maker) as session:
        records = await EntityRepository(project_id=test_project.id).find_review_due_records(
            session, today
        )

    # Oldest first, and the detail carries the date that expired.
    assert [(record.file_path, record.detail) for record in records] == [
        ("old.md", "2025-01-31"),
        ("due.md", "2026-08-15"),
    ]


@pytest.mark.asyncio
async def test_find_inferred_date_records_reports_only_the_inferred_rung(
    session_maker, test_project: Project
):
    """`date-source: inferred` matches; every other rung is evidence somebody can re-open."""
    await make_record(
        session_maker,
        test_project,
        "guessed.md",
        {"type": "finding", "event-date": "2026-03-01", "date-source": "inferred"},
    )
    await make_record(
        session_maker,
        test_project,
        "sourced.md",
        {"type": "finding", "event-date": "2026-03-02", "date-source": "git"},
    )
    await make_record(session_maker, test_project, "undated.md", {"type": "note"})

    async with db.scoped_session(session_maker) as session:
        records = await EntityRepository(project_id=test_project.id).find_inferred_date_records(
            session
        )

    assert [(record.file_path, record.detail) for record in records] == [
        ("guessed.md", "2026-03-01")
    ]


@pytest.mark.asyncio
async def test_find_stale_state_records_reports_only_old_state_records(
    session_maker, test_project: Project
):
    """An old `state` record matches; a fresh one and an old `task` do not."""
    now = datetime.now().astimezone()
    cutoff = now - timedelta(days=30)
    await make_record(
        session_maker,
        test_project,
        "stale.md",
        {"type": "state"},
        updated_at=now - timedelta(days=90),
    )
    await make_record(
        session_maker,
        test_project,
        "fresh.md",
        {"type": "state"},
        updated_at=now - timedelta(days=1),
    )
    # Positive control on the type predicate: same age, wrong type.
    await make_record(
        session_maker,
        test_project,
        "old-task.md",
        {"type": "task"},
        updated_at=now - timedelta(days=90),
    )

    async with db.scoped_session(session_maker) as session:
        records = await EntityRepository(project_id=test_project.id).find_stale_state_records(
            session, cutoff
        )

    assert [record.file_path for record in records] == ["stale.md"]
    assert records[0].detail == (now - timedelta(days=90)).date().isoformat()


@pytest.mark.asyncio
async def test_find_inbox_records_reports_the_pile_and_its_proposals(
    session_maker, test_project: Project
):
    """Every inbox record is listed, with the type it proposes when it names one."""
    await make_record(
        session_maker,
        test_project,
        "a.md",
        {"type": "inbox", "proposed-type": "guide"},
    )
    await make_record(session_maker, test_project, "b.md", {"type": "inbox"})
    await make_record(session_maker, test_project, "c.md", {"type": "task"})

    async with db.scoped_session(session_maker) as session:
        records = await EntityRepository(project_id=test_project.id).find_inbox_records(session)

    assert [(record.file_path, record.detail) for record in records] == [
        ("a.md", "guide"),
        ("b.md", ""),
    ]


@pytest.mark.asyncio
async def test_hygiene_queries_stay_inside_their_project(
    session_maker, test_project: Project, project_repository
):
    """A matching record in another project must not appear in this one's report."""
    async with db.scoped_session(session_maker) as session:
        other = await project_repository.create(
            session,
            {
                "name": "other-hygiene-project",
                "path": "/tmp/other-hygiene-project",
                "is_active": True,
                "is_default": None,
            },
        )
    await make_record(
        session_maker,
        other,
        "theirs.md",
        {"type": "inbox", "review-by": "2020-01-01", "date-source": "inferred"},
    )

    async with db.scoped_session(session_maker) as session:
        repository = EntityRepository(project_id=test_project.id)
        assert await repository.find_inbox_records(session) == []
        assert await repository.find_review_due_records(session, date(2026, 8, 16)) == []
        assert await repository.find_inferred_date_records(session) == []
        assert await repository.find_stale_state_records(session, datetime.now().astimezone()) == []

        # Positive control: the other project's own report does see the record.
        theirs = await EntityRepository(project_id=other.id).find_inbox_records(session)
    assert [record.file_path for record in theirs] == ["theirs.md"]
