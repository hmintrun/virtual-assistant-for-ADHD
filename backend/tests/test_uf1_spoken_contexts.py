"""Kiểm thử toàn diện ngữ cảnh văn nói UF1 BE-01 across 3 tầng (Schema, AIService, DraftService/API)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.services.ai_service as ai_service_module
import app.services.draft_service as draft_service_module
from app.api.deps import get_db
from app.core.config import Settings
from app.core.database import make_engine, session_factory
from app.main import app
from app.models import Availability as AvailabilityRow
from app.models import Base
from app.models.settings import Availability as SettingsAvailabilityRow
from app.schemas.availability import DraftAvailability
from app.schemas.calendar import BusyInterval, BusyIntervalSource, CalendarContext
from app.schemas.common import TimeInterval
from app.schemas.task import (
    ExtractedFreeWindowSchema,
    ExtractedTimeConstraint,
    LLMTaskExtractionPayload,
)
from app.services.ai_service import (
    UF1_EXTRACTION_SYSTEM_PROMPT,
    AIService,
    ai_service,
)
from app.services.draft_service import resolve_user_uuid
from app.services.planning.availability import compute_effective_free_intervals

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Thứ Tư 2026-11-18 09:00+07:00 để toàn bộ ngày tuần 16..22/11 và 23..29/11 tất định
FROZEN_NOW = datetime(2026, 11, 18, 9, 0, tzinfo=VN_TZ)
WEEK_START = datetime(2026, 11, 16, 7, 0, tzinfo=VN_TZ)
WEEK_DEADLINE = datetime(2026, 11, 22, 23, 59, tzinfo=VN_TZ)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)


def _week_tc(
    *,
    start_at: datetime = WEEK_START,
    deadline_at: datetime = WEEK_DEADLINE,
    tz: str = "Asia/Ho_Chi_Minh",
) -> ExtractedTimeConstraint:
    return ExtractedTimeConstraint(
        kind="deadline",
        start_at=start_at,
        deadline_at=deadline_at,
        input_timezone=tz,
    )


def _mock_ai_service() -> AIService:
    return AIService(
        app_settings=Settings(
            _env_file=None,
            GEMINI_API_KEY="AIzaSyMockTestKey",
            GROQ_API_KEY="gsk_MockTestKey",
            GOOGLE_CREDENTIALS_PATH="",
        )
    )


@pytest.fixture
async def uf1_api(monkeypatch: pytest.MonkeyPatch):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)

    async def database():
        async with factory() as db:
            yield db

    monkeypatch.setattr(draft_service_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(ai_service_module, "datetime", _FrozenDatetime)
    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def _create_draft(
    client: AsyncClient,
    *,
    key: str,
    raw_input: str = "Làm báo cáo môn học",
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "client_draft_id": key,
        "input_source": "text",
        "raw_input": raw_input,
    }
    if summary is not None:
        body["summary"] = summary
    res = await client.post("/api/v1/drafts", headers={"Idempotency-Key": key}, json=body)
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def _analyze(
    client: AsyncClient,
    draft: dict[str, Any],
    *,
    key: str,
    message: str,
    raw_input: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expected_summary_revision": draft["summary_revision"],
        "expected_planning_input_revision": draft["planning_input_revision"],
        "message": message,
    }
    if raw_input is not None:
        body["raw_input"] = raw_input
    res = await client.post(
        f"/api/v1/drafts/{draft['draft_id']}/analyze",
        headers={"Idempotency-Key": key},
        json=body,
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _set_ai_sequence(
    monkeypatch: pytest.MonkeyPatch,
    payloads: list[LLMTaskExtractionPayload],
    *,
    captured_prompts: list[dict[str, Any]] | None = None,
) -> None:
    queue = list(payloads)

    async def _fake_hedged(**kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        if captured_prompts is not None:
            captured_prompts.append(json.loads(kwargs["user_prompt"]))
        assert queue, "Hết payload mock cho _execute_hedged_structured"
        return queue.pop(0), "tier_1_gemini"

    monkeypatch.setattr(ai_service, "_execute_hedged_structured", _fake_hedged)


def _override_map(availability_dict: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    return {
        item["date"]: [(iv["start_local"][:5], iv["end_local"][:5]) for iv in item["intervals"]]
        for item in availability_dict["date_overrides"]
    }


def _weekly_map(availability_dict: dict[str, Any]) -> dict[int, list[tuple[str, str]]]:
    out: dict[int, list[tuple[str, str]]] = {}
    for rule in availability_dict["weekly_rules"]:
        out.setdefault(rule["weekday"], []).append(
            (rule["start_local"][:5], rule["end_local"][:5])
        )
    return out


# =============================================================================
# 1. NHÓM NỀN BE-01 (B0.1 – B0.6)
# =============================================================================


@pytest.mark.parametrize(
    "window_kwargs",
    [
        {
            "kind": "daily",
            "start_local": "19:00",
            "end_local": "22:00",
            "raw_phrase": "19:00 đến 22:00 hằng ngày",
        },
        {
            "kind": "weekly_rule",
            "weekday": None,
            "start_local": "19:00",
            "end_local": "22:00",
            "raw_phrase": "19:00 đến 22:00 hằng ngày",
        },
    ],
)
async def test_b0_1_daily_turn1_explicit_and_fallback(
    uf1_api, monkeypatch: pytest.MonkeyPatch, window_kwargs: dict[str, Any]
) -> None:
    """B0.1: Lượt 1 hiểu ngay '19:00 đến 22:00 hằng ngày' (cả kind='daily' lẫn fallback LLM)."""
    # Tầng Schema
    schema_win = ExtractedFreeWindowSchema.model_validate(window_kwargs)
    assert schema_win.kind == "daily"
    assert schema_win.is_ambiguous is False

    # Tầng AIService
    svc = _mock_ai_service()
    payload = LLMTaskExtractionPayload(
        title="Báo cáo tuần",
        content="Viết báo cáo tuần môn Hệ thống thông minh",
        scope="5 trang Word kèm biểu đồ",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        free_time_slots=["19:00 đến 22:00 hằng ngày"],
        structured_free_windows=[ExtractedFreeWindowSchema.model_validate(window_kwargs)],
        is_complete=True,
        summary="Báo cáo tuần, rảnh 19:00-22:00 hằng ngày, hạn 22/11.",
    )

    async def _fake_svc(**kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        return payload, "tier_1_gemini"

    with monkeypatch.context() as mp:
        mp.setattr(svc, "_execute_hedged_structured", _fake_svc)
        extracted = await svc.extract_task_intent(
            "Làm báo cáo 5 trang, rảnh 19:00 đến 22:00 hằng ngày, hạn 22/11",
            reference_now=FROZEN_NOW,
        )
    assert extracted.is_complete is True
    assert extracted.structured_free_windows[0].kind == "daily"
    assert extracted.structured_free_windows[0].is_ambiguous is False

    # Tầng DraftService / API end-to-end
    client, _ = uf1_api
    _set_ai_sequence(monkeypatch, [payload])
    draft = await _create_draft(client, key=f"b01-{window_kwargs['kind']}")
    analyzed = await _analyze(
        client,
        draft,
        key=f"b01-a-{window_kwargs['kind']}",
        message="Viết báo cáo 5 trang, rảnh 19:00 đến 22:00 hằng ngày từ 16/11 đến 22/11",
    )
    summary = analyzed["summary"]
    assert summary["can_plan"] is True
    assert not [i for i in summary["clarification_issues"] if i["blocking"]]
    w_map = _weekly_map(summary["availability"])
    assert w_map == {d: [("19:00", "22:00")] for d in range(1, 8)}
    o_map = _override_map(summary["availability"])
    for day_num in range(16, 23):
        assert o_map[f"2026-11-{day_num:02d}"] == [("19:00", "22:00")]


async def test_b0_2_vague_daily_turn1_then_7_specific_dates_turn2(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B0.2: Lượt 1 mơ hồ 'tối hằng ngày' -> can_plan=False; Lượt 2 bổ sung 7 specific_date -> can_plan=True."""
    client, _ = uf1_api
    turn1_payload = LLMTaskExtractionPayload(
        title="Báo cáo đồ án",
        content="Viết báo cáo đồ án",
        scope="8 trang tài liệu",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        free_time_slots=["tối hằng ngày"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local=None,
                end_local=None,
                raw_phrase="tối hằng ngày",
            )
        ],
        is_complete=False,
        summary="Báo cáo 8 trang, rảnh tối hằng ngày (chưa rõ giờ).",
        clarification_questions=["Khung giờ 'tối hằng ngày' bạn làm từ mấy giờ đến mấy giờ?"],
    )
    assert turn1_payload.structured_free_windows[0].is_ambiguous is True

    turn2_payload = LLMTaskExtractionPayload(
        title="Báo cáo đồ án",
        content="Viết báo cáo đồ án",
        scope="8 trang tài liệu",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        availability_update_mode="merge",
        free_time_slots=[f"2026-11-{d:02d} 19:00-22:00" for d in range(16, 23)],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date=f"2026-11-{d:02d}",
                start_local="19:00",
                end_local="22:00",
                raw_phrase=f"Ngày {d}/11 từ 19:00 đến 22:00",
            )
            for d in range(16, 23)
        ],
        is_complete=True,
        summary="Báo cáo 8 trang, đã chốt 7 ngày 16-22/11 từ 19:00-22:00.",
    )
    _set_ai_sequence(monkeypatch, [turn1_payload, turn2_payload])

    draft = await _create_draft(client, key="b02")
    d1 = await _analyze(
        client,
        draft,
        key="b02-t1",
        message="Viết báo cáo 8 trang, rảnh tối hằng ngày, hạn 22/11",
    )
    assert d1["summary"]["can_plan"] is False
    assert any(
        i["code"] == "AI_AVAILABILITY_INCOMPLETE" and i["blocking"]
        for i in d1["summary"]["clarification_issues"]
    )

    d2 = await _analyze(
        client,
        d1,
        key="b02-t2",
        message="Từ 16/11 đến 22/11 mình rảnh 19:00 đến 22:00",
    )
    assert d2["summary"]["can_plan"] is True
    assert not any(
        i["code"] == "AI_AVAILABILITY_INCOMPLETE"
        for i in d2["summary"]["clarification_issues"]
    )
    assert d2["summary"]["availability"]["weekly_rules"] == []
    o_map = _override_map(d2["summary"]["availability"])
    assert len(o_map) == 7
    assert o_map["2026-11-18"] == [("19:00", "22:00")]


