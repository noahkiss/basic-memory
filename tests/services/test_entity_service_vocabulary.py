"""Vocabulary enforcement in the entity write path (GAPS W4).

Real code paths throughout: nothing here mocks the checker, because the thing
under test is that every mutator actually reaches it.

A project is governed only when ``store/<external_id>/vocabulary.yml`` exists.
``EntityService`` caches that answer for the life of the instance, so every test
governs the project *before* the first write.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.exceptions import VocabularyViolationError
from basic_memory.vocabulary.model import vocabulary_path

# The common four every record needs, plus a permalink that equals the id
# byte-for-byte. The permalink resolver honours an unclaimed frontmatter
# permalink verbatim, so these two stay equal through the write.
BASE_FRONTMATTER: Mapping[str, str] = {
    "id": "tnd-0001",
    "permalink": "tnd-0001",
    "source": "agent",
}

# What each type needs beyond the common four, from the checker's rules.
EXTRA_BY_TYPE: Mapping[str, Mapping[str, str]] = {
    "task": {"status": "open"},
    "guide": {"review-by": "2027-01-01"},
    "finding": {
        "event-date": "2026-01-15",
        "review-by": "2027-01-01",
        "date-source": "inline",
        "date-confidence": "exact",
    },
    "profile": {},
    "state": {},
    "inbox": {},
}


def note_content(body: str, **fields: Any) -> str:
    """Build note markdown carrying an explicit frontmatter block.

    ``schema_to_markdown`` merges a content frontmatter block into the written
    note, so this is how a test states the frontmatter a write will persist.
    """
    dumped = yaml.safe_dump(dict(fields), sort_keys=True)
    return f"---\n{dumped}---\n\n{body}\n"


@pytest.fixture
def govern_project(test_project):
    """Return a callable that gives the test project a vocabulary file.

    Call it before the first write: the service resolves the vocabulary once and
    caches it, which is deliberate (see ``_project_vocabulary``).
    """

    def _govern(**content: Any) -> Path:
        path = vocabulary_path(test_project.external_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # An empty mapping is a present, deliberate opt-in: it governs with the
        # default six types and five statuses.
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
        return path

    return _govern


@pytest.mark.asyncio
async def test_ungoverned_project_write_paths_are_untouched(entity_service, test_project) -> None:
    """No vocabulary.yml means no rule applies, and every write path is unchanged.

    This is the gate. An absent file must mean "not governed", never "use the
    defaults" — the entity parser types every note ``note`` by default, so
    defaulting-when-absent would reject every existing write on the spot.

    Its positive control is
    ``test_governed_project_rejects_off_vocabulary_type``: the identical write
    below is refused once a vocabulary file exists.
    """
    assert not vocabulary_path(test_project.external_id).exists()

    created = await entity_service.create_entity(
        EntitySchema(
            title="Ungoverned Note",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )
    assert created.note_type == "note"

    updated = await entity_service.update_entity(
        created,
        EntitySchema(
            title="Ungoverned Note",
            directory="notes",
            note_type="note",
            content="Replaced body",
        ),
    )
    assert updated.id == created.id

    edited = await entity_service.edit_entity(
        identifier=updated.permalink,
        operation="append",
        content="Appended line",
    )
    persisted = await entity_service.file_service.read_file_content(Path(edited.file_path))
    assert "Appended line" in persisted


@pytest.mark.asyncio
async def test_governed_project_rejects_off_vocabulary_type(
    entity_service, govern_project, file_service
) -> None:
    """The agent write path refuses an off-vocabulary type and teaches the set.

    ``type: note`` is the entity parser's default, and under a vocabulary it is
    an off-vocabulary type like any other: there is no ungoverned seventh type.
    """
    govern_project()

    with pytest.raises(VocabularyViolationError) as excinfo:
        await entity_service.create_entity(
            EntitySchema(
                title="Off Vocabulary",
                directory="notes",
                note_type="note",
                content=note_content("Body", **BASE_FRONTMATTER),
            )
        )

    message = str(excinfo.value)
    # The picking question is the part an agent can act on at the moment of
    # filing; the bare type names alone were what W19 opened over.
    assert "task (do it)" in message
    assert "guide (consult it)" in message
    assert "finding (learned it)" in message
    assert "inbox" in message

    # A rejection must leave nothing behind on disk: a refused write that still
    # wrote the file is worse than no rejection at all.
    assert not list((file_service.base_path / "notes").glob("*.md"))


@pytest.mark.asyncio
async def test_governed_project_rejects_set_once_change_on_update(
    entity_service, govern_project
) -> None:
    """A full replacement may not rewrite a set-once field."""
    govern_project()

    created = await entity_service.create_entity(
        EntitySchema(
            title="Set Once Guide",
            directory="notes",
            note_type="guide",
            content=note_content("Body", **BASE_FRONTMATTER, **EXTRA_BY_TYPE["guide"]),
        )
    )

    with pytest.raises(VocabularyViolationError) as excinfo:
        await entity_service.update_entity(
            created,
            EntitySchema(
                title="Set Once Guide",
                directory="notes",
                note_type="guide",
                content=note_content("Replaced body", source="human"),
            ),
        )

    message = str(excinfo.value)
    assert "'source' is set once" in message
    assert "'agent'" in message and "'human'" in message


@pytest.mark.asyncio
async def test_sync_path_records_the_violation_and_still_indexes(
    entity_service, govern_project
) -> None:
    """A hand-edited off-vocabulary file is indexed, never refused.

    Refusing the index would make the file invisible to search *and* to
    ``bm doctor`` — on disk, unfindable, and silent.
    """
    govern_project()

    now = datetime.now(timezone.utc)
    markdown = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={"title": "Hand Edited", "type": "note", "permalink": "hand-edited"}
        ),
        content="Body a human typed",
        created=now,
        modified=now,
    )

    entity = await entity_service.upsert_entity_from_markdown(
        Path("notes/hand-edited.md"),
        markdown,
        is_new=True,
    )

    assert entity.permalink == "hand-edited"
    assert entity.note_type == "note"
    assert await entity_service.get_by_permalink("hand-edited")


@pytest.mark.asyncio
async def test_advisory_does_not_block_a_write(entity_service, govern_project) -> None:
    """An undeclared frontmatter key is flagged, never rejected.

    Frontmatter is Basic Memory's open metadata surface. Sprawl is a *type* and
    *value* problem, so unknown keys stay advisory.
    """
    govern_project()

    created = await entity_service.create_entity(
        EntitySchema(
            title="Advisory Only",
            directory="notes",
            note_type="state",
            content=note_content("Body", **BASE_FRONTMATTER, mood="cheerful"),
        )
    )

    assert created.entity_metadata["mood"] == "cheerful"


@pytest.mark.asyncio
@pytest.mark.parametrize("record_type", sorted(EXTRA_BY_TYPE))
async def test_valid_record_of_each_type_writes_cleanly(
    entity_service, govern_project, record_type: str
) -> None:
    """Every type in the default vocabulary has a shape that passes."""
    govern_project()

    created = await entity_service.create_entity(
        EntitySchema(
            title=f"Valid {record_type}",
            directory="notes",
            note_type=record_type,
            content=note_content("Body", **BASE_FRONTMATTER, **EXTRA_BY_TYPE[record_type]),
        )
    )

    assert created.note_type == record_type
    assert created.permalink == BASE_FRONTMATTER["permalink"]


@pytest.mark.asyncio
async def test_governed_project_rejects_an_edit_of_an_off_vocabulary_note(
    entity_service, govern_project
) -> None:
    """An edit builds on frontmatter that must itself be on vocabulary.

    An edit cannot introduce a set-once violation, but the note it edits may
    already be off vocabulary from a hand edit. Refusing is the intended answer:
    MCP ``edit_note`` reaches this path, and leaving it unchecked is exactly the
    hole a per-hook-point design leaves.
    """
    govern_project()

    file_path = Path("notes/hand-edited.md")
    await entity_service.file_service.write_file(
        file_path,
        note_content(
            "Body a human typed",
            title="Hand Edited",
            type="note",
            permalink="hand-edited",
        ),
    )

    now = datetime.now(timezone.utc)
    markdown = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={"title": "Hand Edited", "type": "note", "permalink": "hand-edited"}
        ),
        content="Body a human typed",
        created=now,
        modified=now,
    )
    # The sync path indexes it without complaint; the agent path then refuses to
    # build on it.
    await entity_service.upsert_entity_from_markdown(file_path, markdown, is_new=True)

    with pytest.raises(VocabularyViolationError):
        await entity_service.edit_entity(
            identifier="hand-edited",
            operation="append",
            content="An agent's addition",
        )
