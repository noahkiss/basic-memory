"""Portable persistence handoffs for accepted note writes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

import frontmatter
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import file_utils
from basic_memory.indexing.accepted_note_search import (
    accepted_search_content_from_markdown,
    build_accepted_note_search_row,
)
from basic_memory.models import Entity, NoteContent
from basic_memory.repository import (
    AcceptedNoteContentWrite,
    AcceptedObservationWrite,
    AcceptedRelationWrite,
)
from basic_memory.repository.accepted_note_search_row import AcceptedNoteSearchRow
from basic_memory.repository.entity_repository import AcceptedPendingEntityWrite
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteContentWriteSource,
    RuntimeDeletedNoteFileChecksumSource,
    RuntimeDeletedNoteFileDeleteEntitySource,
    RuntimePendingNoteFileDelete,
    plan_accepted_note_content_write,
    plan_accepted_note_delete_change,
)
from basic_memory.runtime.storage import (
    ProjectId,
    RuntimeEntityId,
    RuntimeFilePath,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
)
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services.note_preparation import (
    PreparedEntityFields,
    PreparedEntityMove,
    PreparedEntityWrite,
    apply_prepared_entity_fields,
)
from basic_memory.services.record_identity import PermalinkTakenCheck, allocate_record_id
from basic_memory.vocabulary.checker import Violation
from basic_memory.vocabulary.model import Vocabulary, default_review_by

# The two types whose `review-by` a project's vocabulary fills in when the write
# leaves it out (GAPS W4's type table, GAPS W5 rule 4). Instructions rot faster
# than findings do, which is why a guide is on this list beside a finding.
REVIEW_BY_DEFAULT_TYPES: frozenset[str] = frozenset({"finding", "guide"})
REVIEW_BY_FIELD = "review-by"

ID_FIELD = "id"
PERMALINK_FIELD = "permalink"
SOURCE_FIELD = "source"

# What a write that arrived over the API or an MCP tool says its content came
# from. `bm new` stamps `cli` for a record a human authored at the prompt
# (`cli/record_notes.py:DEFAULT_SOURCE`); there is no narrower answer here,
# because no tool on this path carries a source of its own — `write_note` has no
# such parameter, and a caller that does know one states it in `metadata`.
API_SOURCE = "api"


class AcceptedNoteCreatePreparer(Protocol):
    """Capability that derives accepted markdown for a new note."""

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = ...,
        skip_conflict_check: bool = ...,
        session: AsyncSession | None = ...,
    ) -> PreparedEntityWrite: ...


class AcceptedNoteReplacePreparer(Protocol):
    """Capability that derives accepted markdown for a full note replacement."""

    async def prepare_update_entity_content(
        self,
        entity: Entity,
        schema: EntitySchema,
        existing_content: str,
        *,
        session: AsyncSession | None = ...,
    ) -> PreparedEntityWrite: ...


class AcceptedNoteEditPreparer(Protocol):
    """Capability that derives accepted markdown for a partial note edit."""

    async def prepare_edit_entity_content(
        self,
        entity: Entity,
        current_content: str,
        *,
        operation: str,
        content: str,
        section: str | None = ...,
        find_text: str | None = ...,
        expected_replacements: int = ...,
        replace_subsections: bool = ...,
        metadata: dict[str, Any] | None = ...,
        session: AsyncSession | None = ...,
    ) -> PreparedEntityWrite: ...


class AcceptedNoteSelfRelationResolver(Protocol):
    """Capability for resolving ambiguity-safe self-links during acceptance."""

    async def resolve_deferred_self_relation(
        self,
        target: str,
        entity: Entity,
        session: AsyncSession | None = ...,
    ) -> Entity | None: ...


class AcceptedNoteMovePreparer(Protocol):
    """Capability that derives accepted markdown for a note move."""

    async def prepare_move_entity_content(
        self,
        entity: Entity,
        current_content: str,
        destination_path: str,
        *,
        session: AsyncSession | None = ...,
    ) -> PreparedEntityMove: ...

    async def verify_move_destination_absent(
        self,
        *,
        source_file_path: str,
        destination_file_path: str,
    ) -> None: ...


class AcceptedNoteDeleteEntitySource(RuntimeDeletedNoteFileDeleteEntitySource, Protocol):
    """Entity identity required to delete one accepted note row."""


class AcceptedPendingEntityRepository(Protocol):
    """Repository capability for inserting one pending accepted entity."""

    async def create_pending_accepted_entity(
        self,
        session: AsyncSession,
        write: AcceptedPendingEntityWrite,
    ) -> Entity: ...


class AcceptedNoteContentRepository(Protocol):
    """Repository capability for accepting one note_content snapshot."""

    async def accept_write(
        self,
        session: AsyncSession,
        write: AcceptedNoteContentWrite,
    ) -> NoteContent: ...


class AcceptedNoteSearchRowRepository(Protocol):
    """Repository capability for replacing one accepted-note search row."""

    async def refresh_entity(
        self,
        session: AsyncSession,
        row: AcceptedNoteSearchRow,
    ) -> None: ...

    async def delete_entity(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
    ) -> None: ...

    async def delete_entity_vectors(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
    ) -> None: ...


class AcceptedNoteObservationRepository(Protocol):
    """Repository capability for replacing one accepted note's observations."""

    async def replace_accepted_observations(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
        observations: Sequence[AcceptedObservationWrite],
    ) -> None: ...


