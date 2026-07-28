"""Add structured metadata indexes for entity frontmatter

Revision ID: d7e8f9a0b1c2
Revises: g9a0b3c4d5e6
Create Date: 2026-01-31 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


def column_exists(connection, table: str, column: str) -> bool:
    """Check if a column exists in a table (idempotent migration support)."""
    result = connection.execute(text(f"PRAGMA table_info({table})"))
    columns = [row[1] for row in result]
    return column in columns


def index_exists(connection, index_name: str) -> bool:
    """Check if an index exists (idempotent migration support)."""
    result = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
        {"index_name": index_name},
    )
    return result.fetchone() is not None


# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "6830751f5fb6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add generated columns and indexes for common frontmatter fields."""
    connection = op.get_bind()

    # Constraint: SQLite ALTER TABLE ADD COLUMN only supports VIRTUAL generated columns,
    # not STORED. json_extract is deterministic so VIRTUAL columns can still be indexed.
    if not column_exists(connection, "entity", "tags_json"):
        op.add_column(
            "entity",
            sa.Column(
                "tags_json",
                sa.Text(),
                sa.Computed("json_extract(entity_metadata, '$.tags')", persisted=False),
            ),
        )
    if not column_exists(connection, "entity", "frontmatter_status"):
        op.add_column(
            "entity",
            sa.Column(
                "frontmatter_status",
                sa.Text(),
                sa.Computed("json_extract(entity_metadata, '$.status')", persisted=False),
            ),
        )
    if not column_exists(connection, "entity", "frontmatter_type"):
        op.add_column(
            "entity",
            sa.Column(
                "frontmatter_type",
                sa.Text(),
                sa.Computed("json_extract(entity_metadata, '$.type')", persisted=False),
            ),
        )

    # Index generated columns
    if not index_exists(connection, "idx_entity_tags_json"):
        op.create_index("idx_entity_tags_json", "entity", ["tags_json"])
    if not index_exists(connection, "idx_entity_frontmatter_status"):
        op.create_index("idx_entity_frontmatter_status", "entity", ["frontmatter_status"])
    if not index_exists(connection, "idx_entity_frontmatter_type"):
        op.create_index("idx_entity_frontmatter_type", "entity", ["frontmatter_type"])


def downgrade() -> None:
    """Best-effort downgrade: drop indexes (dropping generated columns needs a rebuild)."""
    op.execute("DROP INDEX IF EXISTS idx_entity_frontmatter_status")
    op.execute("DROP INDEX IF EXISTS idx_entity_frontmatter_type")
    op.execute("DROP INDEX IF EXISTS idx_entity_tags_json")
