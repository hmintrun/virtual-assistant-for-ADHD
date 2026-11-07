"""Join C01 and week-two device storage without rewriting either history."""

revision = "20261106_01"
down_revision = ("20261103_01", "20261105_01")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    # Chặn ngay ở head trước khi Alembic có thể xóa DeviceState rồi mới gặp guard C01.
    raise RuntimeError("C01 downgrade requires an explicit snapshot/data recovery plan")
