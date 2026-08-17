"""Tests for the ViolationRepository (GAPS W5 mechanism A)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.models import Entity, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.violation_repository import ViolationRepository
from basic_memory.vocabulary import Severity
from basic_memory.vocabulary import Violation as CheckedViolation


def checked(rule: str, field: str, severity: Severity = "error") -> CheckedViolation:
    """Build a checker violation with a message that names its rule."""
    return CheckedViolation(
        rule=rule,
        field=field,
        message=f"{rule} on '{field}'",
        severity=severity,
    )


async def make_entity(
    session: AsyncSession, entity_repository: EntityRepository, title: str, file_path: str
) -> Entity:
    """Create a markdown entity in the repository's project."""
    return await entity_repository.create(
        session,
        {
            "project_id": entity_repository.project_id,
            "title": title,
            "note_type": "test",
            "permalink": f"test/{file_path}",
            "file_path": file_path,
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )


@pytest.mark.asyncio
async def test_replace_for_entity_writes_and_lists_rows(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """A check's violations land as rows, joined back to the entity's file path."""
    repository = ViolationRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        written = await repository.replace_for_entity(
            session,
            sample_entity.id,
            test_project.id,
            [checked("unknown-type", "type"), checked("unknown-key", "owner", "advisory")],
        )

        rows = await repository.list_for_project(session, test_project.id)

    assert written == 2
    assert [(row.rule, row.field, row.severity) for row in rows] == [
        ("unknown-key", "owner", "advisory"),
        ("unknown-type", "type", "error"),
    ]
    assert {row.file_path for row in rows} == {sample_entity.file_path}


@pytest.mark.asyncio
async def test_replace_for_entity_with_empty_list_clears(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """A record that now checks clean must leave no rows behind."""
    repository = ViolationRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        await repository.replace_for_entity(
            session, sample_entity.id, test_project.id, [checked("unknown-type", "type")]
        )

    async with db.scoped_session(session_maker) as session:
        cleared = await repository.replace_for_entity(
            session, sample_entity.id, test_project.id, []
        )
        rows = await repository.list_for_project(session, test_project.id)

    assert cleared == 0
    assert rows == []


@pytest.mark.asyncio
async def test_repeat_check_collapses_onto_one_row_per_rule_and_field(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Re-checking an unchanged record leaves one row per rule+field, not two."""
    repository = ViolationRepository(project_id=test_project.id)
    violations = [checked("unknown-type", "type"), checked("missing-required-field", "source")]

    async with db.scoped_session(session_maker) as session:
        await repository.replace_for_entity(session, sample_entity.id, test_project.id, violations)

    async with db.scoped_session(session_maker) as session:
        await repository.replace_for_entity(session, sample_entity.id, test_project.id, violations)
        rows = await repository.list_for_project(session, test_project.id)

    assert len(rows) == 2


@pytest.mark.asyncio
async def test_duplicate_rule_and_field_is_rejected_by_the_unique_constraint(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """The collapse is enforced by the DB, not only by delete-then-insert."""
    repository = ViolationRepository(project_id=test_project.id)

    with pytest.raises(IntegrityError):
        async with db.scoped_session(session_maker) as session:
            await repository.replace_for_entity(
                session,
                sample_entity.id,
                test_project.id,
                [checked("unknown-type", "type"), checked("unknown-type", "type")],
            )


@pytest.mark.asyncio
async def test_entity_delete_removes_violations_by_orm_and_by_sql(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
):
    """Both live delete paths take the entity's violations with them."""
    repository = ViolationRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        orm_entity = await make_entity(session, entity_repository, "ORM", "orm.md")
        sql_entity = await make_entity(session, entity_repository, "SQL", "sql.md")
        await repository.replace_for_entity(
            session, orm_entity.id, test_project.id, [checked("unknown-type", "type")]
        )
        await repository.replace_for_entity(
            session, sql_entity.id, test_project.id, [checked("unknown-type", "type")]
        )

    async with db.scoped_session(session_maker) as session:
        await entity_repository.delete(session, orm_entity.id)

    async with db.scoped_session(session_maker) as session:
        # The DB-side ON DELETE CASCADE, exercised without the ORM in the path.
        await session.execute(text("DELETE FROM entity WHERE id = :id"), {"id": sql_entity.id})

    async with db.scoped_session(session_maker) as session:
        remaining = await repository.count_for_projects(session, [test_project.id])

    assert remaining == 0


@pytest.mark.asyncio
async def test_deleting_one_entity_leaves_another_entitys_rows(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
):
    """Positive control: the cascade is scoped to the deleted entity."""
    repository = ViolationRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        doomed = await make_entity(session, entity_repository, "Doomed", "doomed.md")
        survivor = await make_entity(session, entity_repository, "Survivor", "survivor.md")
        await repository.replace_for_entity(
            session, doomed.id, test_project.id, [checked("unknown-type", "type")]
        )
        await repository.replace_for_entity(
            session, survivor.id, test_project.id, [checked("missing-required-field", "source")]
        )

    async with db.scoped_session(session_maker) as session:
        await entity_repository.delete(session, doomed.id)

    async with db.scoped_session(session_maker) as session:
        rows = await repository.list_for_project(session, test_project.id)

    assert [(row.file_path, row.rule) for row in rows] == [
        ("survivor.md", "missing-required-field")
    ]


@pytest.mark.asyncio
async def test_count_by_reason_orders_by_count(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
):
    """The notice's top reason is the commonest rule+field pair."""
    repository = ViolationRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        for index in range(3):
            entity = await make_entity(session, entity_repository, f"Note {index}", f"n{index}.md")
            violations = [checked("unknown-type", "type")]
            if index == 0:
                violations.append(checked("missing-required-field", "source"))
            await repository.replace_for_entity(session, entity.id, test_project.id, violations)

        reasons = await repository.count_by_reason(session, [test_project.id])

    assert [(reason.rule, reason.field, reason.count) for reason in reasons] == [
        ("unknown-type", "type", 3),
        ("missing-required-field", "source", 1),
    ]


@pytest.mark.asyncio
async def test_counts_and_reasons_are_scoped_to_the_projects_asked_for(session_maker, config_home):
    """Each project sees its own rows; a roll-up over both sees all of them."""
    project_repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        project_one = await project_repository.create(
            session,
            {"name": "violations-one", "path": str(config_home / "one"), "is_active": True},
        )
        project_two = await project_repository.create(
            session,
            {"name": "violations-two", "path": str(config_home / "two"), "is_active": True},
        )

        repository_one = ViolationRepository(project_id=project_one.id)
        repository_two = ViolationRepository(project_id=project_two.id)
        entity_one = await make_entity(
            session, EntityRepository(project_id=project_one.id), "One", "one.md"
        )
        entity_two = await make_entity(
            session, EntityRepository(project_id=project_two.id), "Two", "two.md"
        )
        await repository_one.replace_for_entity(
            session, entity_one.id, project_one.id, [checked("unknown-type", "type")]
        )
        await repository_two.replace_for_entity(
            session,
            entity_two.id,
            project_two.id,
            [checked("unknown-status", "status"), checked("unknown-area", "area")],
        )

        count_one = await repository_one.count_for_projects(session, [project_one.id])
        count_both = await repository_one.count_for_projects(
            session, [project_one.id, project_two.id]
        )
        reasons_one = await repository_one.count_by_reason(session, [project_one.id])
        rows_two = await repository_two.list_for_project(session, project_two.id)

    assert count_one == 1
    assert count_both == 3
    assert [reason.rule for reason in reasons_one] == ["unknown-type"]
    assert [row.rule for row in rows_two] == ["unknown-area", "unknown-status"]


@pytest.mark.asyncio
async def test_list_for_project_filters_by_severity(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Doctor's integrity section reads errors; its hygiene section reads advisories."""
    repository = ViolationRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        await repository.replace_for_entity(
            session,
            sample_entity.id,
            test_project.id,
            [checked("unknown-type", "type"), checked("unknown-key", "owner", "advisory")],
        )

        errors = await repository.list_for_project(session, test_project.id, severity="error")
        advisories = await repository.list_for_project(
            session, test_project.id, severity="advisory"
        )

    assert [row.rule for row in errors] == ["unknown-type"]
    assert [row.rule for row in advisories] == ["unknown-key"]


@pytest.mark.asyncio
async def test_empty_project_scope_reads_nothing(session_maker, test_project: Project):
    """An empty project list is an empty answer, not an unfiltered query."""
    repository = ViolationRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        assert await repository.count_for_projects(session, []) == 0
        assert await repository.count_by_reason(session, []) == []
