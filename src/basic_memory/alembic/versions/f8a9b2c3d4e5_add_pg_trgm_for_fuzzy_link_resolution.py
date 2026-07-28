"""Add project_id to relation/observation

Revision ID: f8a9b2c3d4e5
Revises: 314f1ea54dc4
Create Date: 2025-12-01 12:00:00.000000

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
revision: str = "f8a9b2c3d4e5"
down_revision: Union[str, None] = "314f1ea54dc4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add project_id to relation and observation tables.

    This migration:
    1. Adds project_id column to relation and observation tables (denormalization)
    2. Backfills project_id from the associated entity
    3. Indexes project_id on both tables
    """
    connection = op.get_bind()

    # -------------------------------------------------------------------------
    # Add project_id to relation table
    # -------------------------------------------------------------------------

    # Step 1: Add project_id column as nullable first (idempotent)
    if not column_exists(connection, "relation", "project_id"):
        op.add_column("relation", sa.Column("project_id", sa.Integer(), nullable=True))

        # Step 2: Backfill project_id from entity.project_id via from_id
        op.execute("""
            UPDATE relation
            SET project_id = (
                SELECT entity.project_id
                FROM entity
                WHERE entity.id = relation.from_id
            )
        """)

        # Step 3: Make project_id NOT NULL and add foreign key.
        # SQLite requires batch operations for ALTER COLUMN.
        with op.batch_alter_table("relation") as batch_op:
            batch_op.alter_column("project_id", nullable=False)
            batch_op.create_foreign_key(
                "fk_relation_project_id",
                "project",
                ["project_id"],
                ["id"],
            )

    # Step 4: Create index on relation.project_id (idempotent)
    if not index_exists(connection, "ix_relation_project_id"):
        op.create_index("ix_relation_project_id", "relation", ["project_id"])

    # -------------------------------------------------------------------------
    # Add project_id to observation table
    # -------------------------------------------------------------------------

    # Step 1: Add project_id column as nullable first (idempotent)
    if not column_exists(connection, "observation", "project_id"):
        op.add_column("observation", sa.Column("project_id", sa.Integer(), nullable=True))

        # Step 2: Backfill project_id from entity.project_id via entity_id
        op.execute("""
            UPDATE observation
            SET project_id = (
                SELECT entity.project_id
                FROM entity
                WHERE entity.id = observation.entity_id
            )
        """)

        # Step 3: Make project_id NOT NULL and add foreign key.
        # SQLite requires batch operations for ALTER COLUMN.
        with op.batch_alter_table("observation") as batch_op:
            batch_op.alter_column("project_id", nullable=False)
            batch_op.create_foreign_key(
                "fk_observation_project_id",
                "project",
                ["project_id"],
                ["id"],
            )

    # Step 4: Create index on observation.project_id (idempotent)
    if not index_exists(connection, "ix_observation_project_id"):
        op.create_index("ix_observation_project_id", "observation", ["project_id"])


def downgrade() -> None:
    """Remove project_id from relation/observation."""
    # SQLite requires batch operations
    op.drop_index("ix_observation_project_id", table_name="observation")
    with op.batch_alter_table("observation") as batch_op:
        batch_op.drop_constraint("fk_observation_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")

    op.drop_index("ix_relation_project_id", table_name="relation")
    with op.batch_alter_table("relation") as batch_op:
        batch_op.drop_constraint("fk_relation_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")