def test_b0_2_selective_unanchored_cleanup_preserves_other_vague_window() -> None:
    """B0.2 guard: Khi có 2 cụm mơ hồ khác nhau, làm rõ cụm 'tối hằng ngày' không xóa nhầm cụm thứ 2."""
    prev = [
        ExtractedFreeWindowSchema(
            kind="weekly_rule",
            weekday=None,
            raw_phrase="tối hằng ngày",
        ),
        ExtractedFreeWindowSchema(
            kind="weekly_rule",
            weekday=6,
            raw_phrase="chiều thứ 7",
        ),
    ]
    inc = [
        ExtractedFreeWindowSchema(
            kind="specific_date",
            date="2026-11-18",
            start_local="19:00",
            end_local="21:00",
            raw_phrase="Ngày 18/11 19:00-21:00",
        )
    ]
    merged = draft_service_module._merge_free_windows(prev, inc)
    phrases = [w.raw_phrase for w in merged]
    assert "tối hằng ngày" not in phrases
    assert "chiều thứ 7" in phrases


async def test_b0_3_grouped_weekdays_and_fallback(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B0.3: Nhiều thứ gộp (weekdays=[1..5], [6,7], fallback từ raw_phrase, lọc trùng/báo lỗi)."""
    w_dedup = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekdays=[5, 1, 5, 3],
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ 2, 4, 6 từ 19h-21h",
    )
    assert w_dedup.weekdays == [1, 3, 5]
    assert w_dedup.is_ambiguous is False

    w_invalid = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekdays=[0, 8],
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ không hợp lệ",
    )
    assert w_invalid.weekdays is None
    assert w_invalid.is_ambiguous is True

    w_empty = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekdays=[],
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ rỗng",
    )
    assert w_empty.weekdays is None
    assert w_empty.is_ambiguous is True

    w_fb_weekdays = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekdays=None,
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ 2 đến thứ 6 19h-21h",
    )
    assert w_fb_weekdays.weekdays == [1, 2, 3, 4, 5]
    assert w_fb_weekdays.is_ambiguous is False

    w_fb_weekend = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekdays=None,
        start_local="09:00",
        end_local="11:00",
        raw_phrase="Cuối tuần 09h-11h",
    )
    assert w_fb_weekend.weekdays == [6, 7]
    assert w_fb_weekend.is_ambiguous is False

    client, _ = uf1_api
    payload = LLMTaskExtractionPayload(
        title="Học tiếng Anh",
        content="Ôn luyện ngữ pháp tiếng Anh",
        scope="Hoàn thành 3 unit",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[w_fb_weekdays, w_fb_weekend],
        is_complete=True,
        summary="Ôn tiếng Anh thứ 2-6 tối và cuối tuần sáng.",
    )
    _set_ai_sequence(monkeypatch, [payload])
    draft = await _create_draft(client, key="b03")
    analyzed = await _analyze(
        client,
        draft,
        key="b03-a",
        message="Thứ 2 đến thứ 6 19h-21h, cuối tuần 9h-11h",
    )
    assert analyzed["summary"]["can_plan"] is True
    w_map = _weekly_map(analyzed["summary"]["availability"])
    assert w_map == {
        1: [("19:00", "21:00")],
        2: [("19:00", "21:00")],
        3: [("19:00", "21:00")],
        4: [("19:00", "21:00")],
        5: [("19:00", "21:00")],
        6: [("09:00", "11:00")],
        7: [("09:00", "11:00")],
    }


async def test_b0_4_date_range_valid_and_invalid(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B0.4: Khoảng ngày date_range 19/11..25/11 và bắt lỗi ngày không tồn tại / date_from > date_to."""
    bad_leap = ExtractedFreeWindowSchema(
        kind="date_range",
        date_from="2026-02-30",
        date_to="2026-03-02",
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Từ 30/2 đến 2/3",
    )
    assert bad_leap.date_from is None
    assert bad_leap.is_ambiguous is True

    bad_order = ExtractedFreeWindowSchema(
        kind="date_range",
        date_from="2026-11-25",
        date_to="2026-11-19",
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Từ 25/11 đến 19/11",
    )
    assert bad_order.is_ambiguous is True

    client, _ = uf1_api
    payload = LLMTaskExtractionPayload(
        title="Ôn tập giữa kỳ",
        content="Ôn tập môn Cơ sở dữ liệu",
        scope="Đọc hết chương 4-6",
        time_constraint=_week_tc(deadline_at=datetime(2026, 11, 26, 23, 59, tzinfo=VN_TZ)),
        deadline=datetime(2026, 11, 26, 23, 59, tzinfo=VN_TZ),
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="date_range",
                date_from="2026-11-19",
                date_to="2026-11-25",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Từ 19/11 đến 25/11 rảnh 19h-21h",
            )
        ],
        is_complete=True,
        summary="Ôn CSDL từ 19/11 đến 25/11 khung 19:00-21:00.",
    )
    _set_ai_sequence(monkeypatch, [payload])
    draft = await _create_draft(client, key="b04")
    analyzed = await _analyze(
        client, draft, key="b04-a", message="Từ 19/11 đến 25/11 rảnh 19h-21h"
    )
    assert analyzed["summary"]["can_plan"] is True
    o_map = _override_map(analyzed["summary"]["availability"])
    assert sorted(o_map) == [f"2026-11-{d:02d}" for d in range(19, 26)]
    assert all(ivs == [("19:00", "21:00")] for ivs in o_map.values())


async def test_b0_5_default_availability_passed_without_ref(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B0.5: Draft chưa gắn availability_ref nhưng user có lịch mặc định -> context truyền default_free_windows."""
    client, factory = uf1_api
    await client.get("/api/v1/preferences")
    async with factory() as db:
        db.add(
            AvailabilityRow(
                user_id=resolve_user_uuid("user_a"),
                source="user_confirmed",
                confirmed_at=FROZEN_NOW.astimezone(UTC),
                timezone="Asia/Ho_Chi_Minh",
                weekly_rules=[{"weekday": 5, "start_local": "20:00", "end_local": "22:00"}],
                date_overrides=[],
                task_limits=[],
            )
        )
        await db.commit()

    captured: list[dict[str, Any]] = []
    payload = LLMTaskExtractionPayload(
        title="Đọc tài liệu",
        content="Đọc tài liệu nghiên cứu",
        scope="Tóm tắt 2 bài báo",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=5,
                start_local="20:00",
                end_local="22:00",
                raw_phrase="Thứ 6 20:00-22:00 (lịch mặc định)",
            )
        ],
        is_complete=True,
        summary="Đọc 2 bài báo vào lịch mặc định thứ 6.",
    )
    _set_ai_sequence(monkeypatch, [payload], captured_prompts=captured)

    draft = await _create_draft(client, key="b05")
    assert draft["summary"]["availability_ref"] is None
    analyzed = await _analyze(
        client, draft, key="b05-a", message="Dùng lịch mặc định của mình nhé"
    )
    assert analyzed["summary"]["can_plan"] is True
    assert len(captured) == 1
    default_windows = captured[0]["default_free_windows"]
    assert len(default_windows) == 1
    assert default_windows[0]["weekday"] == 5
    assert default_windows[0]["start_local"] == "20:00"
    assert default_windows[0]["end_local"] == "22:00"


@pytest.mark.parametrize(
    "missing_field",
    ["title", "scope", "deadline", "availability"],
)
async def test_b0_6_semantic_guard_blocks_hallucinated_is_complete(
    uf1_api, monkeypatch: pytest.MonkeyPatch, missing_field: str
) -> None:
    """B0.6: LLM ảo giác is_complete=True khi thiếu title/scope/deadline/availability -> can_plan=False."""
    client, _ = uf1_api
    base_windows = (
        []
        if missing_field == "availability"
        else [
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="19:00-21:00 hằng ngày",
            )
        ]
    )
    tc = None if missing_field == "deadline" else _week_tc()
    dl = None if missing_field == "deadline" else WEEK_DEADLINE
    payload = LLMTaskExtractionPayload(
        title=None if missing_field == "title" else "Làm bài tập lớn",
        content="Làm bài tập lớn môn AI",
        scope=None if missing_field == "scope" else "Hoàn thiện module tìm kiếm",
        time_constraint=tc,
        deadline=dl,
        structured_free_windows=base_windows,
        is_complete=True,  # LLM ảo giác is_complete=True
        summary="Tóm tắt bài tập lớn.",
    )
    _set_ai_sequence(monkeypatch, [payload])

    draft = await _create_draft(client, key=f"b06-{missing_field}")
    analyzed = await _analyze(
        client,
        draft,
        key=f"b06-a-{missing_field}",
        message="Làm bài tập lớn môn AI",
    )
    assert analyzed["summary"]["can_plan"] is False
    blocking = [i for i in analyzed["summary"]["clarification_issues"] if i["blocking"]]
    assert len(blocking) >= 1


# =============================================================================
# 2. TOÀN BỘ 13 NGỮ CẢNH VĂN NÓI MỞ RỘNG (#1 – #13)
# =============================================================================


async def test_ctx_01_add_extend_vs_replace_day(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1: op='add' nối dài thứ Sáu 19-21h -> 22h, giữ nền khi thêm ngày 20; op='replace_day' thay mới."""
    client, _ = uf1_api
    # Ca 1: Thứ Sáu 19h-21h rồi nối dài đến 22h (op="add")
    t1 = LLMTaskExtractionPayload(
        title="Ôn thi",
        content="Ôn thi cuối kỳ",
        scope="Chương 1-3",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=5,
                start_local="19:00",
                end_local="21:00",
                op="add",
                raw_phrase="Thứ Sáu 19h-21h",
            )
        ],
        is_complete=True,
        summary="Ôn thi tối thứ 6 19-21h.",
    )
    t2 = LLMTaskExtractionPayload(
        title="Ôn thi",
        content="Ôn thi cuối kỳ",
        scope="Chương 1-3",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=5,
                start_local="21:00",
                end_local="22:00",
                op="add",
                raw_phrase="Thứ Sáu thêm đến 22h",
            )
        ],
        is_complete=True,
        summary="Ôn thi tối thứ 6 19-22h.",
    )
    # Ca 2a: Ngày 20 (Thứ Sáu) theo nền 19-22h, thêm 20-21h bằng op="add" -> vẫn giữ nguyên nền 19-22h
    t2b = LLMTaskExtractionPayload(
        title="Ôn thi",
        content="Ôn thi cuối kỳ",
        scope="Chương 1-3",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                start_local="20:00",
                end_local="21:00",
                op="add",
                raw_phrase="Ngày 20 thêm 20h-21h",
            )
        ],
        is_complete=True,
        summary="Ngày 20/11 giữ nền 19-22h.",
    )
    # Ca 2b: Ngày 20 (Thứ Sáu) theo nền 19-22h, thêm 21-23h bằng op="add" -> union thành 19:00-23:00
    t3 = LLMTaskExtractionPayload(
        title="Ôn thi",
        content="Ôn thi cuối kỳ",
        scope="Chương 1-3",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                start_local="21:00",
                end_local="23:00",
                op="add",
                raw_phrase="Ngày 20 thêm 21h-23h",
            )
        ],
        is_complete=True,
        summary="Ngày 20/11 giữ nền 19-22h và thêm 21-23h.",
    )
    # Ca 3: op="replace_day" thay mới hoàn toàn ngày 20 thành 08:00-10:00
    t4 = LLMTaskExtractionPayload(
        title="Ôn thi",
        content="Ôn thi cuối kỳ",
        scope="Chương 1-3",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                start_local="08:00",
                end_local="10:00",
                op="replace_day",
                raw_phrase="Đổi ngày 20 sang 08h-10h",
            )
        ],
        is_complete=True,
        summary="Ngày 20/11 đổi sang 08:00-10:00.",
    )
    _set_ai_sequence(monkeypatch, [t1, t2, t2b, t3, t4])

    d = await _create_draft(client, key="ctx01")
    d = await _analyze(client, d, key="ctx01-1", message="Thứ Sáu 19h-21h")
    assert _weekly_map(d["summary"]["availability"])[5] == [("19:00", "21:00")]

    d = await _analyze(client, d, key="ctx01-2", message="Thứ Sáu thêm đến 22h")
    assert _weekly_map(d["summary"]["availability"])[5] == [("19:00", "22:00")]

    d = await _analyze(client, d, key="ctx01-2b", message="Ngày 20 thêm 20h-21h")
    assert _override_map(d["summary"]["availability"])["2026-11-20"] == [("19:00", "22:00")]

    d = await _analyze(client, d, key="ctx01-3", message="Ngày 20 thêm 21h-23h")
    assert _override_map(d["summary"]["availability"])["2026-11-20"] == [("19:00", "23:00")]

    d = await _analyze(client, d, key="ctx01-4", message="Thôi đổi ngày 20 sang 8h-10h")
    assert _override_map(d["summary"]["availability"])["2026-11-20"] == [("08:00", "10:00")]


