"""Repair SQLite databases created with duplicate planning_parts constraint names.

The initial migration now names both constraints distinctly for fresh databases.
This revision repairs old SQLite databases without changing their business data.
"""

from alembic import context, op
from sqlalchemy import inspect

revision = "20261101_01"
down_revision = "0ece3b500cfe"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode():
        # Fresh PostgreSQL SQL uses corrected names in the initial migration.
        return
    connection = op.get_bind()
    constraints = inspect(connection).get_unique_constraints("planning_parts")
    old = [item for item in constraints if item.get("name") == "uq_planning_parts_task_id"]
    if not old:
        return
    if connection.dialect.name != "sqlite" or len(old) != 2:
        raise RuntimeError("Unexpected legacy constraints; inspect database before migration")
    with op.batch_alter_table("planning_parts", recreate="always") as batch:
        batch.drop_constraint("uq_planning_parts_task_id", type_="unique")
        batch.create_unique_constraint(
            "uq_planning_parts_task_client_key", ["task_id", "client_part_key"]
        )
        batch.create_unique_constraint("uq_planning_parts_task_order", ["task_id", "order_index"])


def downgrade():
    # Names only; retaining distinct names preserves both uniqueness guarantees.
    pass
