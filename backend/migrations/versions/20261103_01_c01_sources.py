"""Expand C01 sources; retain legacy Part data for a later reviewed cleanup.

No application database is migrated by importing this revision.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261103_01"
down_revision = "20261101_01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("availability", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("source_summary_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_planning_input_revision", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "decomposition_state", sa.String(32), server_default="complete", nullable=False
            )
        )
        batch.add_column(sa.Column("unexpanded_scope", sa.Text(), nullable=True))
    with op.batch_alter_table("plan_candidates") as batch:
        batch.add_column(
            sa.Column(
                "decomposition_state", sa.String(32), server_default="complete", nullable=False
            )
        )
        batch.add_column(sa.Column("unexpanded_scope", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False)
        )
        # Keep original JSON for review/recovery, but new inserts no longer need it.
        batch.alter_column("planning_parts", existing_type=sa.JSON(), nullable=True)
    with op.batch_alter_table("planning_contexts") as batch:
        batch.alter_column("availability_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("availability_revision", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("source_draft_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("source_summary_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("availability_snapshot", sa.JSON(), nullable=True))
        batch.create_foreign_key(
            "fk_planning_contexts_source_draft_id_planning_drafts",
            "planning_drafts",
            ["source_draft_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_planning_contexts_source_draft_id_owner",
            "planning_drafts",
            ["source_draft_id", "user_id"],
            ["id", "user_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            op.f("ck_planning_contexts_context_source_exclusive"),
            "(availability_id IS NOT NULL AND availability_revision IS NOT NULL "
            "AND source_draft_id IS NULL AND source_summary_revision IS NULL AND availability_snapshot IS NULL) "
            "OR (availability_id IS NULL AND availability_revision IS NULL "
            "AND source_draft_id IS NOT NULL AND source_summary_revision IS NOT NULL "
            "AND source_summary_revision > 0 AND availability_snapshot IS NOT NULL)",
        )
    connection = op.get_bind()
    meta = sa.MetaData()
    tasks = sa.Table("tasks", meta, autoload_with=connection)
    parts = sa.Table("planning_parts", meta, autoload_with=connection)
    steps = sa.Table("steps", meta, autoload_with=connection)
    candidates = sa.Table("plan_candidates", meta, autoload_with=connection)
    for task in connection.execute(sa.select(tasks)).mappings():
        rows = list(
            connection.execute(
                sa.select(parts)
                .where(parts.c.task_id == task["id"])
                .order_by(parts.c.order_index, parts.c.id)
            ).mappings()
        )
        remaining = [
            row["unexpanded_scope"] or row["scope_description"]
            for row in rows
            if row["decomposition_state"] != "complete"
        ]
        summary = task["source_summary"] or {}
        connection.execute(
            tasks.update()
            .where(tasks.c.id == task["id"])
            .values(
                availability=summary.get("availability"),
                decomposition_state="partial" if remaining else "complete",
                unexpanded_scope="\n".join(remaining) or None,
            )
        )
        part_order = {row["id"]: index for index, row in enumerate(rows)}
        children = list(
            connection.execute(sa.select(steps).where(steps.c.task_id == task["id"])).mappings()
        )
        children.sort(
            key=lambda row: (
                part_order.get(row["planning_part_id"], len(rows)),
                row["order_index"],
                str(row["id"]),
            )
        )
        for index, child in enumerate(children):
            connection.execute(
                steps.update().where(steps.c.id == child["id"]).values(order_index=index)
            )
    for candidate in connection.execute(sa.select(candidates)).mappings():
        legacy = candidate["planning_parts"] or []
        remaining = [
            part.get("unexpanded_scope") or part.get("scope_description", "")
            for part in legacy
            if part.get("decomposition_state") != "complete"
        ]
        order = {part["client_part_key"]: part["order_index"] for part in legacy}
        children = sorted(
            candidate["steps"] or [],
            key=lambda step: (
                order.get(step.get("client_part_key"), 0),
                step["order_index"],
                step["client_step_key"],
            ),
        )
        report = dict(candidate["feasibility"] or {})
        scope = dict(report.get("verification_scope", {}))
        selected = set(scope.get("client_part_keys", []))
        keys = [
            step["client_step_key"]
            for step in children
            if not selected or step.get("client_part_key") in selected
        ]
        new_steps = []
        for index, child in enumerate(children):
            child = dict(child)
            child.pop("client_part_key", None)
            child["order_index"] = index
            new_steps.append(child)
        scope.pop("client_part_keys", None)
        scope.update(scope_type="candidate_steps", client_step_keys=keys)
        report["verification_scope"] = scope
        report.pop("unestimated_part_ids", None)
        report.pop("unplanned_part_ids", None)
        connection.execute(
            candidates.update()
            .where(candidates.c.id == candidate["id"])
            .values(
                decomposition_state="partial" if remaining else "complete",
                unexpanded_scope="\n".join(filter(None, remaining)) or None,
                steps=new_steps,
                feasibility=report,
            )
        )


def downgrade():
    # Dropping new snapshots would lose provenance; reverse only after data review.
    raise RuntimeError("C01 downgrade requires an explicit snapshot/data recovery plan")
