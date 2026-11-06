"""Fill missing micro-task settings after the shared C01/device migration head."""

from alembic import context, op
from sqlalchemy import inspect

revision = "20261107_01"
down_revision = "20261106_01"
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        schema = inspect(op.get_bind())
        if "availability" not in {
            column["name"] for column in schema.get_columns("tasks")
        } or not schema.has_table("device_states"):
            raise RuntimeError(
                "Revision 20261106_01 does not match the shared C01/device schema; "
                "review the earlier local scoped-availability migration before upgrading"
            )
    # Không đổi lịch/phiên đã có hoặc mốc nhắc do người dùng lựa chọn.
    op.execute(
        "UPDATE user_preferences SET micro_task_seconds = 120 WHERE micro_task_seconds IS NULL"
    )


def downgrade():
    # Không thể phân biệt giá trị 120 do migration với lựa chọn đã có của user.
    pass