async def test_ctx_02_partial_exclude_and_busy_only_without_base(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2: Nền 19-22h trừ bận 20-21h -> [19-20, 21-22]; chỉ báo giờ bận không có nền -> AI_AVAILABILITY_INCOMPLETE."""
    client, _ = uf1_api
    # 2A: Có lịch nền 19-22h, ngày 20 bận 20-21h
    payload_with_base = LLMTaskExtractionPayload(
        title="Code backend",
        content="Code API UF1",
        scope="3 endpoint",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="22:00",
                raw_phrase="19h-22h hằng ngày",
            ),
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                start_local="20:00",
                end_local="21:00",
                is_excluded=True,
                raw_phrase="Ngày 20 bận 20h-21h",
            ),
        ],
        is_complete=True,
        summary="Code API 19-22h hằng ngày, trừ 20h-21h ngày 20/11.",
    )
    assert payload_with_base.structured_free_windows[1].op == "exclude"
    assert payload_with_base.structured_free_windows[1].is_ambiguous is False

    # 2B: Chỉ báo "Mai mình chỉ bận 15h-16h" khi chưa có lịch rảnh nền
    payload_busy_only = LLMTaskExtractionPayload(
        title="Code backend",
        content="Code API UF1",
        scope="3 endpoint",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-19",
                start_local="15:00",
                end_local="16:00",
                is_excluded=True,
                raw_phrase="Mai mình chỉ bận 15h-16h",
            )
        ],
        is_complete=True,
        summary="Mai bận 15h-16h.",
    )
    _set_ai_sequence(monkeypatch, [payload_with_base, payload_busy_only])

    d1 = await _create_draft(client, key="ctx02-a")
    d1 = await _analyze(
        client, d1, key="ctx02-a1", message="19h-22h hằng ngày, ngày 20 bận 20h-21h"
    )
    assert d1["summary"]["can_plan"] is True
    o_map = _override_map(d1["summary"]["availability"])
    assert o_map["2026-11-20"] == [("19:00", "20:00"), ("21:00", "22:00")]

    d2 = await _create_draft(client, key="ctx02-b")
    d2 = await _analyze(client, d2, key="ctx02-b1", message="Mai mình chỉ bận 15h-16h")
    assert d2["summary"]["can_plan"] is False
    incomplete = [
        i
        for i in d2["summary"]["clarification_issues"]
        if i["code"] == "AI_AVAILABILITY_INCOMPLETE" and i["blocking"]
    ]
    assert len(incomplete) == 1
    assert "rảnh" in incomplete[0]["question"].lower()


async def test_ctx_02_busy_only_turn1_then_base_turn2(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2 Turn 2: Sau khi chỉ báo giờ bận ở Lượt 1, Lượt 2 cung cấp khung nền phải hợp lệ và trừ đúng giờ bận."""
    client, _ = uf1_api
    t1 = LLMTaskExtractionPayload(
        title="Code backend",
        content="Code API UF1",
        scope="3 endpoint",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-19",
                start_local="15:00",
                end_local="16:00",
                is_excluded=True,
                raw_phrase="Mai mình chỉ bận 15h-16h",
            )
        ],
        is_complete=False,
        summary="Mai bận 15h-16h.",
    )
    t2 = LLMTaskExtractionPayload(
        title="Code backend",
        content="Code API UF1",
        scope="3 endpoint",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        availability_update_mode="merge",
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="14:00",
                end_local="18:00",
                raw_phrase="14h-18h hằng ngày",
            )
        ],
        is_complete=True,
        summary="Rảnh 14h-18h hằng ngày, trừ 15h-16h ngày 19/11.",
    )
    _set_ai_sequence(monkeypatch, [t1, t2])
    d = await _create_draft(client, key="ctx02-t2")
    d = await _analyze(client, d, key="ctx02-t2-1", message="Mai mình chỉ bận 15h-16h")
    assert d["summary"]["can_plan"] is False

    d = await _analyze(client, d, key="ctx02-t2-2", message="Mình rảnh 14h-18h hằng ngày")
    assert d["summary"]["can_plan"] is True
    assert not any(i["blocking"] for i in d["summary"]["clarification_issues"])
    o_map = _override_map(d["summary"]["availability"])
    assert o_map["2026-11-19"] == [("14:00", "15:00"), ("16:00", "18:00")]


async def test_ctx_03_specific_date_overrides_weekly_exclusion(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3: Ngoại lệ ngày cụ thể (2026-11-18 20h-21h) thắng quy tắc loại thứ Tư (weekday=3)."""
    client, _ = uf1_api
    two_week_deadline = datetime(2026, 11, 26, 23, 59, tzinfo=VN_TZ)
    payload = LLMTaskExtractionPayload(
        title="Đọc sách chuyên ngành",
        content="Đọc chương 1-5 sách Kiến trúc hệ thống",
        scope="Ghi chú tóm tắt 5 chương",
        time_constraint=_week_tc(deadline_at=two_week_deadline),
        deadline=two_week_deadline,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="22:00",
                raw_phrase="Tối nào cũng rảnh 19h-22h",
            ),
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=3,
                is_excluded=True,
                raw_phrase="trừ thứ Tư",
            ),
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-18",
                start_local="20:00",
                end_local="21:00",
                raw_phrase="Riêng thứ Tư tuần này (18/11) rảnh 20h-21h",
            ),
        ],
        is_complete=True,
        summary="Rảnh 19-22h trừ thứ 4, riêng thứ 4 18/11 rảnh 20-21h.",
    )
    _set_ai_sequence(monkeypatch, [payload])

    d = await _create_draft(client, key="ctx03")
    d = await _analyze(
        client,
        d,
        key="ctx03-a",
        message="Tối nào cũng rảnh 19h-22h trừ thứ Tư. Riêng thứ Tư tuần này rảnh 20h-21h",
    )
    assert d["summary"]["can_plan"] is True
    w_map = _weekly_map(d["summary"]["availability"])
    assert 3 not in w_map
    assert set(w_map) == {1, 2, 4, 5, 6, 7}
    o_map = _override_map(d["summary"]["availability"])
    assert o_map["2026-11-18"] == [("20:00", "21:00")]
    assert o_map["2026-11-25"] == []  # Thứ Tư tuần sau vẫn đóng


async def test_ctx_04_reset_override_vs_full_day_exclude(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4: op='reset_override' xóa key date_overrides để quay về weekly_rules; op='exclude' cả ngày giữ intervals=[]."""
    client, _ = uf1_api
    # Lượt 1: Weekly rule 19-22h + đóng ngày 20 (exclude cả ngày)
    t1 = LLMTaskExtractionPayload(
        title="Làm slide",
        content="Thiết kế slide thuyết trình",
        scope="12 slide",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="22:00",
                raw_phrase="19h-22h hằng ngày",
            ),
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                is_excluded=True,
                raw_phrase="Ngày 20 bận cả ngày",
            ),
        ],
        is_complete=True,
        summary="Rảnh 19-22h trừ ngày 20/11 đóng cả ngày.",
    )
    # Lượt 2: Reset override ngày 20 để quay về lịch tuần
    t2 = LLMTaskExtractionPayload(
        title="Làm slide",
        content="Thiết kế slide thuyết trình",
        scope="12 slide",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                op="reset_override",
                raw_phrase="Ngày 20 rảnh lại rồi, bỏ ngoại lệ hôm đó đi",
            )
        ],
        is_complete=True,
        summary="Ngày 20/11 quay lại lịch tuần 19-22h.",
    )
    _set_ai_sequence(monkeypatch, [t1, t2])

    d = await _create_draft(client, key="ctx04")
    d = await _analyze(client, d, key="ctx04-1", message="19-22h hằng ngày, ngày 20 bận cả ngày")
    assert _override_map(d["summary"]["availability"])["2026-11-20"] == []

    # Kiểm tra tiếp với nền là weekly_rules (không phải daily pre-populated dates)
    t3_weekly_base = LLMTaskExtractionPayload(
        title="Làm slide",
        content="Thiết kế slide thuyết trình",
        scope="12 slide",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        availability_update_mode="replace",
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=5,
                start_local="19:00",
                end_local="22:00",
                raw_phrase="Thứ Sáu 19h-22h",
            ),
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                start_local="20:00",
                end_local="21:00",
                op="replace_day",
                raw_phrase="Riêng 20/11 chỉ 20h-21h",
            ),
        ],
        is_complete=True,
        summary="Thứ 6 19-22h, riêng 20/11 20-21h.",
    )
    t4_reset = LLMTaskExtractionPayload(
        title="Làm slide",
        content="Thiết kế slide thuyết trình",
        scope="12 slide",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                op="reset_override",
                raw_phrase="Bỏ ngoại lệ 20/11",
            )
        ],
        is_complete=True,
        summary="Đã xóa ngoại lệ ngày 20/11.",
    )
    _set_ai_sequence(monkeypatch, [t2, t3_weekly_base, t4_reset])

    d = await _analyze(client, d, key="ctx04-2", message="Ngày 20 rảnh lại rồi, bỏ ngoại lệ")
    # Khi nền có daily pre-populated, reset_override bỏ key 2026-11-20 khỏi date_overrides -> kế thừa weekly_rule
    assert "2026-11-20" not in _override_map(d["summary"]["availability"])
    assert _weekly_map(d["summary"]["availability"])[5] == [("19:00", "22:00")]

    d = await _analyze(client, d, key="ctx04-3", message="Thứ Sáu 19-22h, riêng 20/11 20-21h")
    assert _override_map(d["summary"]["availability"])["2026-11-20"] == [("20:00", "21:00")]

    d = await _analyze(client, d, key="ctx04-4", message="Bỏ ngoại lệ ngày 20/11")
    assert "2026-11-20" not in _override_map(d["summary"]["availability"])
    assert _weekly_map(d["summary"]["availability"])[5] == [("19:00", "22:00")]


