"""Retired: added Postgres semantic vector search tables (pgvector-aware, optional)

Revision ID: h1b2c3d4e5f6
Revises: d7e8f9a0b1c2
Create Date: 2026-02-07 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "h1b2c3d4e5f6"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: this revision was Postgres-only and is retained to preserve the chain."""
    pass


def downgrade() -> None:
    """No-op: this revision was Postgres-only and is retained to preserve the chain."""
    pass
