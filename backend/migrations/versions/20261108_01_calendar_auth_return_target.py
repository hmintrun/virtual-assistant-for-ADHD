"""Add return_target column to calendar_authorizations table.

Revision ID: 20261108_01
Revises: 20261107_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20261108_01"
down_revision = "20261107_02"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("calendar_authorizations") as batch_op:
        batch_op.add_column(sa.Column("return_target", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("calendar_authorizations") as batch_op:
        batch_op.drop_column("return_target")
