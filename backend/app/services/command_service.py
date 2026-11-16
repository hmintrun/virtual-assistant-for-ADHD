"""Durable command receipts and replay; no provider calls here."""

import hashlib
import inspect
import json
from datetime import UTC, datetime
from functools import wraps
from uuid import NAMESPACE_DNS, UUID, uuid5

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.models.system import CommandReceipt
from app.models.user import User


def owner_uuid(value):
    try:
        return UUID(value)
    except ValueError:
        return uuid5(NAMESPACE_DNS, f"proplan.user:{value}")


def command_hash(operation, resource, body):
    data = json.dumps(
        {"operation": operation, "resource": resource, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(data.encode()).hexdigest()


async def replay(db, user_id, key, digest):
    row = await db.scalar(
        select(CommandReceipt).where(
            CommandReceipt.user_id == owner_uuid(user_id), CommandReceipt.command_id == key
        )
    )
    if row is None:
        return None
    if row.payload_hash != digest:
        raise AppError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message="Mã lệnh đã được dùng với nội dung khác.",
            next_action="use_original_payload_or_new_command",
        )
    return (row.result_reference or {}).get("_response")


async def lock_owner(db, user_id):
    found = await db.scalar(
        update(User)
        .where(User.id == owner_uuid(user_id))
        .values(revision=User.revision)
        .returning(User.id)
    )
    if found is None:
        raise AppError(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message="Không tìm thấy người dùng.",
            next_action="reload",
        )


def record(db, user_id, key, digest, operation, result):
    refs = {
        name: getattr(result, name)
        for name in (
            "draft_id",
            "thought_id",
            "candidate_id",
            "change_request_id",
            "run_id",
            "checkin_id",
            "session_id",
            "device_id",
            "reminder_id",
        )
        if isinstance(getattr(result, name, None), str)
    }
    linked_draft_id = getattr(result, "linked_draft_id", None)
    if "draft_id" not in refs and isinstance(linked_draft_id, str):
        refs["draft_id"] = linked_draft_id
    nested_checkin = getattr(result, "check_in", None)
    if (
        "checkin_id" not in refs
        and nested_checkin is not None
        and isinstance(getattr(nested_checkin, "checkin_id", None), str)
    ):
        refs["checkin_id"] = nested_checkin.checkin_id
    extra_meta = {
        k: v
        for k, v in (db.info.pop("command_result_meta", None) or {}).items()
        if isinstance(k, str) and k.startswith("_")
    }
    db.add(
        CommandReceipt(
            user_id=owner_uuid(user_id),
            command_id=key,
            command_type=operation,
            payload_hash=digest,
            state="applied",
            result_reference={
                **refs,
                **extra_meta,
                "_response": result.model_dump(mode="json", exclude_unset=True),
            },
            applied_at=datetime.now(UTC),
        )
    )


def durable_command(operation, result_type: type[BaseModel], *, resource_arg: str = "draft_id"):
    """Commit the function's DB changes and receipt together; replay before execution."""

    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            bound = inspect.signature(function).bind(*args, **kwargs)
            db = bound.arguments.get("db")
            if db is None:
                return await function(*args, **kwargs)
            if db.info.get("defer_command_commit"):
                # Lệnh cha (Settings -> sửa Draft) sở hữu transaction và receipt.
                return await function(*args, **kwargs)
            user_id = bound.arguments["user_id"]
            key = bound.arguments["idempotency_key"]
            payload = bound.arguments["payload"].model_dump(mode="json", exclude_unset=True)
            resource_val = bound.arguments.get(resource_arg, "") if resource_arg else ""
            resource_str = "" if resource_val is None else str(resource_val)
            digest = command_hash(operation, resource_str, payload)
            saved = await replay(db, user_id, key, digest)
            if saved is not None:
                return result_type.model_validate(saved)
            await db.rollback()
            db.info["defer_command_commit"] = True
            try:
                if operation != "analyze_draft":
                    await lock_owner(db, user_id)
                    saved = await replay(db, user_id, key, digest)
                    if saved is not None:
                        await db.rollback()
                        return result_type.model_validate(saved)
                result = await function(*args, **kwargs)
                await lock_owner(db, user_id)
                saved = await replay(db, user_id, key, digest)
                if saved is not None:
                    await db.rollback()
                    return result_type.model_validate(saved)
                record(db, user_id, key, digest, operation, result)
                await db.commit()
                return result
            except (IntegrityError, AppError):
                await db.rollback()
                saved = await replay(db, user_id, key, digest)
                if saved is not None:
                    return result_type.model_validate(saved)
                raise
            except Exception:
                await db.rollback()
                raise
            finally:
                db.info.pop("defer_command_commit", None)
                db.info.pop("command_result_meta", None)

        return wrapped

    return decorate
