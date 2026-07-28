"""Retired: added cascade delete FK from search_index to entity

Revision ID: a2b3c4d5e6f7
Revises: f8a9b2c3d4e5
Create Date: 2025-12-02 07:00:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f8a9b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: search_index is an FTS5 virtual table and cannot carry a FK.

    Retained to preserve the revision chain.
    """
    pass


def downgrade() -> None:
    """No-op: retained to preserve the revision chain."""
    pass