class AcceptedNoteRelationRepository(Protocol):
    """Repository capability for replacing one accepted note's outgoing relations."""

    async def replace_accepted_outgoing_relations(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
        relations: Sequence[AcceptedRelationWrite],
    ) -> None: ...


class AcceptedNoteViolationRepository(Protocol):
    """Repository capability for replacing one accepted note's violation rows.

    Narrow on purpose: the accepted path only ever states the whole answer for
    one record, and it always decides every rule, so it never needs the
    ``preserve_rules`` arm the move planner uses (GAPS T29).
    """

    async def replace_for_entity(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
        project_id: ProjectId,
        violations: Sequence[Violation],
    ) -> int: ...


class AcceptedNoteWriteRepositories(Protocol):
    """Repository capability set needed by accepted-note DB-first writes."""

    def violation_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteViolationRepository: ...

    def pending_entity_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedPendingEntityRepository: ...

    def note_content_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteContentRepository: ...

    def search_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteSearchRowRepository: ...

    def observation_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteObservationRepository: ...

    def relation_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteRelationRepository: ...


@dataclass(frozen=True, slots=True)
class AcceptedPreparedNoteWrite:
    """Prepared accepted markdown plus the checksum of that exact markdown."""

    prepared: PreparedEntityWrite
    db_checksum: RuntimeNoteContentChecksum


@dataclass(frozen=True, slots=True)
class AcceptedPreparedNoteMove:
    """Accepted markdown, path, permalink, and checksum for one DB-first move."""

    file_path: RuntimeFilePath
    markdown_content: str
    search_content: str
    permalink: str | None
    db_checksum: RuntimeNoteContentChecksum


@dataclass(frozen=True, slots=True)
class AcceptedPersistedNoteWrite:
    """Accepted note_content row plus any cleanup for a superseded materialized file."""

    note_content: NoteContent
    previous_file_delete: RuntimePendingNoteFileDelete | None = None


def accepted_note_create_type(data: EntitySchema) -> str:
    """The frontmatter type a create will actually store.

    Not simply ``data.note_type``: a content frontmatter block overrides it
    (``note_preparation._apply_schema_frontmatter_overrides``), and that is how
    every MCP write states its type. ``entity_metadata`` cannot set the type at
    all — ``schema_to_markdown`` drops the key — so it is not consulted.
    """
    content = data.content or ""
    if file_utils.has_frontmatter(content):
        declared = file_utils.parse_frontmatter(content).get("type")
        if isinstance(declared, str) and declared:
            return declared
    return data.note_type


