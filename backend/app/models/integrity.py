"""Portable database invariants shared by all persistent tables."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.models.base import Base


def configure_integrity() -> None:
    # FK ghép ngăn gắn bản ghi của user A vào dữ liệu user B, kể cả ghi SQL trực tiếp.
    for table in Base.metadata.tables.values():
        table.append_constraint(CheckConstraint("revision >= 1", name="revision_positive"))
        if "user_id" in table.c:
            table.append_constraint(
                UniqueConstraint("id", "user_id", name=f"uq_{table.name}_id_user")
            )
    for table in Base.metadata.tables.values():
        if "user_id" not in table.c:
            continue
        for fk in list(table.foreign_keys):
            if fk.constraint is None or len(fk.constraint.elements) != 1:
                continue
            target = fk.column.table
            if fk.parent.name == "user_id" or "user_id" not in target.c:
                continue
            name = f"fk_{table.name}_{fk.parent.name}_owner"
            table.append_constraint(
                ForeignKeyConstraint(
                    [fk.parent.name, "user_id"],
                    [f"{target.name}.{fk.column.name}", f"{target.name}.user_id"],
                    name=name,
                    ondelete="RESTRICT",
                    use_alter=fk.use_alter,
                )
            )