async def test_ctx_05_disjoint_intervals_same_day(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#5: Hai khung rời trong 1 ngày (09-11h và 19-21h) giữ tách biệt khi qua compute_effective_free_intervals."""
    client, _ = uf1_api
    payload = LLMTaskExtractionPayload(
        title="Luyện đề",
        content="Giải đề luyện thi",
        scope="2 đề mẫu",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=1,
                start_local="09:00",
                end_local="11:00",
                raw_phrase="Thứ Hai 09:00-11:00",
            ),
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=1,
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Thứ Hai 19:00-21:00",
            ),
        ],
        is_complete=True,
        summary="Giải 2 đề vào thứ Hai sáng và tối.",
    )
    _set_ai_sequence(monkeypatch, [payload])

    d = await _create_draft(client, key="ctx05")
    d = await _analyze(
        client, d, key="ctx05-a", message="Thứ Hai mình rảnh 9h-11h và 19h-21h"
    )
    assert _weekly_map(d["summary"]["availability"])[1] == [
        ("09:00", "11:00"),
        ("19:00", "21:00"),
    ]
    draft_avail = DraftAvailability.model_validate(d["summary"]["availability"])
    effective = compute_effective_free_intervals(
        draft_avail,
        window_start=datetime(2026, 11, 16, 0, 0, tzinfo=VN_TZ),
        window_end=datetime(2026, 11, 16, 23, 59, tzinfo=VN_TZ),
    )
    assert len(effective) == 2
    assert (
        effective[0].start_at.astimezone(VN_TZ).time(),
        effective[0].end_at.astimezone(VN_TZ).time(),
    ) == (time(9, 0), time(11, 0))
    assert (
        effective[1].start_at.astimezone(VN_TZ).time(),
        effective[1].end_at.astimezone(VN_TZ).time(),
    ) == (time(19, 0), time(21, 0))


async def test_ctx_06_relative_calendar_anchor_and_same_time_inheritance(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#6: Context tiêm relative_calendar_anchor 14 ngày; 'mốt cũng giờ đó' kế thừa 19:00-21:00; prompt có luật tự đính chính."""
    assert "tự đính chính" in UF1_EXTRACTION_SYSTEM_PROMPT.lower()
    assert "à nhầm" in UF1_EXTRACTION_SYSTEM_PROMPT.lower()

    # Tầng AIService: kiểm tra payload gửi xuống LLM & kế thừa 'cũng giờ đó'
    svc = _mock_ai_service()
    captured_svc: list[dict[str, Any]] = []
    ai_payload = LLMTaskExtractionPayload(
        title="Viết tiểu luận",
        content="Viết tiểu luận triết học",
        scope="1500 chữ",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-19",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Tối mai 19h-21h",
            ),
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                start_local=None,
                end_local=None,
                raw_phrase="mốt cũng giờ đó",
            ),
        ],
        is_complete=True,
        summary="Tiểu luận 1500 chữ, làm tối mai và mốt 19-21h.",
    )
    assert ai_payload.structured_free_windows[1].start_local == "19:00"
    assert ai_payload.structured_free_windows[1].end_local == "21:00"
    assert ai_payload.structured_free_windows[1].is_ambiguous is False

    async def _fake_svc(**kwargs: Any) -> tuple[LLMTaskExtractionPayload, str]:
        captured_svc.append(json.loads(kwargs["user_prompt"]))
        return ai_payload, "tier_1_gemini"

    with monkeypatch.context() as mp:
        mp.setattr(svc, "_execute_hedged_structured", _fake_svc)
        res = await svc.extract_task_intent(
            "Viết tiểu luận 1500 chữ, tối mai 19h-21h, mốt cũng giờ đó",
            reference_now=FROZEN_NOW,
        )
    assert res.is_complete is True
    anchor = captured_svc[0]["relative_calendar_anchor"]
    assert anchor["today"] == "2026-11-18"
    assert anchor["tomorrow_mai"] == "2026-11-19"
    assert anchor["day_after_tomorrow_mot"] == "2026-11-20"
    assert len(anchor["upcoming_14_days"]) == 14

    # Tầng DraftService: mô phỏng trường hợp LLM chưa chạy validator kế thừa qua raw dict hoặc merge 2 lượt
    client, _ = uf1_api
    captured_api: list[dict[str, Any]] = []
    _set_ai_sequence(monkeypatch, [ai_payload], captured_prompts=captured_api)
    d = await _create_draft(client, key="ctx06")
    d = await _analyze(
        client, d, key="ctx06-a", message="Tối mai 19h-21h, mốt cũng giờ đó"
    )
    assert d["summary"]["can_plan"] is True
    assert "relative_calendar_anchor" in captured_api[0]
    o_map = _override_map(d["summary"]["availability"])
    assert o_map["2026-11-19"] == [("19:00", "21:00")]
    assert o_map["2026-11-20"] == [("19:00", "21:00")]


async def test_ctx_07_weekday_date_mismatch_clarification_and_turn2(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#7: Thứ Sáu ngày 21/11/2026 (21/11 là Thứ Bảy) -> giữ 19-21h, is_ambiguous=True, hỏi 20/11 vs 21/11; Lượt 2 sửa -> can_plan=True."""
    w_conflict = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-21",
        weekday=5,
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ Sáu ngày 21/11/2026, từ 19h đến 21h",
    )
    assert w_conflict.start_local == "19:00"
    assert w_conflict.end_local == "21:00"
    assert w_conflict.is_ambiguous is True
    assert w_conflict.clarification_hint is not None
    assert "2026-11-20" in w_conflict.clarification_hint
    assert "2026-11-21" in w_conflict.clarification_hint

    # Ngay cả khi LLM không điền weekday=5, validator tự trích từ raw_phrase
    w_conflict_auto = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-21",
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ Sáu ngày 21/11/2026, từ 19h đến 21h",
    )
    assert w_conflict_auto.is_ambiguous is True

    client, _ = uf1_api
    t1 = LLMTaskExtractionPayload(
        title="Họp chuẩn bị demo",
        content="Chuẩn bị demo sản phẩm",
        scope="Kịch bản demo 10 phút",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[w_conflict],
        is_complete=False,
        summary="Chuẩn bị demo.",
        clarification_questions=[w_conflict.clarification_hint],
    )
    t2 = LLMTaskExtractionPayload(
        title="Họp chuẩn bị demo",
        content="Chuẩn bị demo sản phẩm",
        scope="Kịch bản demo 10 phút",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Thứ Sáu 20/11/2026 19h-21h",
            )
        ],
        is_complete=True,
        summary="Đã chốt Thứ Sáu 20/11/2026 19:00-21:00.",
    )
    _set_ai_sequence(monkeypatch, [t1, t2])

    d = await _create_draft(client, key="ctx07")
    d = await _analyze(
        client,
        d,
        key="ctx07-1",
        message="Thứ Sáu ngày 21/11/2026, từ 19h đến 21h",
    )
    assert d["summary"]["can_plan"] is False
    avail_issues = [
        i
        for i in d["summary"]["clarification_issues"]
        if i["code"] == "AI_AVAILABILITY_INCOMPLETE"
    ]
    assert len(avail_issues) == 1
    assert "2026-11-20" in avail_issues[0]["question"]
    assert "2026-11-21" in avail_issues[0]["question"]

    d = await _analyze(client, d, key="ctx07-2", message="Ý mình là thứ Sáu 20/11/2026")
    assert d["summary"]["can_plan"] is True
    assert _override_map(d["summary"]["availability"]) == {"2026-11-20": [("19:00", "21:00")]}