def stamp_default_review_by(
    data: EntitySchema,
    vocabulary: Vocabulary | None,
    *,
    today: date | None = None,
) -> EntitySchema:
    """Fill in a create's ``review-by`` from the project's ``review_months``.

    A finding and a guide both require the field, and requiring a date a writer
    has no basis to choose would make the rule busywork — so an ungoverned
    project, another type, or a write that states its own date is left alone.

    Stamped on the schema *before* prepare, so the accepted markdown, its
    checksum, and the entity row all carry the same value the checker then
    judges. Stamping afterwards would validate one write and store another.
    """
    if vocabulary is None or accepted_note_create_type(data) not in REVIEW_BY_DEFAULT_TYPES:
        return data

    metadata = dict(data.entity_metadata or {})
    content = data.content or ""
    content_metadata: dict[str, Any] = {}
    if file_utils.has_frontmatter(content):
        content_metadata = file_utils.parse_frontmatter(content)
    # Both blocks are merged into the stored frontmatter, so a value in either
    # one is a value the writer stated.
    if metadata.get(REVIEW_BY_FIELD) or content_metadata.get(REVIEW_BY_FIELD):
        return data

    metadata[REVIEW_BY_FIELD] = default_review_by(vocabulary, today or datetime.now(tz=UTC).date())
    return data.model_copy(update={"entity_metadata": metadata})


async def stamp_record_identity(
    data: EntitySchema,
    vocabulary: Vocabulary | None,
    *,
    is_permalink_taken: PermalinkTakenCheck,
) -> EntitySchema:
    """Fill in the ``id``, ``permalink`` and ``source`` a governed record needs.

    `bm new` stamps all three itself (`cli/commands/new.py:build_frontmatter`).
    Every other write states a title and a body and nothing else, so once a
    project is governed by default (GAPS U49) the checker's common-field rule
    refuses every MCP and API write into one — over three fields that only the
    tool is in a position to supply. It supplies them here instead.

    Stamped into the note's *content* frontmatter rather than into
    ``entity_metadata``, for the reason `record_markdown` states: the permalink
    resolver honours a content-frontmatter permalink verbatim and derives every
    other one from the file path, so ``permalink == id`` is only true when the
    value arrives that way (`markdown/utils.py:schema_to_markdown` drops the key
    from metadata outright). Stamped *before* prepare, like the `review-by`
    default above, so the accepted markdown, its checksum, the entity row and the
    file all carry the value the checker then judges.

    A value the writer stated survives untouched, an id included: these fields are
    set-once (`.forked/schema.md` §4), and a write that names its own id is
    addressing a record rather than asking for one. Identity is one value under
    two keys (§2), so a stated permalink with no id becomes that id, and a stated
    id with no permalink becomes that permalink.

    An ungoverned project is left alone because no rule applies to it, and a type
    the vocabulary does not declare is left alone for GAPS E2's reason: that write
    is refused a moment later, and spending an id on it buys nothing.
    """
    note_type = accepted_note_create_type(data)
    if vocabulary is None or note_type not in vocabulary.types:
        return data

    content = data.content or ""
    has_frontmatter = file_utils.has_frontmatter(content)
    stated: dict[str, Any] = file_utils.parse_frontmatter(content) if has_frontmatter else {}
    metadata = data.entity_metadata or {}

    # `id` and `source` are ordinary keys and reach the file from either block,
    # so a value in either one counts as stated. `permalink` reaches it from the
    # content block alone, so one parked in `entity_metadata` is a value the
    # writer meant but did not land: it names the id, and is restamped where it
    # takes effect.
    stated_id = stated.get(ID_FIELD) or metadata.get(ID_FIELD)
    stated_permalink = stated.get(PERMALINK_FIELD) or metadata.get(PERMALINK_FIELD)
    needs_id = not stated_id
    needs_permalink = not stated.get(PERMALINK_FIELD)
    needs_source = not (stated.get(SOURCE_FIELD) or metadata.get(SOURCE_FIELD))
    if not (needs_id or needs_permalink or needs_source):
        return data

    record_id = stated_id or stated_permalink
    if not record_id:
        record_id = await allocate_record_id(note_type, is_permalink_taken)

    # Stamped keys first, then the writer's own — `bm new`'s order, and the one a
    # reader of the file expects identity to be in.
    stamped: dict[str, Any] = {}
    if needs_id:
        stamped[ID_FIELD] = record_id
    if needs_permalink:
        stamped[PERMALINK_FIELD] = record_id
    if needs_source:
        stamped[SOURCE_FIELD] = API_SOURCE

    body = file_utils.remove_frontmatter(content, strip=False) if has_frontmatter else content
    post = frontmatter.Post(body, **{**stamped, **stated})
    return data.model_copy(update={"content": file_utils.dump_frontmatter(post)})


