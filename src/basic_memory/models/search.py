"""Search DDL statements.

The search_index table is created via raw DDL, not ORM models, because it is an
FTS5 virtual table, which cannot be represented as an ORM model. All search
operations go through raw SQL via the SearchIndexRow dataclass.
"""

from sqlalchemy import DDL


CREATE_SEARCH_INDEX = DDL("""
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    -- Core entity fields
    id UNINDEXED,          -- Row ID
    title,                 -- Title for searching
    content_stems,         -- Main searchable content split into stems
    content_snippet,       -- File content snippet for display
    permalink,             -- Stable identifier (now indexed for path search)
    file_path UNINDEXED,   -- Physical location
    type UNINDEXED,        -- entity/relation/observation

    -- Project context
    project_id UNINDEXED,  -- Project identifier

    -- Relation fields
    from_id UNINDEXED,     -- Source entity
    to_id UNINDEXED,       -- Target entity
    relation_type UNINDEXED, -- Type of relation

    -- Observation fields
    entity_id UNINDEXED,   -- Parent entity
    category UNINDEXED,    -- Observation category

    -- Common fields
    metadata UNINDEXED,    -- JSON metadata
    created_at UNINDEXED,  -- Creation timestamp
    updated_at UNINDEXED,  -- Last update

    -- Configuration
    tokenize='unicode61 tokenchars 0x2F',  -- Hex code for /
    prefix='1,2,3,4'                    -- Support longer prefixes for paths
);
""")

# Semantic chunk metadata table.
# Embedding vectors live in a sqlite-vec virtual table keyed by this table's rowid.
CREATE_SQLITE_SEARCH_VECTOR_CHUNKS = DDL("""
CREATE TABLE IF NOT EXISTS search_vector_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    chunk_key TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    entity_fingerprint TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
""")

CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_vector_chunks_project_entity
ON search_vector_chunks (project_id, entity_id)
""")

CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE = DDL("""
CREATE UNIQUE INDEX IF NOT EXISTS uix_search_vector_chunks_entity_key
ON search_vector_chunks (project_id, entity_id, chunk_key)
""")


def create_sqlite_search_vector_embeddings(dimensions: int) -> DDL:
    """Build sqlite-vec virtual table DDL for the configured embedding dimension."""
    return DDL(
        f"""
CREATE VIRTUAL TABLE IF NOT EXISTS search_vector_embeddings
USING vec0(embedding float[{dimensions}])
"""
    )