async def test_ctx_08_duration_minutes_and_open_ended_time(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#8: 'Từ 19h30, rảnh 90 phút' -> 19:30-21:00; 'Tối nay rảnh sau 19h30' -> giữ 19:30, hỏi giờ kết thúc, Lượt 2 ghép."""
    w_dur = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-18",
        start_local="19:30",
        duration_minutes=90,
        raw_phrase="Từ 19h30, rảnh 90 phút",
    )
    assert w_dur.end_local == "21:00"
    assert w_dur.is_ambiguous is False

    w_open = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-18",
        start_local="19:30",
        end_local=None,
        raw_phrase="Tối nay rảnh sau 19h30",
    )
    assert w_open.start_local == "19:30"
    assert w_open.end_local is None
    assert w_open.is_ambiguous is True
    assert w_open.clarification_hint is not None

    client, _ = uf1_api
    t1 = LLMTaskExtractionPayload(
        title="Review PR",
        content="Review code nhóm",
        scope="2 PR backend",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[w_open],
        is_complete=False,
        summary="Review 2 PR tối nay sau 19h30.",
        clarification_questions=[w_open.clarification_hint],
    )
    t2 = LLMTaskExtractionPayload(
        title="Review PR",
        content="Review code nhóm",
        scope="2 PR backend",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-18",
                start_local=None,
                end_local="21:30",
                raw_phrase="đến 21h30",
            )
        ],
        is_complete=True,
        summary="Review 2 PR tối nay 19:30-21:30.",
    )
    _set_ai_sequence(monkeypatch, [t1, t2])

    d = await _create_draft(client, key="ctx08")
    d = await _analyze(client, d, key="ctx08-1", message="Tối nay rảnh sau 19h30")
    assert d["summary"]["can_plan"] is False
    assert any(
        i["code"] == "AI_AVAILABILITY_INCOMPLETE" and "19:30" in i["question"]
        for i in d["summary"]["clarification_issues"]
    )

    d = await _analyze(client, d, key="ctx08-2", message="Làm đến 21h30 nhé")
    assert d["summary"]["can_plan"] is True
    assert _override_map(d["summary"]["availability"]) == {"2026-11-18": [("19:30", "21:30")]}


async def test_ctx_09_all_day_midnight_and_overnight(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#9: 'Mai rảnh cả ngày' -> is_ambiguous=True; '20h đến 24h' / '12h đêm' -> 20:00-23:59; qua đêm 22:00-01:00 tách 2 ngày."""
    w_allday_null = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-19",
        raw_phrase="Mai rảnh cả ngày",
    )
    assert w_allday_null.is_ambiguous is True
    assert w_allday_null.clarification_hint is not None

    w_allday_full = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-19",
        start_local="00:00",
        end_local="23:59",
        raw_phrase="Mai rảnh cả ngày",
    )
    assert w_allday_full.is_ambiguous is True

    w_24 = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-19",
        start_local="20:00",
        end_local="24:00",
        raw_phrase="20h đến 24h",
    )
    assert w_24.end_local == "23:59"
    assert w_24.is_ambiguous is False

    w_12dem = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-19",
        start_local="20:00",
        end_local="00:00",
        raw_phrase="20h đến 12h đêm",
    )
    assert w_12dem.end_local == "23:59"
    assert w_12dem.is_ambiguous is False

    w_overnight = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekday=5,
        start_local="22:00",
        end_local="01:00",
        raw_phrase="Thứ Sáu 22h đến thứ Bảy 1h sáng",
    )
    assert w_overnight.is_ambiguous is False

    client, _ = uf1_api
    payload = LLMTaskExtractionPayload(
        title="Deploy bản thử nghiệm",
        content="Triển khai hệ thống cuối tuần",
        scope="Checklist 5 bước deploy",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[w_overnight],
        is_complete=True,
        summary="Deploy từ 22h thứ Sáu đến 1h sáng thứ Bảy.",
    )
    payload_date_overnight = LLMTaskExtractionPayload(
        title="Deploy bản thử nghiệm",
        content="Triển khai hệ thống cuối tuần",
        scope="Checklist 5 bước deploy",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        availability_update_mode="replace",
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                start_local="22:00",
                end_local="01:00",
                raw_phrase="20/11 22h đến 21/11 1h sáng",
            )
        ],
        is_complete=True,
        summary="Deploy đêm 20/11 sang 21/11.",
    )
    _set_ai_sequence(monkeypatch, [payload, payload_date_overnight])
    d = await _create_draft(client, key="ctx09")
    d = await _analyze(
        client, d, key="ctx09-a", message="Thứ Sáu 22h đến thứ Bảy 1h sáng"
    )
    assert d["summary"]["can_plan"] is True
    w_map = _weekly_map(d["summary"]["availability"])
    assert w_map[5] == [("22:00", "23:59")]
    assert w_map[6] == [("00:00", "01:00")]

    d = await _analyze(client, d, key="ctx09-b", message="20/11 22h đến 21/11 1h sáng")
    assert d["summary"]["can_plan"] is True
    assert _override_map(d["summary"]["availability"]) == {
        "2026-11-20": [("22:00", "23:59")],
        "2026-11-21": [("00:00", "01:00")],
    }


async def test_ctx_10_11_extra_requirements_not_blocked_by_availability(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#10 & #11: Ràng buộc thực thi và ưu tiên lưu vào extra_requirements, không bật AI_AVAILABILITY_INCOMPLETE oan."""
    client, _ = uf1_api
    reqs = [
        "Mỗi ngày học tối đa 45 phút",
        "Chỉ xếp khi có ít nhất 45 phút trống liền",
        "Ưu tiên sau 20h",
        "Thứ Bảy hoặc Chủ nhật, chọn một buổi thôi",
        "Thiếu giờ các tối trong tuần thì mới dùng cuối tuần",
    ]
    payload = LLMTaskExtractionPayload(
        title="Học thuật toán",
        content="Ôn quy hoạch động",
        scope="6 bài cơ bản",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="22:00",
                raw_phrase="19h-22h hằng ngày",
            )
        ],
        extra_requirements=reqs,
        is_complete=True,
        summary="Ôn quy hoạch động theo các ràng buộc ưu tiên.",
    )
    _set_ai_sequence(monkeypatch, [payload])
    d = await _create_draft(client, key="ctx10-11")
    d = await _analyze(
        client,
        d,
        key="ctx10-11-a",
        message="Rảnh 19h-22h hằng ngày; " + "; ".join(reqs),
    )
    assert d["summary"]["can_plan"] is True
    assert d["summary"]["extra_requirements"] == reqs
    assert not any(
        i["code"] == "AI_AVAILABILITY_INCOMPLETE"
        for i in d["summary"]["clarification_issues"]
    )