async def prepare_accepted_note_create(
    preparer: AcceptedNoteCreatePreparer,
    data: EntitySchema,
    *,
    check_storage_exists: bool,
    is_permalink_taken: PermalinkTakenCheck,
    skip_conflict_check: bool = False,
    session: AsyncSession | None = None,
    vocabulary: Vocabulary | None = None,
) -> AcceptedPreparedNoteWrite:
    """Prepare one DB-first note create and checksum the accepted markdown."""
    governed = await stamp_record_identity(
        stamp_default_review_by(data, vocabulary),
        vocabulary,
        is_permalink_taken=is_permalink_taken,
    )
    prepared = await preparer.prepare_create_entity_content(
        governed,
        check_storage_exists=check_storage_exists,
        skip_conflict_check=skip_conflict_check,
        session=session,
    )
    return AcceptedPreparedNoteWrite(
        prepared=prepared,
        db_checksum=await file_utils.compute_checksum(prepared.markdown_content),
    )


async def prepare_accepted_note_replace(
    preparer: AcceptedNoteReplacePreparer,
    session: AsyncSession,
    *,
    entity: Entity,
    data: EntitySchema,
    current_note_content: NoteContent,
    user_profile_value: str | None,
) -> AcceptedPreparedNoteWrite:
    """Prepare a full accepted replacement and apply its entity fields."""
    prepared = await preparer.prepare_update_entity_content(
        entity,
        data,
        str(current_note_content.markdown_content),
        session=session,
    )
    result = AcceptedPreparedNoteWrite(
        prepared=prepared,
        db_checksum=await file_utils.compute_checksum(prepared.markdown_content),
    )
    apply_accepted_prepared_entity_fields(
        entity,
        prepared.entity_fields,
        user_profile_value=user_profile_value,
    )
    await session.flush()
    return result


async def prepare_accepted_note_edit(
    preparer: AcceptedNoteEditPreparer,
    session: AsyncSession,
    *,
    entity: Entity,
    current_note_content: NoteContent,
    operation: str,
    content: str,
    section: str | None,
    find_text: str | None,
    expected_replacements: int,
    replace_subsections: bool,
    user_profile_value: str | None,
    metadata: dict[str, Any] | None = None,
) -> AcceptedPreparedNoteWrite:
    """Prepare a partial accepted edit and apply its entity fields."""
    prepared = await preparer.prepare_edit_entity_content(
        entity,
        str(current_note_content.markdown_content),
        operation=operation,
        content=content,
        section=section,
        find_text=find_text,
        expected_replacements=expected_replacements,
        replace_subsections=replace_subsections,
        metadata=metadata,
        session=session,
    )
    result = AcceptedPreparedNoteWrite(
        prepared=prepared,
        db_checksum=await file_utils.compute_checksum(prepared.markdown_content),
    )
    apply_accepted_prepared_entity_fields(
        entity,
        prepared.entity_fields,
        user_profile_value=user_profile_value,
    )
    await session.flush()
    return result


