"""Revalidation when a project's vocabulary changes (GAPS W5 item 4).

Violations are persisted when a record is indexed or moved, so they go stale in
exactly one situation: the rules changed and the records did not. Nothing on the
index path would look again — no file's mtime moved — which is what this trigger
is for.

Every test drives the real function against a real database and a real
``vocabulary.yml`` on disk. The rows are read back through the repository the
report surfaces will use, never asserted from a spy.
"""

from datetime import datetime, timezone
from typing import Any

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.models import Entity, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.violation_repository import ViolationRepository
from basic_memory.services.vocabulary_revalidation import (
    revalidate_if_vocabulary_changed,
    vocabulary_stamp,
)
from basic_memory.vocabulary import Violation as CheckedViolation
from basic_memory.vocabulary import VocabularyError, vocabulary_path

# A record with a type no default vocabulary declares. `type` short-circuits the
# checker, so this produces exactly one violation and every count below is
# unambiguous.
OFF_VOCABULARY: dict[str, Any] = {
    "type": "runbook",
    "id": "tnd-w5-0001",
    "permalink": "tnd-w5-0001",
    "title": "Restore From Backup",
    "source": "human",
}

# The same shape with a type the defaults do declare, so it checks clean. The
# permalink moves with the id: they must match byte-for-byte (schema.md §2), and
# a distinct permalink is what lets both records live in one project.
ON_VOCABULARY: dict[str, Any] = {
    **OFF_VOCABULARY,
    "type": "state",
    "id": "tnd-w5-0002",
    "permalink": "tnd-w5-0002",
}