async def test_ctx_12_event_relative_availability(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#12: 'Sau cuộc họp nhóm 30 phút thì mình rảnh hai tiếng' -> is_ambiguous=True, duration_minutes=120, hỏi giờ kết thúc họp."""
    w_event = ExtractedFreeWindowSchema(
        kind="specific_date",
        date=None,
        start_local=None,
        end_local=None,
        raw_phrase="Sau cuộc họp nhóm 30 phút thì mình rảnh hai tiếng",
    )
    assert w_event.is_ambiguous is True
    assert w_event.duration_minutes == 120
    assert w_event.clarification_hint is not None
    assert "kết thúc" in w_event.clarification_hint.lower()

    client, _ = uf1_api
    payload = LLMTaskExtractionPayload(
        title="Tổng hợp biên bản",
        content="Viết biên bản họp nhóm",
        scope="1 trang tóm tắt action items",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[w_event],
        is_complete=False,
        summary="Viết biên bản sau cuộc họp nhóm.",
        clarification_questions=[w_event.clarification_hint],
    )
    _set_ai_sequence(monkeypatch, [payload])
    d = await _create_draft(client, key="ctx12")
    d = await _analyze(
        client,
        d,
        key="ctx12-a",
        message="Sau cuộc họp nhóm 30 phút thì mình rảnh hai tiếng",
    )
    assert d["summary"]["can_plan"] is False
    assert any(
        i["code"] == "AI_AVAILABILITY_INCOMPLETE" and i["blocking"]
        for i in d["summary"]["clarification_issues"]
    )


# =============================================================================
# 3. HAI CA KIỂM THỬ TÍCH HỢP NGHIỆP VỤ (IT-1, IT-2)
# =============================================================================


async def test_it_1_google_busy_with_capped_window(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-1: 'Lịch Google trống thì học, nhưng chỉ trong 19h-22h' -> trừ busy_intervals 19:30-20:30 chỉ còn [19:00-19:30, 20:30-22:00]."""
    client, _ = uf1_api
    payload = LLMTaskExtractionPayload(
        title="Học Machine Learning",
        content="Xem bài giảng và làm quiz tuần 4",
        scope="2 bài giảng + 1 quiz",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="22:00",
                raw_phrase="Lịch Google trống thì học, nhưng chỉ trong 19h-22h",
            )
        ],
        is_complete=True,
        summary="Học ML trong khung 19h-22h khi lịch Google trống.",
    )
    _set_ai_sequence(monkeypatch, [payload])

    d = await _create_draft(client, key="it1")
    d = await _analyze(
        client,
        d,
        key="it1-a",
        message="Lịch Google trống thì học, nhưng chỉ trong 19h-22h",
    )
    assert d["summary"]["can_plan"] is True
    draft_avail = DraftAvailability.model_validate(d["summary"]["availability"])

    cal_ctx = CalendarContext(
        context_id="ctx_google_1",
        revision=1,
        query_start_end=TimeInterval(
            start_at=datetime(2026, 11, 19, 0, 0, tzinfo=VN_TZ).astimezone(UTC),
            end_at=datetime(2026, 11, 19, 23, 59, tzinfo=VN_TZ).astimezone(UTC),
        ),
        busy_intervals=[
            BusyInterval(
                start_at=datetime(2026, 11, 19, 19, 30, tzinfo=VN_TZ).astimezone(UTC),
                end_at=datetime(2026, 11, 19, 20, 30, tzinfo=VN_TZ).astimezone(UTC),
                source=BusyIntervalSource(
                    kind="calendar_event",
                    connection_id="conn_1",
                    google_calendar_id="primary",
                    google_event_id="evt_1",
                ),
            )
        ],
        fetched_at=FROZEN_NOW.astimezone(UTC),
        sync_state="fresh",
        coverage_state="complete",
    )
    effective = compute_effective_free_intervals(
        draft_avail,
        window_start=datetime(2026, 11, 19, 0, 0, tzinfo=VN_TZ),
        window_end=datetime(2026, 11, 19, 23, 59, tzinfo=VN_TZ),
        calendar_context=cal_ctx,
    )
    assert [
        (
            iv.start_at.astimezone(VN_TZ).strftime("%H:%M"),
            iv.end_at.astimezone(VN_TZ).strftime("%H:%M"),
        )
        for iv in effective
    ] == [("19:00", "19:30"), ("20:30", "22:00")]


async def test_it_2_task_specific_availability_preserves_default_availability(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-2: 'Giờ này chỉ dùng cho báo cáo, lịch mặc định giữ nguyên' -> lưu draft.availability, availabilities bảng gốc không đổi."""
    client, factory = uf1_api
    await client.get("/api/v1/preferences")
    async with factory() as db:
        db.add(
            SettingsAvailabilityRow(
                user_id=resolve_user_uuid("user_a"),
                source="user_confirmed",
                confirmed_at=FROZEN_NOW.astimezone(UTC),
                timezone="Asia/Ho_Chi_Minh",
                weekly_rules=[
                    {"weekday": w, "start_local": "08:00", "end_local": "10:00"}
                    for w in range(1, 8)
                ],
                date_overrides=[],
                task_limits=[],
            )
        )
        await db.commit()

    before_global = (await client.get("/api/v1/availability")).json()["data"]
    payload = LLMTaskExtractionPayload(
        title="Báo cáo chuyên đề",
        content="Viết báo cáo chuyên đề",
        scope="6 trang",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="19h-21h chỉ dùng cho báo cáo",
            )
        ],
        is_complete=True,
        summary="Báo cáo dùng giờ riêng 19h-21h, giữ nguyên lịch mặc định.",
    )
    _set_ai_sequence(monkeypatch, [payload])

    d = await _create_draft(client, key="it2-report")
    d = await _analyze(
        client,
        d,
        key="it2-report-a",
        message="Giờ 19h-21h này chỉ dùng cho báo cáo, lịch mặc định giữ nguyên",
    )
    assert d["summary"]["can_plan"] is True
    assert d["summary"]["availability_ref"] is None
    assert _weekly_map(d["summary"]["availability"])[1] == [("19:00", "21:00")]

    # Kiểm tra bảng availabilities (lịch mặc định user) vẫn giữ nguyên 08:00-10:00
    after_global = (await client.get("/api/v1/availability")).json()["data"]
    assert after_global == before_global
    async with factory() as db:
        row = await db.scalar(
            select(SettingsAvailabilityRow).where(
                SettingsAvailabilityRow.user_id == resolve_user_uuid("user_a")
            )
        )
        assert row is not None
        assert row.weekly_rules[0]["start_local"] == "08:00"
        assert row.weekly_rules[0]["end_local"] == "10:00"

    # Tạo draft khác dùng availability_ref mặc định vẫn nhận 08:00-10:00
    other_draft = await _create_draft(
        client,
        key="it2-other",
        raw_input="Việc khác dùng lịch mặc định",
        summary={
            "title": "Việc khác",
            "description": "Việc khác dùng lịch mặc định",
            "scope": "2 trang",
            "time_constraint": {
                "kind": "deadline",
                "start_at": WEEK_START.isoformat(),
                "deadline_at": WEEK_DEADLINE.isoformat(),
                "input_timezone": "Asia/Ho_Chi_Minh",
            },
            "availability_ref": {
                "availability_id": after_global["availability_id"],
                "revision": after_global["revision"],
            },
        },
    )
    assert other_draft["summary"]["can_plan"] is True
    assert other_draft["summary"]["availability"] is None
    assert other_draft["summary"]["availability_ref"]["availability_id"] == after_global["availability_id"]


# =============================================================================
# 4. BỘ KIỂM THỬ HỒI QUY CHO CÁC BẢN VÁ LIVE AI UF1 (1A, 1B, 2A, 2B)
# =============================================================================


def test_patch_2a_auto_correct_vietnamese_word_weekday_preserves_numeric_fixture() -> None:
    """2A: Tự động sửa lệch Thứ chữ tiếng Việt ('Thứ Sáu' + weekday=6 -> 5) nhưng giữ nguyên 'Thứ 2'."""
    corrected = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekday=6,
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ Sáu rảnh 19h đến 21h",
    )
    assert corrected.weekday == 5
    assert corrected.is_ambiguous is False

    corrected_list = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekdays=[6],
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ Sáu rảnh 19h đến 21h",
    )
    assert corrected_list.weekdays == [5]

    numeric_preserved = ExtractedFreeWindowSchema(
        kind="weekly_rule",
        weekday=2,
        start_local="00:00",
        end_local="23:59",
        raw_phrase="Thứ 2 từ 00:00 đến 23:59",
    )
    assert numeric_preserved.weekday == 2
    assert numeric_preserved.is_ambiguous is False