async def prepare_accepted_note_move(
    preparer: AcceptedNoteMovePreparer | None,
    session: AsyncSession,
    *,
    entity: Entity,
    current_note_content: NoteContent,
    accepted_file_path: RuntimeFilePath,
    should_update_permalink: bool,
    user_profile_value: str | None,
) -> AcceptedPreparedNoteMove:
    """Prepare a DB-first move and apply the accepted path/permalink fields."""
    current_content = str(current_note_content.markdown_content)
    file_path = accepted_file_path
    permalink = entity.permalink
    markdown_content = current_content
    search_content = accepted_search_content_from_markdown(markdown_content)

    if should_update_permalink:
        if preparer is None:
            raise ValueError("Accepted note move requires a preparer to update the permalink")
        prepared = await preparer.prepare_move_entity_content(
            entity,
            current_content,
            accepted_file_path,
            session=session,
        )
        file_path = prepared.file_path.as_posix()
        permalink = prepared.permalink
        markdown_content = prepared.markdown_content
        search_content = prepared.search_content

    result = AcceptedPreparedNoteMove(
        file_path=file_path,
        markdown_content=markdown_content,
        search_content=search_content,
        permalink=permalink,
        db_checksum=await file_utils.compute_checksum(markdown_content),
    )
    previous_permalink = entity.permalink
    entity.file_path = result.file_path
    entity.permalink = result.permalink
    # Trigger: the move changed the permalink, so the prepared content rewrote
    #     the `permalink:` line in the file's frontmatter.
    # Why: entity_metadata mirrors that frontmatter, and a stale mirror makes the
    #     id-integrity check report a routine move as drift (GAPS T28), burying
    #     the hand-edit the check exists to find. Keyed on the permalink actually
    #     changing rather than on the caller's intent, because the preparer
    #     declines the rewrite when permalinks are disabled.
    # Outcome: the mirror agrees with the bytes this move writes. Rebound rather
    #     than mutated in place: a plain JSON column does not track item
    #     assignment. A row with no mirror does not acquire one here, matching
    #     the batch path, where json_set on NULL stays NULL.
    if result.permalink != previous_permalink and entity.entity_metadata is not None:
        entity.entity_metadata = {**entity.entity_metadata, "permalink": result.permalink}
    entity.last_updated_by = user_profile_value
    await session.flush()
    return result


def apply_accepted_prepared_entity_fields(
    entity: Entity,
    entity_fields: PreparedEntityFields,
    *,
    user_profile_value: str | None,
) -> None:
    """Copy prepared accepted markdown fields onto an entity row."""
    apply_prepared_entity_fields(
        entity,
        entity_fields,
        user_profile_value=user_profile_value,
    )


def accepted_pending_entity_write_from_prepared(
    prepared: PreparedEntityWrite,
    *,
    user_profile_value: str | None,
    external_id: str | None = None,
) -> AcceptedPendingEntityWrite:
    """Map prepared Basic Memory entity fields to the pending entity DB write."""
    fields = prepared.entity_fields
    return AcceptedPendingEntityWrite(
        title=fields.title,
        note_type=fields.note_type,
        entity_metadata=fields.entity_metadata,
        content_type=fields.content_type,
        permalink=fields.permalink,
        file_path=fields.file_path,
        created_at=fields.created_at,
        updated_at=fields.updated_at,
        created_by=user_profile_value,
        last_updated_by=user_profile_value,
        external_id=external_id,
    )


async def create_accepted_pending_entity(
    session: AsyncSession,
    *,
    prepared: PreparedEntityWrite,
    project_id: ProjectId,
    user_profile_value: str | None,
    external_id: str | None = None,
    repositories: AcceptedNoteWriteRepositories,
) -> Entity:
    """Insert a prepared accepted entity row without materializing a file."""
    repository = repositories.pending_entity_repository(project_id)
    return await repository.create_pending_accepted_entity(
        session,
        accepted_pending_entity_write_from_prepared(
            prepared,
            user_profile_value=user_profile_value,
            external_id=external_id,
        ),
    )


def accepted_note_content_write_from_markdown(
    *,
    entity_id: RuntimeEntityId,
    markdown_content: str,
    db_version: RuntimeNoteContentVersion,
    db_checksum: RuntimeNoteContentChecksum,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
) -> AcceptedNoteContentWrite:
    """Build the repository write for one accepted note_content snapshot."""
    return AcceptedNoteContentWrite(
        entity_id=entity_id,
        markdown_content=markdown_content,
        db_version=db_version,
        db_checksum=db_checksum,
        last_source=last_source,
        updated_at=updated_at,
    )


