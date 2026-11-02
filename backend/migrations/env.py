"""Use the same database URL/metadata as the application."""

import asyncio

from alembic import context
from sqlalchemy import inspect

from app.core.database import DatabaseSettings, make_engine
from app.models import Base


def configure(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = make_engine(DatabaseSettings().database_url)
    try:
        async with engine.connect() as connection:
            # SQLite phải tắt FK ngoài transaction khi tái tạo bảng legacy có bản ghi con.
            # Chỉ áp dụng cho DB cũ có hai constraint cùng tên.
            def has_legacy_duplicate(conn):
                if conn.dialect.name != "sqlite" or not inspect(conn).has_table("planning_parts"):
                    return False
                constraints = inspect(conn).get_unique_constraints("planning_parts")
                return (
                    sum(item.get("name") == "uq_planning_parts_task_id" for item in constraints) > 1
                )

            legacy = await connection.run_sync(has_legacy_duplicate)
            await connection.commit()
            if legacy:
                violations = (
                    await connection.exec_driver_sql("PRAGMA foreign_key_check")
                ).fetchall()
                if violations:
                    raise RuntimeError("SQLite foreign key check failed before migration")
                await connection.commit()
                await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                await connection.commit()
            try:
                await connection.run_sync(configure)
                if legacy:
                    violations = (
                        await connection.exec_driver_sql("PRAGMA foreign_key_check")
                    ).fetchall()
                    if violations:
                        raise RuntimeError("SQLite foreign key check failed after migration")
                    await connection.commit()
            finally:
                if legacy:
                    await connection.rollback()
                    await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=DatabaseSettings().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