async def test_patch_2b_deduplicates_clarification_issues_by_field_path(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2B: Khử trùng lặp clarification_issues theo field_path trong analyze_draft (không lặp 6-9 issues)."""
    client, _ = uf1_api
    from app.schemas.common import ClarificationIssue

    noisy_payload = LLMTaskExtractionPayload(
        title="Ôn thi Hệ điều hành",
        content="Ôn thi môn Hệ điều hành",
        scope=None,
        time_constraint=None,
        deadline=None,
        free_time_slots=["tối hằng ngày"],
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local=None,
                end_local=None,
                raw_phrase="tối hằng ngày",
                clarification_hint="Khung giờ 'tối hằng ngày' bạn rảnh từ mấy giờ đến mấy giờ?",
            )
        ],
        clarification_issues=[
            ClarificationIssue(
                code="AI_MISSING_SCOPE",
                field_path="scope",
                message="Thiếu scope 1",
                question="Bạn cần ôn mấy chương hoặc mấy đề?",
                blocking=True,
            ),
            ClarificationIssue(
                code="AI_SCOPE_REQUIRED",
                field_path="scope",
                message="Thiếu scope 2",
                question="Phạm vi ôn tập cụ thể là gì?",
                blocking=True,
            ),
            ClarificationIssue(
                code="AI_AMBIGUOUS_FREE_TIME",
                field_path="availability",
                message="Mơ hồ giờ rảnh",
                question="Tối hằng ngày bạn rảnh từ mấy giờ đến mấy giờ?",
                blocking=True,
            ),
            ClarificationIssue(
                code="AI_MISSING_DEADLINE",
                field_path="time_constraint",
                message="Thiếu hạn chót",
                question="Hạn chót hoàn thành là khi nào?",
                blocking=True,
            ),
        ],
        clarification_questions=[
            "Bạn cần ôn mấy chương hoặc mấy đề?",
            "Khung giờ 'tối hằng ngày' bạn rảnh từ mấy giờ đến mấy giờ?",
            "Hạn chót hoàn thành là khi nào?",
            "Phạm vi hoặc kết quả đầu ra mong muốn của việc này là gì (ví dụ: số trang, số bài, mức độ hoàn thiện)?",
        ],
        is_complete=False,
        summary="Ôn thi Hệ điều hành, thiếu phạm vi, giờ cụ thể và hạn chót.",
    )
    _set_ai_sequence(monkeypatch, [noisy_payload])

    draft = await _create_draft(client, key="patch2b-dedup")
    analyzed = await _analyze(
        client,
        draft,
        key="patch2b-dedup-a",
        message="Ôn thi môn Hệ điều hành, tối hằng ngày rảnh.",
    )
    assert analyzed["summary"]["can_plan"] is False
    issues = analyzed["summary"]["clarification_issues"]
    field_paths = [i["field_path"] for i in issues]
    assert field_paths.count("availability") == 1
    assert field_paths.count("scope") == 1
    assert field_paths.count("time_constraint") == 1
    assert not any(i["code"] == "AI_CLARIFICATION" for i in issues)
    assert len(issues) == 3


def test_patch_1a_uf1_prompt_schema_anchor_budget_guard() -> None:
    """1A: Tổng kích thước UF1 prompt + compact JSON schema + compact anchor phải < 8,000 ký tự và đủ field."""
    assert "tự đính chính" in UF1_EXTRACTION_SYSTEM_PROMPT.lower()
    assert "à nhầm" in UF1_EXTRACTION_SYSTEM_PROMPT.lower()
    schema_dict = LLMTaskExtractionPayload.model_json_schema()
    assert set(schema_dict["properties"].keys()) == set(LLMTaskExtractionPayload.model_fields.keys())
    assert "description" in schema_dict["properties"]["title"]
    compact_schema = json.dumps(
        schema_dict,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    compact_anchor = json.dumps(
        AIService._build_relative_calendar_anchor(FROZEN_NOW),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    total_chars = len(UF1_EXTRACTION_SYSTEM_PROMPT) + len(compact_schema) + len(compact_anchor)
    assert total_chars < 8000, f"Budget exceeded: {total_chars}"


async def test_patch_1b_groq_uf1_rate_limit_falls_back_to_secondary_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1B: _call_groq_structured với allow_secondary=False (UF1) gặp 429 rate_limit phải tự cứu hộ sang secondary."""
    svc = AIService(
        app_settings=Settings(
            _env_file=None,
            GEMINI_API_KEY="",
            GROQ_API_KEY="gsk_MockTestKey",
            GOOGLE_CREDENTIALS_PATH="",
            GROQ_FALLBACK_MODEL="qwen/qwen3.8-27b",
            GROQ_SECONDARY_FALLBACK_MODEL="openai/gpt-oss-20b",
        )
    )
    called_models: list[str] = []

    class FakeRateLimitError(Exception):
        status_code = 429

    class _Msg:
        content = json.dumps(
            {
                "title": "Làm báo cáo",
                "content": "Làm báo cáo thực tập",
                "scope": "10 trang Word",
                "structured_free_windows": [],
                "summary": "Làm báo cáo thực tập 10 trang Word.",
            },
            ensure_ascii=False,
        )

    class _Choice:
        message = _Msg()

    class _Comp:
        choices = [_Choice()]

    class _FakeCompletions:
        async def create(self, *, model: str, **_: Any) -> _Comp:
            called_models.append(model)
            if model == "qwen/qwen3.8-27b":
                raise FakeRateLimitError("429 Rate limit reached for qwen/qwen3.8-27b")
            return _Comp()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeGroqClient:
        chat = _FakeChat()

    monkeypatch.setattr(svc, "_get_groq_client", lambda: _FakeGroqClient())
    res = await svc._call_groq_structured(
        system_prompt=UF1_EXTRACTION_SYSTEM_PROMPT,
        user_prompt='{"user_raw_input":"Làm báo cáo 10 trang"}',
        schema_cls=LLMTaskExtractionPayload,
        allow_secondary=False,
    )
    assert called_models == ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
    assert res.scope == "10 trang Word"


# =============================================================================
# 5. BỘ KIỂM THỬ HỒI QUY ĐỢT V3 (NHÓM A: UF1 RISKS + NHÓM B: 4 LIVE AI FIXES)
# =============================================================================


def test_v3_risk_uf1_02_date_range_180_days_cap_and_daily_60_days_cap() -> None:
    """Nhóm A1 (RISK-UF1-02): _item_dates cho phép date_range tối đa 180 ngày; _effective_time_dates giữ trần 60 ngày."""
    w_180 = ExtractedFreeWindowSchema(
        kind="date_range",
        date_from="2026-11-19",
        date_to="2027-06-30",
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Từ 19/11/2026 đến 30/06/2027 rảnh 19h-21h",
    )
    dates_180 = draft_service_module._item_dates(w_180)
    assert len(dates_180) == 180
    assert dates_180[0].isoformat() == "2026-11-19"
    assert dates_180[-1].isoformat() == "2027-05-17"

    tc_long = draft_service_module.TimeConstraint(
        kind="deadline",
        start_at=datetime(2026, 11, 19, 8, 0, tzinfo=VN_TZ),
        deadline_at=datetime(2027, 6, 30, 22, 0, tzinfo=VN_TZ),
        input_timezone="Asia/Ho_Chi_Minh",
    )
    daily_dates = draft_service_module._effective_time_dates(tc_long, "Asia/Ho_Chi_Minh")
    assert len(daily_dates) == 60


async def test_v3_risk_uf1_03_locked_fields_preserved_unless_explicitly_edited(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nhóm A2 (RISK-UF1-03): Trường đã user_confirmed không bị AI ghi đè hay bật review oan khi chỉ bổ sung trường khác."""
    client, _ = uf1_api
    captured_prompts: list[dict[str, Any]] = []

    draft = await _create_draft(
        client,
        key="v3-locked",
        raw_input="Làm báo cáo thực tập, hạn 25/11/2026 22:00",
        summary={
            "title": "Báo cáo thực tập (Tên đã chốt)",
            "description": "Viết báo cáo thực tập tốt nghiệp",
            "scope": "10 trang Word",
            "time_constraint": {
                "kind": "deadline",
                "start_at": WEEK_START.isoformat(),
                "deadline_at": WEEK_DEADLINE.isoformat(),
                "input_timezone": "Asia/Ho_Chi_Minh",
            },
        },
    )
    # Turn 1: User chỉ bổ sung giờ rảnh, nhưng AI đề xuất lệch title/scope/deadline
    t1_payload = LLMTaskExtractionPayload(
        title="Tên khác do AI tự đổi",
        content="Viết báo cáo thực tập tốt nghiệp",
        scope="15 trang Word",
        time_constraint=_week_tc(deadline_at=datetime(2026, 11, 23, 22, 0, tzinfo=VN_TZ)),
        deadline=datetime(2026, 11, 23, 22, 0, tzinfo=VN_TZ),
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Rảnh 19h đến 21h hằng ngày",
            )
        ],
        is_complete=True,
        summary="Bổ sung giờ rảnh.",
    )
    # Turn 2: User chủ động yêu cầu đổi phạm vi -> phải giữ bảo vệ & hỏi review
    t2_payload = LLMTaskExtractionPayload(
        title="Báo cáo thực tập (Tên đã chốt)",
        content="Viết báo cáo thực tập tốt nghiệp",
        scope="12 trang Word",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Rảnh 19h đến 21h hằng ngày",
            )
        ],
        is_complete=True,
        summary="Đổi phạm vi sang 12 trang.",
    )
    _set_ai_sequence(monkeypatch, [t1_payload, t2_payload], captured_prompts=captured_prompts)

    d1 = await _analyze(
        client,
        draft,
        key="v3-locked-t1",
        message="Mỗi ngày mình rảnh 19h đến 21h.",
    )
    assert set(captured_prompts[0]["locked_fields"]) >= {"title", "scope", "time_constraint"}
    assert d1["summary"]["can_plan"] is True
    assert d1["summary"]["title"] == "Báo cáo thực tập (Tên đã chốt)"
    assert d1["summary"]["scope"] == "10 trang Word"
    assert not any(
        i["code"] in {"AI_FIELD_REVIEW_REQUIRED", "AI_TIME_REVIEW_REQUIRED"}
        for i in d1["summary"]["clarification_issues"]
    )

    d2 = await _analyze(
        client,
        d1,
        key="v3-locked-t2",
        message="Đổi phạm vi sang 12 trang nhé.",
    )
    assert any(
        i["code"] == "AI_FIELD_REVIEW_REQUIRED" and i["field_path"] == "scope"
        for i in d2["summary"]["clarification_issues"]
    )


def test_v3_risk_uf1_04_2359_treated_as_full_end_of_day() -> None:
    """Nhóm A3 (RISK-UF1-04): Khung 20:00-23:59 tính đủ 14,400 giây (4 giờ) đến 00:00 ngày kế tiếp."""
    avail = DraftAvailability.model_validate(
        {
            "timezone": "Asia/Ho_Chi_Minh",
            "weekly_rules": [],
            "date_overrides": [
                {
                    "date": "2026-11-20",
                    "intervals": [{"start_local": "20:00", "end_local": "23:59"}],
                }
            ],
        }
    )
    intervals = compute_effective_free_intervals(
        avail,
        window_start=datetime(2026, 11, 20, 0, 0, tzinfo=VN_TZ),
        window_end=datetime(2026, 11, 21, 0, 0, tzinfo=VN_TZ),
    )
    assert len(intervals) == 1
    assert int((intervals[0].end_at - intervals[0].start_at).total_seconds()) == 14400


def test_v3_tier3_safe_lightweight_regex_fallback() -> None:
    """Nhóm A4: Tier 3 fallback vẫn giữ is_complete=False nhưng trích được lịch tường minh, deadline & extra_requirements."""
    svc = _mock_ai_service()
    res = svc._fallback_extract_task(
        "Ôn 6 bài quy hoạch động, hạn chót 26/11/2026 lúc 22:00, rảnh 19:00 đến 21:00 hằng ngày. "
        "Lưu ý mỗi ngày học tối đa 45 phút, ít nhất 45 phút trống liền, ưu tiên sau 20h.",
        reference_now=FROZEN_NOW,
    )
    assert res.generation_tier == "tier_3_rule_based"
    assert res.is_complete is False
    assert len(res.structured_free_windows) == 1
    assert res.structured_free_windows[0].kind == "daily"
    assert res.structured_free_windows[0].start_local == "19:00"
    assert res.structured_free_windows[0].end_local == "21:00"
    assert res.deadline == datetime(2026, 11, 26, 22, 0, tzinfo=VN_TZ)
    joined_extra = " | ".join(res.extra_requirements).lower()
    assert "45 phút" in joined_extra
    assert "20h" in joined_extra


def test_v3_patch_b1_exclude_recovers_explicit_time_range() -> None:
    """Nhóm B1 (TC-LIVE-04): op='exclude' bị LLM bỏ trống giờ sẽ tự khôi phục '15:00-16:00' từ raw_phrase."""
    w = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2026-11-20",
        op="exclude",
        is_excluded=True,
        start_local=None,
        end_local=None,
        raw_phrase="ngày mai 20/11/2026 mình chỉ bận 15h đến 16h",
    )
    assert w.start_local == "15:00"
    assert w.end_local == "16:00"
    assert w.is_ambiguous is False


async def test_v3_patch_b2_default_availability_auto_hydration_and_it2_guard(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nhóm B2 (TC-LIVE-14A): Tự nạp default_free_windows khi user đòi lịch mặc định nhưng LLM trả []; bảo vệ IT-2."""
    assert draft_service_module._user_requests_default_availability(
        "dùng khung giờ rảnh mặc định đã lưu của mình nhé"
    ) is True
    assert draft_service_module._user_requests_default_availability(
        "khung giờ 19h đến 21h này chỉ dùng cho báo cáo, lịch mặc định giữ nguyên"
    ) is False

    client, factory = uf1_api
    await client.get("/api/v1/preferences")
    async with factory() as db:
        db.add(
            SettingsAvailabilityRow(
                user_id=resolve_user_uuid("user_a"),
                source="user_confirmed",
                confirmed_at=FROZEN_NOW.astimezone(UTC),
                timezone="Asia/Ho_Chi_Minh",
                weekly_rules=[
                    {"weekday": w, "start_local": "08:00", "end_local": "10:00"}
                    for w in range(1, 8)
                ],
                date_overrides=[],
                task_limits=[],
            )
        )
        await db.commit()

    # Giả lập LLM trả structured_free_windows=[] và câu hỏi availability dù user đòi lịch mặc định
    ai_missed_default = LLMTaskExtractionPayload(
        title="Làm 2 bài tập Toán rời rạc",
        content="Làm 2 bài tập Toán rời rạc",
        scope="2 bài tập",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[],
        is_complete=False,
        summary="Làm 2 bài tập Toán rời rạc.",
        clarification_questions=["Bạn rảnh vào khung giờ nào?"],
    )
    _set_ai_sequence(monkeypatch, [ai_missed_default])

    d = await _create_draft(client, key="v3-b2-default")
    analyzed = await _analyze(
        client,
        d,
        key="v3-b2-default-a",
        message="Làm 2 bài tập Toán rời rạc, hạn 22/11/2026 22:00, dùng khung giờ rảnh mặc định đã lưu của mình nhé.",
    )
    assert analyzed["summary"]["can_plan"] is True
    assert _weekly_map(analyzed["summary"]["availability"])[1] == [("08:00", "10:00")]
    assert not any(i["blocking"] for i in analyzed["summary"]["clarification_issues"])


def test_v3_patch_b3_past_year_window_auto_corrected_to_current_year() -> None:
    """Nhóm B3 (TC-LIVE-05 T2): Năm quá khứ 2025-11-25 không xuất hiện trong input tự sửa sang 2026-11-25."""
    bad_year_win = ExtractedFreeWindowSchema(
        kind="specific_date",
        date="2025-11-25",
        start_local="20:00",
        end_local="21:00",
        raw_phrase="Riêng thứ Tư tuần sau 25/11 rảnh 20h đến 21h",
    )
    raw_res = ai_service_module.TaskExtractionResult(
        title="Đọc sách Kinh tế học",
        content="Đọc 4 chương sách Kinh tế học",
        scope="4 chương",
        time_constraint=_week_tc(deadline_at=datetime(2026, 11, 28, 22, 0, tzinfo=VN_TZ)),
        deadline=datetime(2026, 11, 28, 22, 0, tzinfo=VN_TZ),
        structured_free_windows=[bad_year_win],
        is_complete=False,
        summary="Đọc 4 chương sách.",
    )
    fixed = AIService._enforce_uf1_temporal_guardrails(
        raw_res,
        current_dt=FROZEN_NOW,
        timezone="Asia/Ho_Chi_Minh",
        user_input="Riêng thứ Tư tuần sau 25/11 rảnh 20h đến 21h",
    )
    assert fixed.structured_free_windows[0].date == "2026-11-25"
    assert fixed.structured_free_windows[0].is_ambiguous is False


async def test_v3_patch_b4_groq_gpt_oss_passes_reasoning_effort_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nhóm B4: _call_groq_structured truyền reasoning_effort='low' cho openai/gpt-oss-20b và không truyền cho qwen."""
    svc = AIService(
        app_settings=Settings(
            _env_file=None,
            GEMINI_API_KEY="",
            GROQ_API_KEY="gsk_MockTestKey",
            GOOGLE_CREDENTIALS_PATH="",
            GROQ_FALLBACK_MODEL="qwen/qwen3.8-27b",
            GROQ_SECONDARY_FALLBACK_MODEL="openai/gpt-oss-20b",
        )
    )
    captured_calls: list[dict[str, Any]] = []

    class FakeRateLimitError(Exception):
        status_code = 429

    class _Msg:
        content = json.dumps(
            {
                "title": "Làm báo cáo",
                "content": "Làm báo cáo thực tập",
                "scope": "10 trang Word",
                "structured_free_windows": [],
                "summary": "Làm báo cáo thực tập 10 trang Word.",
            },
            ensure_ascii=False,
        )

    class _Choice:
        message = _Msg()

    class _Comp:
        choices = [_Choice()]

    class _FakeCompletions:
        async def create(self, **kwargs: Any) -> _Comp:
            captured_calls.append(kwargs)
            if kwargs["model"] == "qwen/qwen3.8-27b":
                raise FakeRateLimitError("429 Rate limit")
            return _Comp()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeGroqClient:
        chat = _FakeChat()

    monkeypatch.setattr(svc, "_get_groq_client", lambda: _FakeGroqClient())
    res = await svc._call_groq_structured(
        system_prompt=UF1_EXTRACTION_SYSTEM_PROMPT,
        user_prompt='{"user_raw_input":"Làm báo cáo 10 trang"}',
        schema_cls=LLMTaskExtractionPayload,
        allow_secondary=False,
    )
    assert res.title == "Làm báo cáo"
    assert len(captured_calls) == 2
    assert captured_calls[0]["model"] == "qwen/qwen3.8-27b"
    assert "reasoning_effort" not in captured_calls[0]
    assert captured_calls[1]["model"] == "openai/gpt-oss-20b"
    assert captured_calls[1]["reasoning_effort"] == "low"


def test_v3_tc_live_08_specific_date_normalizes_word_weekday_before_conflict_check() -> None:
    """Fix TC-LIVE-08 T2: specific_date mang weekday=4 lệch với 'Thứ Sáu ngày 20/11/2026' được chuẩn hóa về 5."""
    w_ok = ExtractedFreeWindowSchema(
        kind="specific_date",
        weekday=4,
        date="2026-11-20",
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Ý mình là Thứ Sáu ngày 20/11/2026 từ 19:00 đến 21:00 nhé",
    )
    assert w_ok.weekday == 5
    assert w_ok.is_ambiguous is False
    assert w_ok.clarification_hint is None

    # Trường hợp mâu thuẫn thật ("Thứ Sáu ngày 21/11/2026", 21/11 là Thứ Bảy = 6) vẫn bắt conflict
    w_conflict = ExtractedFreeWindowSchema(
        kind="specific_date",
        weekday=6,
        date="2026-11-21",
        start_local="19:00",
        end_local="21:00",
        raw_phrase="Thứ Sáu ngày 21/11/2026 từ 19:00 đến 21:00",
    )
    assert w_conflict.weekday == 5
    assert w_conflict.is_ambiguous is True
    assert w_conflict.clarification_hint is not None


async def test_finding1_weekly_and_daily_merge_preserves_both_and_closed_base_dates(
    uf1_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #22 Finding #1: (a) weekly sáng T4 + daily tối giữ cả hai (cả 2 thứ tự); (b) ngày đóng [] + daily vẫn đóng []."""
    client, _ = uf1_api
    # Thứ Tư nằm trong tuần: 2026-11-18 (weekday=3), Thứ Sáu: 2026-11-20 (weekday=5)
    t_weekly_then_daily = LLMTaskExtractionPayload(
        title="Học lập trình",
        content="Làm bài tập cấu trúc dữ liệu",
        scope="4 bài tập",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=3,
                start_local="08:00",
                end_local="10:00",
                raw_phrase="Sáng thứ Tư 08:00-10:00",
            ),
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Tối nào cũng rảnh 19:00-21:00",
            ),
        ],
        is_complete=True,
        summary="Sáng thứ 4 và tối hằng ngày.",
    )
    t_daily_then_weekly = LLMTaskExtractionPayload(
        title="Học lập trình",
        content="Làm bài tập cấu trúc dữ liệu",
        scope="4 bài tập",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        availability_update_mode="replace",
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Tối nào cũng rảnh 19:00-21:00",
            ),
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=3,
                start_local="08:00",
                end_local="10:00",
                raw_phrase="Thêm sáng thứ Tư 08:00-10:00",
            ),
        ],
        is_complete=True,
        summary="Tối hằng ngày và sáng thứ 4.",
    )
    t_close_friday = LLMTaskExtractionPayload(
        title="Học lập trình",
        content="Làm bài tập cấu trúc dữ liệu",
        scope="4 bài tập",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        availability_update_mode="replace",
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="weekly_rule",
                weekday=3,
                start_local="08:00",
                end_local="10:00",
                raw_phrase="Sáng thứ Tư 08:00-10:00",
            ),
            ExtractedFreeWindowSchema(
                kind="specific_date",
                date="2026-11-20",
                is_excluded=True,
                raw_phrase="Thứ Sáu 20/11 bận cả ngày",
            ),
        ],
        is_complete=True,
        summary="Đóng 20/11.",
    )
    t_add_daily_keep_closed = LLMTaskExtractionPayload(
        title="Học lập trình",
        content="Làm bài tập cấu trúc dữ liệu",
        scope="4 bài tập",
        time_constraint=_week_tc(),
        deadline=WEEK_DEADLINE,
        structured_free_windows=[
            ExtractedFreeWindowSchema(
                kind="daily",
                start_local="19:00",
                end_local="21:00",
                raw_phrase="Bổ sung tối nào cũng rảnh 19:00-21:00",
            ),
        ],
        is_complete=True,
        summary="Thêm tối hằng ngày, giữ 20/11 đóng.",
    )
    _set_ai_sequence(
        monkeypatch,
        [t_weekly_then_daily, t_daily_then_weekly, t_close_friday, t_add_daily_keep_closed],
    )

    d = await _create_draft(client, key="finding1")
    d = await _analyze(client, d, key="f1-order1", message="Sáng thứ 4 8-10h, tối hằng ngày 19-21h")
    assert _weekly_map(d["summary"]["availability"])[3] == [("08:00", "10:00"), ("19:00", "21:00")]
    assert _override_map(d["summary"]["availability"])["2026-11-18"] == [
        ("08:00", "10:00"),
        ("19:00", "21:00"),
    ]

    d = await _analyze(client, d, key="f1-order2", message="Tối hằng ngày 19-21h, sáng thứ 4 8-10h")
    assert _weekly_map(d["summary"]["availability"])[3] == [("08:00", "10:00"), ("19:00", "21:00")]
    assert _override_map(d["summary"]["availability"])["2026-11-18"] == [
        ("08:00", "10:00"),
        ("19:00", "21:00"),
    ]

    d = await _analyze(client, d, key="f1-close", message="Sáng thứ 4 8-10h, 20/11 bận cả ngày")
    assert _override_map(d["summary"]["availability"])["2026-11-20"] == []

    # Giả lập base chỉ còn date_overrides=[2026-11-20: []] (không lưu window exclude trong ai_context)
    # để kiểm chứng trực tiếp cơ chế closed_base_dates trước daily op="add".
    base_avail = DraftAvailability.model_validate(d["summary"]["availability"])
    clean_draft = draft_service_module.PlanningDraft.model_validate(
        {
            **d,
            "ai_context": {},
        }
    )
    proposal, meta = draft_service_module._availability_proposal(
        t_add_daily_keep_closed,
        clean_draft,
        base_avail,
        "Asia/Ho_Chi_Minh",
    )
    assert proposal is not None and meta is not None
    prop_overrides = {
        ov.date.isoformat(): [
            (iv.start_local.strftime("%H:%M"), iv.end_local.strftime("%H:%M"))
            for iv in ov.intervals
        ]
        for ov in proposal.date_overrides
    }
    assert prop_overrides["2026-11-20"] == []
    assert prop_overrides["2026-11-18"] == [("08:00", "10:00"), ("19:00", "21:00")]