async def accept_note_content_write(
    session: AsyncSession,
    *,
    entity: Entity,
    markdown_content: str,
    db_version: RuntimeNoteContentVersion,
    db_checksum: RuntimeNoteContentChecksum,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    repositories: AcceptedNoteWriteRepositories,
) -> NoteContent:
    """Accept markdown into note_content before object storage catches up."""
    repository = repositories.note_content_repository(entity.project_id)
    return await repository.accept_write(
        session,
        accepted_note_content_write_from_markdown(
            entity_id=entity.id,
            markdown_content=markdown_content,
            db_version=db_version,
            db_checksum=db_checksum,
            last_source=last_source,
            updated_at=updated_at,
        ),
    )


def accepted_note_search_row_from_entity(
    entity: Entity,
    *,
    search_content: str,
) -> AcceptedNoteSearchRow:
    """Build the hot search row for one accepted note snapshot."""
    return build_accepted_note_search_row(
        entity_id=entity.id,
        title=entity.title,
        note_type=entity.note_type,
        entity_metadata=entity.entity_metadata,
        permalink=entity.permalink,
        file_path=entity.file_path,
        search_content=search_content,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        project_id=entity.project_id,
    )


async def refresh_accepted_note_search_index(
    session: AsyncSession,
    *,
    entity: Entity,
    search_content: str,
    repositories: AcceptedNoteWriteRepositories,
) -> None:
    """Refresh the hot accepted-note search row inside the caller's transaction."""
    repository = repositories.search_repository(entity.project_id)
    await repository.refresh_entity(
        session,
        accepted_note_search_row_from_entity(entity, search_content=search_content),
    )


async def delete_accepted_note_search_index(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    repositories: AcceptedNoteWriteRepositories,
) -> None:
    """Remove all search rows for an accepted-note entity inside the caller's transaction."""
    repository = repositories.search_repository(project_id)
    await repository.delete_entity(session, entity_id)


async def delete_accepted_note_vectors(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    repositories: AcceptedNoteWriteRepositories,
) -> None:
    """Remove semantic vectors for an accepted-note entity inside the caller's transaction."""
    repository = repositories.search_repository(project_id)
    await repository.delete_entity_vectors(session, entity_id)


async def _persist_accepted_note_content_and_search(
    session: AsyncSession,
    *,
    entity: Entity,
    markdown_content: str,
    search_content: str,
    db_checksum: RuntimeNoteContentChecksum,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    current_note_content: RuntimeAcceptedNoteContentWriteSource | None = None,
    existing_file_path: RuntimeFilePath | None = None,
    accepted_file_path: RuntimeFilePath | None = None,
    repositories: AcceptedNoteWriteRepositories,
) -> AcceptedPersistedNoteWrite:
    """Internal content/search phase shared by complete snapshots and moves."""
    content_write = plan_accepted_note_content_write(
        project_id=entity.project_id,
        entity_id=entity.id,
        existing_file_path=existing_file_path,
        accepted_file_path=accepted_file_path or entity.file_path,
        current_note_content=current_note_content,
    )
    note_content = await accept_note_content_write(
        session,
        entity=entity,
        markdown_content=markdown_content,
        db_version=content_write.db_version,
        db_checksum=db_checksum,
        last_source=last_source,
        updated_at=updated_at,
        repositories=repositories,
    )
    await refresh_accepted_note_search_index(
        session,
        entity=entity,
        search_content=search_content,
        repositories=repositories,
    )
    return AcceptedPersistedNoteWrite(
        note_content=note_content,
        previous_file_delete=content_write.previous_file_delete,
    )


