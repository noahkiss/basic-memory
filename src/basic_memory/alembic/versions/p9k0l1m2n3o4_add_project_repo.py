"""Add project.repo — the working repo's origin URL (GAPS U36)

Revision ID: p9k0l1m2n3o4
Revises: o8j9k0l1m2n3
Create Date: 2026-08-20 13:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "p9k0l1m2n3o4"
down_revision: Union[str, None] = "o8j9k0l1m2n3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Stamp projects with the working repo they were marked from.

    NULL means "never captured", which is what every existing project is:
    `.bm.yml` markers are gitignored, so the registry is the only place the
    repo ↔ project association can survive a fresh clone, and `bm project
    mark` fills it in from `remote.origin.url` the next time each project is
    marked.
    """
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("repo", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the repo column."""
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("repo")
