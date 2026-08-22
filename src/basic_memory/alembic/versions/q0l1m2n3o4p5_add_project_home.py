"""Add project.home — the declared home for a skill-homed project

Revision ID: q0l1m2n3o4p5
Revises: p9k0l1m2n3o4
Create Date: 2026-08-22 04:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "q0l1m2n3o4p5"
down_revision: Union[str, None] = "p9k0l1m2n3o4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Record which projects home their notes outside the store.

    NULL means "declared nothing", which is what every existing project is: a
    store-homed project and a legacy off-store one are both NULL, and only an
    explicit "external" says the notes live in a directory something else
    already versions. No server default, because NULL is the value, not a
    placeholder for one.
    """
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("home", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the home column."""
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("home")
