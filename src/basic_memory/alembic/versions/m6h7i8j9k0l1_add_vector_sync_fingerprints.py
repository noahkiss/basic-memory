"""Retired: persisted vector sync fingerprints on chunk metadata.

Revision ID: m6h7i8j9k0l1
Revises: l5g6h7i8j9k0
Create Date: 2026-04-07 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "m6h7i8j9k0l1"
down_revision: Union[str, None] = "l5g6h7i8j9k0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: this revision was Postgres-only and is retained to preserve the chain.

    SQLite creates search_vector_chunks at runtime with the fingerprint columns
    already present (see CREATE_SQLITE_SEARCH_VECTOR_CHUNKS).
    """
    pass


def downgrade() -> None:
    """No-op: this revision was Postgres-only and is retained to preserve the chain."""
    pass
