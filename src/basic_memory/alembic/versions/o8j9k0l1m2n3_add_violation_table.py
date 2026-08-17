"""Add violation table and project.vocabulary_stamp

Revision ID: o8j9k0l1m2n3
Revises: n7i8j9k0l1m2
Create Date: 2026-08-16 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "o8j9k0l1m2n3"
down_revision: Union[str, None] = "n7i8j9k0l1m2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create violation, and stamp projects with the vocabulary they were checked against.

    One migration for both because they are one mechanism (GAPS W5): the rows say
    what is wrong, and the stamp says which vocabulary said so.
    """
    op.create_table(
        "violation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("rule", sa.String(), nullable=False),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('error', 'advisory')", name="ck_violation_severity"),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_id", "rule", "field", name="uix_violation_entity_rule_field"),
    )
    op.create_index(
        "ix_violation_project_severity", "violation", ["project_id", "severity"], unique=False
    )
    op.create_index("ix_violation_entity_id", "violation", ["entity_id"], unique=False)

    # NULL means "never validated", which is what every existing project is.
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vocabulary_stamp", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop violation, its indexes, and the vocabulary stamp.

    The table goes first: dropping the project column rebuilds ``project`` under
    SQLite's batch mode, and nothing should reference it while that happens.
    """
    op.drop_index("ix_violation_entity_id", table_name="violation")
    op.drop_index("ix_violation_project_severity", table_name="violation")
    op.drop_table("violation")

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("vocabulary_stamp")
