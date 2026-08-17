"""Vocabulary enforcement on the sync path (GAPS W4, narrowed by GAPS T22).

``EntityService`` reaches the funnel in **record** mode and no other. It is the
sync/watcher path: it reads files a human may have hand-edited, and a file
refused an index is on disk, unfindable, and silent. Reject mode lives in the
accepted-note mutation runner, which is where every agent-facing write lands, and
``tests/mcp/test_tool_vocabulary_enforcement.py`` proves it there over the real
MCP path. The reject cases this file used to hold moved there; none were dropped.

Real code paths throughout: nothing here mocks the checker.

A project is governed only when ``store/<external_id>/vocabulary.yml`` exists.
``EntityService`` caches that answer for the life of the instance, so every test
governs the project *before* the first write.
"""

from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from loguru import logger

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.schemas import Entity as EntitySchema
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


@pytest.fixture
def logged_warnings() -> Iterator[list[str]]:
    """Collect loguru warnings for the duration of one test.

    The funnel reports in record mode by logging, and loguru does not feed
    pytest's ``caplog``. A sink is the only way to read what it said.
    """
    collected: list[str] = []
    sink_id = logger.add(collected.append, level="WARNING")
    try:
        yield collected
    finally:
        logger.remove(sink_id)


@pytest.mark.asyncio
async def test_ungoverned_project_write_paths_are_untouched(entity_service, test_project) -> None:
    """No vocabulary.yml means no rule applies, and every write path is unchanged.

    This is the gate. An absent file must mean "not governed", never "use the
    defaults" — the entity parser types every note ``note`` by default, so
    defaulting-when-absent would reject every existing write on the spot.

    Its positive control is
    ``test_service_write_records_an_off_vocabulary_type_and_still_writes``: the
    identical write below is flagged once a vocabulary file exists.
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
async def test_service_write_records_an_off_vocabulary_type_and_still_writes(
    entity_service, govern_project, file_service, logged_warnings
) -> None:
    """EntityService records; it does not refuse. That is now its whole contract.

    It is the sync path, and its rejecting days ended with GAPS T22: every
    agent-facing write reaches the accepted-note runner instead. The positive
    control for the refusal is in
    ``tests/mcp/test_tool_vocabulary_enforcement.py``, where this same
    off-vocabulary content over the real agent path is refused outright.
    """
    govern_project()

    created = await entity_service.create_entity(
        EntitySchema(
            title="Off Vocabulary",
            directory="notes",
            note_type="note",
            content=note_content("Body", **BASE_FRONTMATTER),
        )
    )

    assert created.note_type == "note"
    assert list((file_service.base_path / "notes").glob("*.md"))
    assert any("is not in this project's vocabulary" in line for line in logged_warnings)


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
async def test_sync_path_records_a_set_once_change_and_still_indexes(
    entity_service, govern_project, logged_warnings
) -> None:
    """A hand edit that rewrites a set-once field is recorded, never refused.

    §4 says a human editing a file by hand is not an error. `bm doctor` reports
    what it broke; the index stays complete either way. Refusing here is the
    accepted-note runner's job, and it does it on the agent path only.
    """
    govern_project()

    now = datetime.now(timezone.utc)
    file_path = Path("notes/hand-edited.md")

    def markdown(source: str) -> EntityMarkdown:
        return EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "title": "Hand Edited",
                    "type": "guide",
                    "id": "tnd-0001",
                    "permalink": "tnd-0001",
                    "review-by": "2027-01-01",
                    "source": source,
                }
            ),
            content="Body a human typed",
            created=now,
            modified=now,
        )

    await entity_service.upsert_entity_from_markdown(file_path, markdown("agent"), is_new=True)

    entity = await entity_service.upsert_entity_from_markdown(
        file_path, markdown("human"), is_new=False
    )

    assert entity.entity_metadata["source"] == "human"
    assert any("'source' is set once" in line for line in logged_warnings)
