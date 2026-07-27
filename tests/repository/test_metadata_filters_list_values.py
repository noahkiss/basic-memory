"""Regression coverage for metadata filters against list-valued and boolean frontmatter.

GAPS T1: a scalar `--meta key=value` query against list-valued frontmatter returned zero rows
and reported it as "no matches" -- indistinguishable from a genuine empty result. The same
silent miss hit YAML booleans, which are indexed as the string "True"/"False".
"""

from datetime import datetime, timezone
from typing import Any

import pytest

from basic_memory import db
from basic_memory.models.knowledge import Entity
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.schemas.search import SearchItemType


async def _index_entity(
    search_repository, session_maker, title: str, metadata: dict[str, Any]
) -> Entity:
    slug = "-".join(title.lower().split())
    now = datetime.now(timezone.utc)

    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=search_repository.project_id,
            title=title,
            note_type="note",
            permalink=f"notes/{slug}",
            file_path=f"notes/{slug}.md",
            content_type="text/markdown",
            entity_metadata=metadata,
            created_at=now,
            updated_at=now,
        )
        session.add(entity)
        await session.flush()

    await search_repository.index_item(
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=entity.id,
            type=SearchItemType.ENTITY.value,
            title=entity.title,
            content_stems="list valued frontmatter",
            content_snippet="list valued frontmatter",
            permalink=entity.permalink,
            file_path=entity.file_path,
            entity_id=entity.id,
            metadata={"note_type": entity.note_type},
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
    )
    return entity


@pytest.mark.asyncio
async def test_scalar_filter_matches_list_valued_frontmatter(search_repository, session_maker):
    """A scalar equality filter must match element-wise against a YAML list."""
    successor = await _index_entity(
        search_repository, session_maker, "Successor", {"supersedes": ["tnd_aaaa1111"]}
    )
    await _index_entity(
        search_repository, session_maker, "Unrelated", {"supersedes": ["tnd_zzzz9999"]}
    )

    results = await search_repository.search(metadata_filters={"supersedes": "tnd_aaaa1111"})

    assert {row.id for row in results} == {successor.id}


@pytest.mark.asyncio
async def test_scalar_filter_still_matches_scalar_frontmatter(search_repository, session_maker):
    """Element-wise matching must not weaken plain scalar equality."""
    active = await _index_entity(search_repository, session_maker, "Active", {"status": "active"})
    await _index_entity(search_repository, session_maker, "Done", {"status": "done"})

    results = await search_repository.search(metadata_filters={"status": "active"})

    assert {row.id for row in results} == {active.id}


@pytest.mark.asyncio
async def test_scalar_filter_does_not_match_substring_of_element(search_repository, session_maker):
    """Element-wise matching is exact -- a prefix must not be reported as a hit."""
    await _index_entity(
        search_repository, session_maker, "Prefixed", {"supersedes": ["tnd_aaaa1111_extra"]}
    )

    results = await search_repository.search(metadata_filters={"supersedes": "tnd_aaaa1111"})

    assert results == []


@pytest.mark.asyncio
async def test_boolean_filter_matches_yaml_boolean(search_repository, session_maker):
    """`--meta draft=true` arrives as the string "true"; the index holds "True"."""
    draft = await _index_entity(search_repository, session_maker, "Draft", {"draft": "True"})
    await _index_entity(search_repository, session_maker, "Published", {"draft": "False"})

    results = await search_repository.search(metadata_filters={"draft": "true"})

    assert {row.id for row in results} == {draft.id}


@pytest.mark.asyncio
async def test_boolean_filter_matches_quoted_string(search_repository, session_maker):
    """A quoted YAML scalar keeps the author's spelling; the same query must find it."""
    quoted = await _index_entity(search_repository, session_maker, "Quoted", {"draft": "true"})

    results = await search_repository.search(metadata_filters={"draft": True})

    assert {row.id for row in results} == {quoted.id}


@pytest.mark.asyncio
async def test_boolean_false_filter_matches_yaml_boolean(search_repository, session_maker):
    published = await _index_entity(
        search_repository, session_maker, "Published", {"draft": "False"}
    )
    await _index_entity(search_repository, session_maker, "Draft", {"draft": "True"})

    results = await search_repository.search(metadata_filters={"draft": "false"})

    assert {row.id for row in results} == {published.id}


@pytest.mark.asyncio
async def test_in_operator_matches_list_elements(search_repository, session_maker):
    """`$in` against a list-valued field is the OR (contains-any) the AND form cannot express."""
    first = await _index_entity(
        search_repository, session_maker, "First", {"supersedes": ["tnd_aaaa1111"]}
    )
    second = await _index_entity(
        search_repository, session_maker, "Second", {"supersedes": ["tnd_zzzz9999"]}
    )
    await _index_entity(search_repository, session_maker, "Third", {"supersedes": ["tnd_bbbb2222"]})

    results = await search_repository.search(
        metadata_filters={"supersedes": {"$in": ["tnd_aaaa1111", "tnd_zzzz9999"]}}
    )

    assert {row.id for row in results} == {first.id, second.id}


@pytest.mark.asyncio
async def test_contains_operator_matches_single_element(search_repository, session_maker):
    """`$contains` names the element-wise intent explicitly (GAPS B1)."""
    both = await _index_entity(
        search_repository,
        session_maker,
        "Both",
        {"supersedes": ["tnd_aaaa1111", "tnd_zzzz9999"]},
    )
    await _index_entity(search_repository, session_maker, "Other", {"supersedes": ["tnd_bbbb2222"]})

    results = await search_repository.search(
        metadata_filters={"supersedes": {"$contains": "tnd_aaaa1111"}}
    )

    assert {row.id for row in results} == {both.id}
