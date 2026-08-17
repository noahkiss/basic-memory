"""Path-only moves for entities the note-content path cannot carry (GAPS T26).

A directory holds whatever a human put in it: markdown notes, and also PDFs,
images, and other indexed binaries. The accepted-note path is markdown-only and
refuses anything else with a 415, which is right for ``move_note`` — there is no
note content to accept — and wrong for a directory move, where refusing means
leaving the file behind while everything around it moves.

So a non-markdown entity takes this arm instead: move the file, restamp the
entity row's path and checksum, refresh its one search row. **No vocabulary
check**, deliberately — the funnel judges frontmatter, and a binary has none.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_search import build_accepted_note_search_row
from basic_memory.indexing.accepted_note_write_runner import AcceptedNoteWriteRepositories
from basic_memory.models import Entity
from basic_memory.runtime.storage import RuntimeFilePath


class NonNoteMoveFileService(Protocol):
    """Filesystem capability needed to relocate one non-note file."""

    async def exists(self, path: RuntimeFilePath | Path) -> bool: ...

    async def ensure_directory(self, path: RuntimeFilePath | Path) -> None: ...

    async def move_file(
        self,
        source: RuntimeFilePath | Path,
        destination: RuntimeFilePath | Path,
    ) -> None: ...

    async def compute_checksum(self, path: RuntimeFilePath | Path) -> str: ...


class NonNoteMoveEntityRepository(Protocol):
    """Entity row capability needed to restamp one moved non-note entity."""

    async def update(
        self,
        session: AsyncSession,
        entity_id: int,
        entity_data: dict[str, object],
    ) -> Entity | None: ...


class NonNoteMoveRejected(Exception):
    """One non-note file could not be moved. Carries the reason for the caller."""


async def move_non_note_entity(
    session: AsyncSession,
    *,
    entity: Entity,
    destination_path: RuntimeFilePath,
    file_service: NonNoteMoveFileService,
    entity_repository: NonNoteMoveEntityRepository,
    write_repositories: AcceptedNoteWriteRepositories,
) -> Entity:
    """Move one non-note entity's file and bring its DB rows to the new path.

    The permalink is untouched. A binary carries no frontmatter, so there is
    nothing the permalink could be derived from beyond the path, and rewriting it
    would orphan every relation bound to it for no gain.
    """
    source_path = entity.file_path
    if not await file_service.exists(source_path):
        raise NonNoteMoveRejected(f"Source file not found: {source_path}")
    if await file_service.exists(destination_path):
        raise NonNoteMoveRejected(f"Destination already exists: {destination_path}")

    await file_service.ensure_directory(Path(destination_path).parent)
    await file_service.move_file(source_path, destination_path)

    updated = await entity_repository.update(
        session,
        entity.id,
        {
            "file_path": destination_path,
            "checksum": await file_service.compute_checksum(destination_path),
            "updated_at": datetime.now(tz=UTC),
        },
    )
    if updated is None:  # pragma: no cover
        raise NonNoteMoveRejected(f"Entity row vanished mid-move: {source_path}")

    # One row, no content: the same shape `SearchService.index_entity_file`
    # writes for a non-markdown entity. Without it the index keeps pointing at
    # the old path.
    search_repository = write_repositories.search_repository(updated.project_id)
    await search_repository.refresh_entity(
        session,
        build_accepted_note_search_row(
            entity_id=updated.id,
            title=updated.title,
            note_type=updated.note_type,
            entity_metadata=updated.entity_metadata,
            permalink=updated.permalink,
            file_path=updated.file_path,
            search_content="",
            created_at=updated.created_at,
            updated_at=updated.updated_at,
            project_id=updated.project_id,
        ),
    )
    return updated
