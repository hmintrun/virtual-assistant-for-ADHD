"""Restrict uq_user_live_run partial unique index to running and paused states."""

import sqlalchemy as sa
from alembic import op

revision = "20261107_02"
down_revision = "20261107_01"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("uq_user_live_run", table_name="session_runs")
    op.create_index(
        "uq_user_live_run",
        "session_runs",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("run_state IN ('running', 'paused')"),
        postgresql_where=sa.text("run_state IN ('running', 'paused')"),
    )


def downgrade():
    op.drop_index("uq_user_live_run", table_name="session_runs")
    op.create_index(
        "uq_user_live_run",
        "session_runs",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("run_state IN ('running', 'paused', 'awaiting_checkin')"),
        postgresql_where=sa.text("run_state IN ('running', 'paused', 'awaiting_checkin')"),
    )