def govern(project: Project, **content: Any) -> str:
    """Write the project's vocabulary file and return its expected stamp."""
    path = vocabulary_path(project.external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # An empty mapping is a present, deliberate opt-in: it governs with defaults.
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return vocabulary_stamp(project.external_id)


async def make_entity(
    session: AsyncSession,
    entity_repository: EntityRepository,
    metadata: dict[str, Any],
    file_path: str,
) -> Entity:
    """Create an indexed record carrying ``metadata`` as its frontmatter."""
    return await entity_repository.create(
        session,
        {
            "project_id": entity_repository.project_id,
            "title": metadata["title"],
            "note_type": metadata["type"],
            "permalink": metadata["permalink"],
            "file_path": file_path,
            "content_type": "text/markdown",
            "entity_metadata": metadata,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )


async def stored_rules(session_maker, project: Project) -> list[tuple[str, str]]:
    """Every violation the project holds, as ``(rule, field)`` pairs."""
    async with db.scoped_session(session_maker) as session:
        rows = await ViolationRepository(project_id=project.id).list_for_project(
            session, project.id
        )
    return [(row.rule, row.field) for row in rows]


async def revalidate(session_maker, project: Project) -> int:
    """Run the trigger on its own session, the way a command would.

    Returns the record count alone so the assertions below stay arithmetic;
    the U39 upgrade flag has its own tests at the end of this file.
    """
    async with db.scoped_session(session_maker) as session:
        return (await revalidate_if_vocabulary_changed(session, project)).revalidated


async def stamp_of(session_maker, project: Project) -> str | None:
    """Re-read the project's stamp from the database, not from the object."""
    async with db.scoped_session(session_maker) as session:
        row = await session.get(Project, project.id)
        assert row is not None
        return row.vocabulary_stamp


@pytest.mark.asyncio
async def test_creating_a_vocabulary_populates_the_violations(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """A project that becomes governed gets checked, without any file changing.

    Nothing on the index path would do this: governing a project rewrites no
    note, so no note presents as modified.
    """
    async with db.scoped_session(session_maker) as session:
        await make_entity(session, entity_repository, OFF_VOCABULARY, "notes/off.md")
    govern(test_project)

    assert await revalidate(session_maker, test_project) == 1

    assert await stored_rules(session_maker, test_project) == [("unknown-type", "type")]
    # An empty mapping is value-equal to the defaults, so the cold pass
    # re-canonicalizes it (U39 order-insensitive detection); the stamp is the
    # rewritten file's, read after the fact rather than predicted before it.
    assert await stamp_of(session_maker, test_project) == vocabulary_stamp(test_project.external_id)


@pytest.mark.asyncio
async def test_an_unchanged_vocabulary_is_a_no_op(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """A matching stamp returns 0 and rewrites nothing.

    Asserted through a row a real check would have deleted, not through a spy: a
    stale row that survives is the only evidence that no check ran.
    """
    async with db.scoped_session(session_maker) as session:
        entity = await make_entity(session, entity_repository, ON_VOCABULARY, "notes/on.md")
    govern(test_project)
    assert await revalidate(session_maker, test_project) == 1
    assert await stored_rules(session_maker, test_project) == []

    # A row no rule would produce. A second real pass would clear it.
    async with db.scoped_session(session_maker) as session:
        await ViolationRepository(project_id=test_project.id).replace_for_entity(
            session,
            entity.id,
            test_project.id,
            [CheckedViolation(rule="stale", field="left-over", message="x", severity="error")],
        )

    assert await revalidate(session_maker, test_project) == 0
    assert await stored_rules(session_maker, test_project) == [("stale", "left-over")]


@pytest.mark.asyncio
async def test_an_edited_vocabulary_rewrites_every_record(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """Every record is re-checked, not only the ones that already had rows."""
    async with db.scoped_session(session_maker) as session:
        await make_entity(session, entity_repository, OFF_VOCABULARY, "notes/off.md")
        await make_entity(session, entity_repository, ON_VOCABULARY, "notes/on.md")
    govern(test_project)
    assert await revalidate(session_maker, test_project) == 2
    assert await stored_rules(session_maker, test_project) == [("unknown-type", "type")]

    # `state` goes away and `runbook` arrives, so both records change verdict.
    govern(test_project, types=["runbook"])

    assert await revalidate(session_maker, test_project) == 2
    assert await stored_rules(session_maker, test_project) == [("unknown-type", "type")]
    async with db.scoped_session(session_maker) as session:
        rows = await ViolationRepository(project_id=test_project.id).list_for_project(
            session, test_project.id
        )
    # The row moved to the other record: the one now legal is clean, and the one
    # that was legal is not.
    assert [row.file_path for row in rows] == ["notes/on.md"]


@pytest.mark.asyncio
async def test_adding_a_type_clears_the_rows_it_legalises(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """The case the trigger exists for: a human widens the vocabulary."""
    async with db.scoped_session(session_maker) as session:
        await make_entity(session, entity_repository, OFF_VOCABULARY, "notes/off.md")
    govern(test_project)
    assert await revalidate(session_maker, test_project) == 1
    assert await stored_rules(session_maker, test_project) == [("unknown-type", "type")]

    govern(test_project, types=["runbook"])

    assert await revalidate(session_maker, test_project) == 1
    assert await stored_rules(session_maker, test_project) == []


@pytest.mark.asyncio
async def test_deleting_the_vocabulary_clears_every_row_and_stamps_empty(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """An ungoverned project holds no violations, including the undecidable ones.

    Cleared in one statement rather than by the governed loop: with no rules
    there is no per-record verdict to compute, so a loop would only be one
    delete per record to reach the same state. The count still reports records
    decided, which is every record in the project.

    ``""`` is the stamp for "checked, not governed" — distinct from ``None``,
    which means nothing has checked it yet.
    """
    async with db.scoped_session(session_maker) as session:
        entity = await make_entity(session, entity_repository, OFF_VOCABULARY, "notes/off.md")
        await make_entity(session, entity_repository, ON_VOCABULARY, "notes/on.md")
    govern(test_project)
    assert await revalidate(session_maker, test_project) == 2

    # A rule revalidation cannot re-derive. It survives a vocabulary *edit* and
    # must not survive the vocabulary going away.
    async with db.scoped_session(session_maker) as session:
        await ViolationRepository(project_id=test_project.id).replace_for_entity(
            session,
            entity.id,
            test_project.id,
            [
                CheckedViolation(
                    rule="supersedes-not-on-type",
                    field="supersedes",
                    message="x",
                    severity="error",
                )
            ],
        )

    vocabulary_path(test_project.external_id).unlink()

    assert await revalidate(session_maker, test_project) == 2
    assert await stored_rules(session_maker, test_project) == []
    assert await stamp_of(session_maker, test_project) == ""


@pytest.mark.asyncio
async def test_deleting_the_vocabulary_clears_only_this_projects_rows(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
    project_repository: ProjectRepository,
) -> None:
    """The bulk clear is scoped to one project, and the neighbour keeps its rows.

    The positive control for the statement itself: a ``DELETE`` with no per-entity
    predicate is the one shape that can take another project's rows with it, and
    an assertion that this project ends up empty could never notice.
    """
    async with db.scoped_session(session_maker) as session:
        neighbour = await project_repository.create(
            session,
            {
                "name": "neighbour-project",
                "path": f"{test_project.path}/neighbour",
                "is_active": True,
            },
        )
        await make_entity(
            session,
            EntityRepository(project_id=neighbour.id),
            {**OFF_VOCABULARY, "id": "tnd-w5-0003", "permalink": "tnd-w5-0003"},
            "notes/neighbour.md",
        )
        await make_entity(session, entity_repository, OFF_VOCABULARY, "notes/off.md")

    govern(test_project)
    govern(neighbour)
    assert await revalidate(session_maker, test_project) == 1
    assert await revalidate(session_maker, neighbour) == 1
    assert await stored_rules(session_maker, neighbour) == [("unknown-type", "type")]

    vocabulary_path(test_project.external_id).unlink()
    assert await revalidate(session_maker, test_project) == 1

    assert await stored_rules(session_maker, test_project) == []
    async with db.scoped_session(session_maker) as session:
        rows = await ViolationRepository(project_id=neighbour.id).list_for_project(
            session, neighbour.id
        )
    assert [(row.file_path, row.rule) for row in rows] == [("notes/neighbour.md", "unknown-type")]


@pytest.mark.asyncio
async def test_rules_revalidation_cannot_decide_are_preserved(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """Rows a frontmatter-only check could never produce survive the rewrite.

    Revalidation reads ``entity_metadata`` alone: no parsed relations, and no
    previous write. Clearing what it cannot re-derive would silently drop the
    move planner's set-once row — the one GAPS T23 calls unrecoverable — and the
    supersession row, and nothing would put either back until the note changed.
    """
    async with db.scoped_session(session_maker) as session:
        entity = await make_entity(session, entity_repository, ON_VOCABULARY, "notes/on.md")
    govern(test_project)
    assert await revalidate(session_maker, test_project) == 1

    async with db.scoped_session(session_maker) as session:
        await ViolationRepository(project_id=test_project.id).replace_for_entity(
            session,
            entity.id,
            test_project.id,
            [
                CheckedViolation(
                    rule="set-once-changed", field="permalink", message="x", severity="error"
                ),
                CheckedViolation(
                    rule="supersedes-not-on-type",
                    field="supersedes",
                    message="x",
                    severity="error",
                ),
                CheckedViolation(
                    rule="unknown-key", field="owner", message="x", severity="advisory"
                ),
            ],
        )

    # `state` stays declared, so the record still checks clean and the only rows
    # left are the ones this check could not have produced. Dropping `state` here
    # would add an `unknown-type` row and stop testing preservation.
    govern(test_project, types=["state", "runbook"])
    assert await revalidate(session_maker, test_project) == 1

    # `unknown-key` is decidable from frontmatter and this record has no such
    # key, so it goes; the other two stay.
    assert await stored_rules(session_maker, test_project) == [
        ("set-once-changed", "permalink"),
        ("supersedes-not-on-type", "supersedes"),
    ]


@pytest.mark.asyncio
async def test_a_malformed_vocabulary_raises_and_does_not_stamp(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """A typo must not become permanent silence.

    Stamping a file that could not be parsed would make the next call return 0,
    which is the state W4 refuses to conflate with "not governed".
    """
    async with db.scoped_session(session_maker) as session:
        await make_entity(session, entity_repository, OFF_VOCABULARY, "notes/off.md")
    vocabulary_path(test_project.external_id).parent.mkdir(parents=True, exist_ok=True)
    vocabulary_path(test_project.external_id).write_text("types: [unclosed\n", encoding="utf-8")

    with pytest.raises(VocabularyError):
        await revalidate(session_maker, test_project)

    assert await stamp_of(session_maker, test_project) is None
    assert await stored_rules(session_maker, test_project) == []


@pytest.mark.asyncio
async def test_the_stamp_changes_when_the_file_does(test_project: Project) -> None:
    """Positive control for every no-op assertion above.

    Without it, "the stamp matched so nothing ran" could equally mean the stamp
    never moves and the trigger can never fire.
    """
    first = govern(test_project)
    second = govern(test_project, types=["runbook"])

    assert first != ""
    assert first != second
    assert second == vocabulary_stamp(test_project.external_id)

    vocabulary_path(test_project.external_id).unlink()
    assert vocabulary_stamp(test_project.external_id) == ""


# --- U39: the auto-upgrade for untouched default snapshots ---

# The v0.1.3 generation (GAPS U39): what `--governed` serialized for the 48
# migrated projects — no `plan`, no `part_of`, no `aliases` key. Written here
# with deliberately un-canonical formatting, because detection must compare
# parsed values, never bytes.
SNAPSHOT_G4 = {
    "types": ["task", "guide", "finding", "profile", "state", "inbox", "note"],
    "statuses": ["open", "doing", "blocked", "shelved", "done", "dropped"],
    "areas": [],
    "relations": ["relates_to", "derived_from", "supersedes"],
    "review_months": 12,
    "fields": {},
}


@pytest.mark.asyncio
async def test_stamp_carries_the_defaults_fingerprint(test_project: Project) -> None:
    """A defaults change must invalidate stamps while the file holds still."""
    from basic_memory.vocabulary.model import defaults_fingerprint

    govern(test_project)

    assert vocabulary_stamp(test_project.external_id).endswith(f":{defaults_fingerprint()}")


@pytest.mark.asyncio
async def test_an_untouched_snapshot_upgrades_to_current_defaults(
    session_maker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """A file equal to a superseded generation is machine property and moves."""
    from basic_memory.vocabulary import load_vocabulary
    from basic_memory.vocabulary.model import DEFAULT_VOCABULARY

    async with db.scoped_session(session_maker) as session:
        await make_entity(session, entity_repository, ON_VOCABULARY, "notes/on.md")
    govern(test_project, **SNAPSHOT_G4)

    async with db.scoped_session(session_maker) as session:
        result = await revalidate_if_vocabulary_changed(session, test_project)

    assert result.upgraded is True
    assert load_vocabulary(test_project.external_id) == DEFAULT_VOCABULARY
    # The stamp was taken from the rewritten bytes, so the next call is warm.
    async with db.scoped_session(session_maker) as session:
        again = await revalidate_if_vocabulary_changed(session, test_project)
    assert again.revalidated == 0 and again.upgraded is False


@pytest.mark.asyncio
async def test_a_hand_edited_vocabulary_is_never_auto_touched(
    session_maker,
    test_project: Project,
) -> None:
    """One declaration of the human's own keeps the file theirs."""
    from basic_memory.vocabulary import vocabulary_path

    snapshot_types = SNAPSHOT_G4["types"]
    assert isinstance(snapshot_types, list)
    edited = {**SNAPSHOT_G4, "types": [*snapshot_types, "runbook"]}
    govern(test_project, **edited)
    before = vocabulary_path(test_project.external_id).read_bytes()

    async with db.scoped_session(session_maker) as session:
        result = await revalidate_if_vocabulary_changed(session, test_project)

    assert result.upgraded is False
    assert vocabulary_path(test_project.external_id).read_bytes() == before
