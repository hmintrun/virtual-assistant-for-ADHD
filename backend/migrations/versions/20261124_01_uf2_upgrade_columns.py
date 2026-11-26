"""Add UF2 Layer 3/4 columns for undo snapshot, horizon extension and session enrichment.

Revision ID: 20261124_01
Revises: 20261118_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20261124_01"
down_revision = "20261118_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_candidates", sa.Column("previous_snapshot", sa.JSON(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("extension_candidate_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("sessions", sa.Column("title", sa.String(160), nullable=True))
    op.add_column("sessions", sa.Column("session_goal", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("expected_output", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions", recreate="never") as batch_op:
        batch_op.drop_column("expected_output")
        batch_op.drop_column("session_goal")
        batch_op.drop_column("title")
    with op.batch_alter_table("tasks", recreate="never") as batch_op:
        batch_op.drop_column("extension_candidate_at")
    with op.batch_alter_table("plan_candidates", recreate="never") as batch_op:
        batch_op.drop_column("previous_snapshot")
