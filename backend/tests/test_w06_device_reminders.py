"""W06: real device reports, device-isolated reminders and durable receipts on migrated SQLite."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models import CommandReceipt, Reminder, ScheduleVersion, Session, User
from app.services import device_service
from tests import test_w05_execution_checkin as baseline

w05_env = baseline.w05_env
w05_client = baseline.w05_client
_start_run = baseline._start_run


@pytest.fixture(autouse=True)
def reminder_clock(monkeypatch, w05_env):
    monkeypatch.setattr(
        device_service, "_now", lambda: w05_env["scheduled_start"] - timedelta(minutes=30)
    )


def report(revision=1, **changes):
    return {
        "device_report_revision": revision,
        "notification_permission": "denied",
        "microphone_permission": "granted",
        "dnd_permission": "denied",
        "alarm_capability": "inexact_only",
        "active_run_id": None,
        "last_schedule_revision": 1,
        "dnd_owner_run_id": None,
        "dnd_apply_state": "failed",
        "dnd_restore_state": "not_needed",
        "reported_at": "2026-10-30T09:00:00Z",
        "app_visibility": "foreground",
        **changes,
    }


def window(env, device="android_pixel_8", **changes):
    start = env["scheduled_start"]
    return {
        "device_id": device,
        "from": (start - timedelta(minutes=30)).isoformat(),
        "to": (start - timedelta(minutes=1)).isoformat(),
        **changes,
    }


async def send_report(client, data, key=None, device="android_pixel_8"):
    return await client.put(
        f"/api/v1/devices/{device}/state",
        json=data,
        headers={"Idempotency-Key": key or str(uuid4())},
    )


async def get_reminders(client, env, **changes):
    res = await client.get("/api/v1/reminders", params=window(env, **changes))
    assert res.status_code == 200, res.text
    return res.json()["data"]["items"]


async def send_receipt(client, row, key=None, **changes):
    return await client.post(
        f"/api/v1/reminders/{row['reminder_id']}/receipt",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={
            "device_id": "android_pixel_8",
            "schedule_revision": row["schedule_revision"],
            "delivery_state": "delivered",
            "occurred_at": row["fire_at"],
            **changes,
        },
    )


async def test_device_reports_preserve_real_states_and_replay_after_restart(w05_client, w05_env):
    key = str(uuid4())
    first = await send_report(w05_client, report(), key)
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["notification_permission"] == "denied"
    assert data["dnd_apply_state"] == "failed"
    assert data["alarm_capability"] == "inexact_only"
    assert (await send_report(w05_client, report(2, dnd_restore_state="failed"))).status_code == 200
    stale = await send_report(w05_client, report(2))
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
    await w05_env["engine"].dispose()
    replay = await send_report(w05_client, report(), key)
    assert replay.status_code == 200 and replay.json()["data"] == data
    changed = await send_report(w05_client, report(3), key)
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize(
    "changes",
    [
        {"notification_permission": True},
        {"notification_permission": "yes"},
        {"dnd_apply_state": "success"},
        {"reported_at": "2026-10-30T09:00:00"},
    ],
)
async def test_device_rejects_invalid_states_and_naive_time(w05_client, changes):
    res = await send_report(w05_client, report(**changes))
    assert res.status_code == 422


async def test_device_report_run_owner_device_and_ended_guards(w05_client, w05_env):
    run = await _start_run(w05_client, w05_env)
    valid = await send_report(
        w05_client, report(active_run_id=run["run_id"], dnd_owner_run_id=run["run_id"])
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["data"]["active_run_id"] == run["run_id"]
    wrong_device = await send_report(
        w05_client, report(active_run_id=run["run_id"]), device="other"
    )
    assert wrong_device.status_code == 409
    missing = await send_report(w05_client, report(2, dnd_owner_run_id=str(uuid4())))
    assert missing.status_code == 404
    async with w05_env["sessions"]() as db:
        foreign = User(email="foreign-w06@example.test", display_name="Other")
        db.add(foreign)
        await db.commit()
        foreign_id = str(foreign.id)
    async with w05_env["sessions"]() as db:
        from app.core.errors import AppError
        from app.schemas.execution import DeviceStateReportRequest

        with pytest.raises(AppError) as err:
            await device_service.save_device_state_report(
                db,
                user_id=foreign_id,
                device_id="android_pixel_8",
                idempotency_key=str(uuid4()),
                payload=DeviceStateReportRequest(**report(active_run_id=run["run_id"])),
            )
        assert err.value.status_code == 404
    ended = await w05_client.post(
        f"/api/v1/runs/{run['run_id']}/end",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "device_id": "android_pixel_8",
            "expected_run_revision": run["run_revision"],
            "event_id": "end-w06",
            "ended_at": (w05_env["scheduled_start"] + timedelta(seconds=10)).isoformat(),
            "end_reason": "user_stopped",
            "work_elapsed_seconds": 10,
            "rest_elapsed_seconds": 0,
        },
    )
    assert ended.status_code == 200, ended.text
    assert (
        await send_report(w05_client, report(2, active_run_id=run["run_id"]))
    ).status_code == 409
    assert (
        await send_report(w05_client, report(2, dnd_owner_run_id=run["run_id"]))
    ).status_code == 200


async def test_reminders_fire_window_concurrent_dedupe_and_device_isolation(w05_client, w05_env):
    responses = await asyncio.gather(
        *(w05_client.get("/api/v1/reminders", params=window(w05_env)) for _ in range(3))
    )
    assert [r.status_code for r in responses] == [200, 200, 200]
    pages = [r.json()["data"]["items"] for r in responses]
    assert pages[0] == pages[1] == pages[2]
    assert len(pages[0]) == 3
    assert all(
        r["kind"] == "before_session" and r["device_id"] == "android_pixel_8" for r in pages[0]
    )
    other = await get_reminders(w05_client, w05_env, device="tablet")
    assert len(other) == 3
    assert {r["reminder_id"] for r in other}.isdisjoint(r["reminder_id"] for r in pages[0])
    async with w05_env["sessions"]() as db:
        count = await db.scalar(
            select(func.count()).select_from(Reminder).where(Reminder.device_id.is_not(None))
        )
        assert count == 6
    start = w05_env["scheduled_start"]
    narrow = await get_reminders(
        w05_client,
        w05_env,
        **{
            "from": (start - timedelta(minutes=20)).isoformat(),
            "to": (start - timedelta(minutes=14)).isoformat(),
        },
    )
    assert [r["fire_at"] for r in narrow] == [
        (start - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    ]


async def test_reminders_skip_elapsed_offsets_and_reject_bad_windows(
    w05_client, w05_env, monkeypatch
):
    start = w05_env["scheduled_start"]
    monkeypatch.setattr(device_service, "_now", lambda: start - timedelta(minutes=7))
    rows = await get_reminders(w05_client, w05_env)
    assert len(rows) == 1
    assert rows[0]["fire_at"] == (start - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    for changes in (
        {"to": (start - timedelta(hours=1)).isoformat()},
        {"from": "2026-10-30T08:00:00"},
    ):
        res = await w05_client.get("/api/v1/reminders", params=window(w05_env, **changes))
        assert res.status_code == 422


async def test_global_revision_does_not_cancel_unchanged_task_reminders(w05_client, w05_env):
    original = await get_reminders(w05_client, w05_env)
    async with w05_env["sessions"]() as db:
        db.add(
            ScheduleVersion(
                user_id=w05_env["user_id"],
                version_number=2,
                base_version=1,
                affected_task_ids=[],
                approved_at=w05_env["scheduled_start"],
                min_inter_session_gap_seconds=900,
            )
        )
        await db.commit()
    res = await w05_client.get("/api/v1/reminders", params=window(w05_env, schedule_revision=2))
    assert res.status_code == 200, res.text
    assert res.json()["data"]["items"] == original
    stale = await w05_client.get("/api/v1/reminders", params=window(w05_env, schedule_revision=1))
    assert stale.status_code == 409
    assert stale.json()["error"]["current_revisions"]["schedule"] == 2
    assert (await send_receipt(w05_client, original[0])).status_code == 200


async def test_receipt_durable_and_cancelled_after_start(w05_client, w05_env):
    rows = await get_reminders(w05_client, w05_env)
    key = str(uuid4())
    delivered = await send_receipt(w05_client, rows[0], key)
    assert delivered.status_code == 200, delivered.text
    await w05_env["engine"].dispose()
    replay = await send_receipt(w05_client, rows[0], key)
    assert replay.json()["data"] == delivered.json()["data"]
    assert (await send_receipt(w05_client, rows[1], schedule_revision=2)).status_code == 409
    assert (await send_receipt(w05_client, rows[1], device_id="tablet")).status_code == 409
    assert (await send_receipt(w05_client, rows[1], delivery_state="made_up")).status_code == 422
    await _start_run(w05_client, w05_env)
    late = await send_receipt(w05_client, rows[1])
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "INVALID_REMINDER_STATE"
    async with w05_env["sessions"]() as db:
        row = await db.get(Reminder, UUID(rows[1]["reminder_id"]))
        assert row.delivery_state == "cancelled"
        receipt = await db.scalar(select(CommandReceipt).where(CommandReceipt.command_id == key))
        assert receipt.result_reference["reminder_id"] == rows[0]["reminder_id"]


async def test_session_revision_change_cancels_old_reminders_persistently(w05_client, w05_env):
    rows = await get_reminders(w05_client, w05_env)
    async with w05_env["sessions"]() as db:
        session = await db.get(Session, w05_env["session_id"])
        session.schedule_revision = 2
        await db.commit()
    page = await get_reminders(w05_client, w05_env)
    assert len(page) == 6
    assert sum(r["delivery_state"] == "cancelled" for r in page) == 3
    assert sum(r["schedule_revision"] == 2 for r in page) == 3
    assert (await send_receipt(w05_client, rows[0])).status_code == 409
    await w05_env["engine"].dispose()
    async with w05_env["sessions"]() as db:
        assert (await db.get(Reminder, UUID(rows[0]["reminder_id"]))).delivery_state == "cancelled"
