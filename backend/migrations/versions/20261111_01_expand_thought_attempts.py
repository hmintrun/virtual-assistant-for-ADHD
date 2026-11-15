"""Expand thoughts.attempt_count check constraint to allow 1..5 for text fallback.

Revision ID: 20261111_01
Revises: 20261108_01
"""

from alembic import op

revision = "20261111_01"
down_revision = "20261108_01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("thoughts") as batch_op:
        batch_op.drop_constraint(op.f("ck_thoughts_attempt_limit"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_thoughts_attempt_limit"),
            "attempt_count BETWEEN 1 AND 5",
        )


def downgrade():
    with op.batch_alter_table("thoughts") as batch_op:
        batch_op.drop_constraint(op.f("ck_thoughts_attempt_limit"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_thoughts_attempt_limit"),
            "attempt_count BETWEEN 1 AND 3",
        )
