"""Repository-owned cleanup for accepted-note vector search rows."""

from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository.project_repository import _load_sqlite_vec_on_session
from basic_memory.runtime.storage import ProjectId


DELETE_PROJECT_INDEX_VECTOR_CHUNKS_SQL = text("""
    DELETE FROM search_vector_chunks
    WHERE project_id = :project_id
      AND entity_id IN :deleted_entity_ids
""").bindparams(bindparam("deleted_entity_ids", expanding=True))

SELECT_PROJECT_INDEX_VECTOR_TABLES_SQL = text("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
      AND name IN ('search_vector_chunks', 'search_vector_embeddings')
""")

# Embeddings live in a vec0 virtual table addressed by its rowid pseudocolumn,
# which is why this joins on rowid rather than a real FK column.
DELETE_PROJECT_INDEX_VECTOR_EMBEDDINGS_SQL = text("""
    DELETE FROM search_vector_embeddings
    WHERE rowid IN (
        SELECT id
        FROM search_vector_chunks
        WHERE project_id = :project_id
          AND entity_id IN :deleted_entity_ids
    )
""").bindparams(bindparam("deleted_entity_ids", expanding=True))


async def project_index_vector_table_names(session: AsyncSession) -> frozenset[str]:
    """Return the vector tables that currently exist; they are created lazily."""
    result = await session.execute(SELECT_PROJECT_INDEX_VECTOR_TABLES_SQL)
    return frozenset(str(table_name) for table_name in result.scalars())


async def delete_project_index_vector_rows(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_ids: Sequence[int],
) -> None:
    """Delete vector rows for project-index entity deletes when the tables exist."""
    deleted_entity_ids = tuple(entity_ids)
    if not deleted_entity_ids:
        return

    vector_table_names = await project_index_vector_table_names(session)
    if "search_vector_chunks" not in vector_table_names:
        return

    delete_params = {
        "project_id": project_id,
        "deleted_entity_ids": deleted_entity_ids,
    }
    # Extension loading is per-connection, so vec0 must be loaded on *this* session
    # before the DELETE or the embeddings are silently left behind.
    if "search_vector_embeddings" in vector_table_names:
        if await _load_sqlite_vec_on_session(session):
            await session.execute(
                DELETE_PROJECT_INDEX_VECTOR_EMBEDDINGS_SQL,
                delete_params,
            )

    await session.execute(DELETE_PROJECT_INDEX_VECTOR_CHUNKS_SQL, delete_params)