async def _replace_accepted_note_graph(
    session: AsyncSession,
    *,
    entity: Entity,
    prepared: PreparedEntityWrite,
    self_relation_resolver: AcceptedNoteSelfRelationResolver,
    repositories: AcceptedNoteWriteRepositories,
) -> None:
    """Persist the accepted note's observations and relations in one transaction.

    The accepted markdown was already parsed during prepare, so the graph rows
    are committed alongside note_content and search instead of waiting for a
    later ``index_file`` pass to reparse the materialized file. Without this the
    observation/relation tables stay empty after a successful DB-first write, so
    schema inference and relation traversal are nondeterministic until an
    unrelated storage notification happens to fire (issue #1076).
    """
    observation_repository = repositories.observation_repository(entity.project_id)
    await observation_repository.replace_accepted_observations(
        session,
        entity.id,
        prepared.observations,
    )

    # General deferred resolution skips target_id == from_id to avoid binding an
    # ambiguous title to the wrong note. Reuse the indexing path's narrow,
    # ambiguity-safe self resolver here so filepath/permalink self-links do not
    # remain unresolved forever after a DB-first write.
    relations: list[AcceptedRelationWrite] = []
    for relation in prepared.relations:
        if relation.target_id is not None:
            relations.append(relation)
            continue
        target_entity = await self_relation_resolver.resolve_deferred_self_relation(
            relation.target_name,
            entity,
            session=session,
        )
        if target_entity is None:
            relations.append(relation)
            continue
        relations.append(
            AcceptedRelationWrite(
                relation_type=relation.relation_type,
                target_name=target_entity.title,
                context=relation.context,
                target_id=target_entity.id,
            )
        )

    relation_repository = repositories.relation_repository(entity.project_id)
    await relation_repository.replace_accepted_outgoing_relations(
        session,
        entity.id,
        relations,
    )


async def persist_accepted_note_snapshot(
    session: AsyncSession,
    *,
    entity: Entity,
    prepared: PreparedEntityWrite,
    db_checksum: RuntimeNoteContentChecksum,
    self_relation_resolver: AcceptedNoteSelfRelationResolver,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    current_note_content: RuntimeAcceptedNoteContentWriteSource | None = None,
    existing_file_path: RuntimeFilePath | None = None,
    accepted_file_path: RuntimeFilePath | None = None,
    repositories: AcceptedNoteWriteRepositories,
) -> AcceptedPersistedNoteWrite:
    """Persist one complete accepted Markdown snapshot in the caller's transaction."""
    persisted = await _persist_accepted_note_content_and_search(
        session,
        entity=entity,
        markdown_content=prepared.markdown_content,
        search_content=prepared.search_content,
        db_checksum=db_checksum,
        last_source=last_source,
        updated_at=updated_at,
        current_note_content=current_note_content,
        existing_file_path=existing_file_path,
        accepted_file_path=accepted_file_path,
        repositories=repositories,
    )
    await _replace_accepted_note_graph(
        session,
        entity=entity,
        prepared=prepared,
        self_relation_resolver=self_relation_resolver,
        repositories=repositories,
    )
    return persisted


async def persist_accepted_note_move(
    session: AsyncSession,
    *,
    entity: Entity,
    prepared: AcceptedPreparedNoteMove,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    current_note_content: RuntimeAcceptedNoteContentWriteSource,
    existing_file_path: RuntimeFilePath,
    repositories: AcceptedNoteWriteRepositories,
) -> AcceptedPersistedNoteWrite:
    """Persist the explicitly narrower content/search state for an accepted move."""
    return await _persist_accepted_note_content_and_search(
        session,
        entity=entity,
        markdown_content=prepared.markdown_content,
        search_content=prepared.search_content,
        db_checksum=prepared.db_checksum,
        last_source=last_source,
        updated_at=updated_at,
        current_note_content=current_note_content,
        existing_file_path=existing_file_path,
        accepted_file_path=prepared.file_path,
        repositories=repositories,
    )


async def delete_accepted_note_entity(
    session: AsyncSession,
    *,
    entity: AcceptedNoteDeleteEntitySource,
) -> None:
    """Delete the accepted entity row inside the caller-owned transaction."""
    await session.delete(entity)


async def delete_accepted_note(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity: AcceptedNoteDeleteEntitySource | None,
    note_content: RuntimeDeletedNoteFileChecksumSource | None = None,
    repositories: AcceptedNoteWriteRepositories,
) -> RuntimeAcceptedNoteChange[dict[str, object]]:
    """Plan an accepted note delete and remove the entity when it exists."""
    accepted = plan_accepted_note_delete_change(
        project_id=project_id,
        entity=entity,
        note_content=note_content,
    )
    if entity is not None:
        await delete_accepted_note_search_index(
            session,
            project_id=project_id,
            entity_id=entity.id,
            repositories=repositories,
        )
        await delete_accepted_note_vectors(
            session,
            project_id=project_id,
            entity_id=entity.id,
            repositories=repositories,
        )
        await session.delete(entity)
    return accepted
