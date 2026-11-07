"""Persist device reports independently of Run outcomes and DND assumptions."""

import sqlalchemy as sa
from alembic import op

revision = "20261105_01"
down_revision = "20261101_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "device_states",
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("device_report_revision", sa.Integer(), nullable=False),
        sa.Column("notification_permission", sa.String(32), nullable=False),
        sa.Column("microphone_permission", sa.String(32), nullable=False),
        sa.Column("dnd_permission", sa.String(32), nullable=False),
        sa.Column("alarm_capability", sa.String(32), nullable=False),
        sa.Column("active_run_id", sa.Uuid(), nullable=True),
        sa.Column("last_schedule_revision", sa.Integer(), nullable=True),
        sa.Column("dnd_owner_run_id", sa.Uuid(), nullable=True),
        sa.Column("dnd_apply_state", sa.String(32), nullable=False),
        sa.Column("dnd_restore_state", sa.String(32), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("app_visibility", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_states")),
        sa.UniqueConstraint("user_id", "device_id", name=op.f("uq_device_states_user_id")),
        sa.UniqueConstraint("id", "user_id", name="uq_device_states_id_user"),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_device_states_revision_positive")),
        sa.CheckConstraint(
            "device_report_revision >= 1", name=op.f("ck_device_states_report_revision_positive")
        ),
        sa.CheckConstraint(
            "last_schedule_revision IS NULL OR last_schedule_revision >= 0",
            name=op.f("ck_device_states_schedule_revision_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_device_states_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["active_run_id"],
            ["session_runs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_device_states_active_run_id_session_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["dnd_owner_run_id"],
            ["session_runs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_device_states_dnd_owner_run_id_session_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["active_run_id", "user_id"],
            ["session_runs.id", "session_runs.user_id"],
            ondelete="RESTRICT",
            name="fk_device_states_active_run_id_owner",
        ),
        sa.ForeignKeyConstraint(
            ["dnd_owner_run_id", "user_id"],
            ["session_runs.id", "session_runs.user_id"],
            ondelete="RESTRICT",
            name="fk_device_states_dnd_owner_run_id_owner",
        ),
    )
    op.create_index("ix_device_states_user_id", "device_states", ["user_id"])


def downgrade():
    op.drop_index("ix_device_states_user_id", table_name="device_states")
    op.drop_table("device_states")
