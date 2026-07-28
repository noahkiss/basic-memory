"""Retired: added Postgres full-text search support with tsvector and GIN indexes

Revision ID: 314f1ea54dc4
Revises: e7e1f4367280
Create Date: 2025-11-15 18:05:01.025405

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "314f1ea54dc4"
down_revision: Union[str, None] = "e7e1f4367280"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: this revision was Postgres-only and is retained to preserve the chain."""
    pass


def downgrade() -> None:
    """No-op: this revision was Postgres-only and is retained to preserve the chain."""
    pass
