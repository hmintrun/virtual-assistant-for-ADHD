"""Align persisted planning data with schema.md.

Revision ID: 0ece3b500cfe
Revises: 20261030_01
"""

import sqlalchemy as sa
from alembic import op

revision = "0ece3b500cfe"
down_revision = "20261030_01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(op.f("ck_sessions_duration_sum"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_sessions_duration_sum"), "duration_seconds = work_seconds + rest_seconds"
        )

    with op.batch_alter_table("check_ins") as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_check_ins_response_state_valid"),
            "response_state IN ('open', 'answered', 'no_respond')",
        )

    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("command_id", sa.String(length=128), nullable=True))
        batch_op.alter_column(
            "resource_ref",
            existing_type=sa.VARCHAR(length=255),
            type_=sa.JSON(),
            existing_nullable=False,
            postgresql_using="to_json(resource_ref)",
        )

    with op.batch_alter_table("session_runs", schema=None) as batch_op:
        batch_op.drop_column("micro_elapsed_seconds")

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column(
            "importance",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.Integer(),
            existing_nullable=True,
            postgresql_using="NULLIF(importance, '')::integer",
        )

    with op.batch_alter_table("thoughts", schema=None) as batch_op:
        batch_op.alter_column("source_run_id", existing_type=sa.CHAR(length=32), nullable=True)


def downgrade():
    with op.batch_alter_table("check_ins") as batch_op:
        batch_op.drop_constraint(op.f("ck_check_ins_response_state_valid"), type_="check")

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(op.f("ck_sessions_duration_sum"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_sessions_duration_sum"),
            "duration_seconds = work_seconds + rest_seconds + COALESCE(micro_task_seconds, 0)",
        )

    with op.batch_alter_table("thoughts", schema=None) as batch_op:
        batch_op.alter_column("source_run_id", existing_type=sa.CHAR(length=32), nullable=False)

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column(
            "importance",
            existing_type=sa.Integer(),
            type_=sa.VARCHAR(length=32),
            existing_nullable=True,
        )

    with op.batch_alter_table("session_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("micro_elapsed_seconds", sa.INTEGER(), nullable=True))

    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.alter_column(
            "resource_ref",
            existing_type=sa.JSON(),
            type_=sa.VARCHAR(length=255),
            existing_nullable=False,
        )
        batch_op.drop_column("command_id")
