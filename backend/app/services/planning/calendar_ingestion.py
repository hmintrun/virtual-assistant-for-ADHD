"""Module chuẩn hóa và xử lý sự kiện Google Calendar (A22.1 Branch B).

Đảm nhận:
- Chuẩn hóa sự kiện Google sang DTO CalendarEvent (O26) và lọc bỏ sự kiện transparent / cancelled.
- Cắt xén (clipping) khoảng bận theo khung thời gian lập lịch [query_start, query_end].
- Hợp nhất các khoảng bận (Google events + Manual busy).
- Máy trạng thái độ phủ (coverage_state): complete | manual_fallback | manual_only | degraded.
- Đóng gói dữ liệu CalendarContext (O21).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarConnection
from app.schemas.calendar import (
    BusyInterval,
    BusyIntervalSource,
    CalendarError,
    CalendarEvent,
    CalendarSource,
    ExcludedCalendarSource,
    ManualBusyInterval,
)
from app.services.calendar_service import CalendarService, _resolve_uuid
from app.services.google_calendar_client import CalendarAuthError, CalendarClientError

logger = logging.getLogger(__name__)


def parse_event_datetime(
    dt_payload: dict[str, Any] | None, default_tz: timezone = UTC
) -> datetime | None:
    """Parse đối tượng start hoặc end của Google Calendar API thành UTC datetime.

    Hỗ trợ cả dateTime (RFC 3339) và date (sự kiện cả ngày - all-day).
    Với all-day event, Google trả date='YYYY-MM-DD', quy về 00:00:00 theo default_tz.
    """
    if not dt_payload:
        return None

    dt_str = dt_payload.get("dateTime")
    if dt_str:
        try:
            parsed = datetime.fromisoformat(dt_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=default_tz)
            return parsed.astimezone(UTC)
        except Exception as e:
            logger.debug("Failed to parse ISO datetime '%s': %s", dt_str, e)
            return None

    date_str = dt_payload.get("date")
    if date_str:
        try:
            d = date.fromisoformat(date_str)
            naive_dt = datetime.combine(d, time.min)
            localized = naive_dt.replace(tzinfo=default_tz)
            return localized.astimezone(UTC)
        except Exception as e:
            logger.debug("Failed to parse all-day date '%s': %s", date_str, e)
            return None

    return None


def normalize_google_event(
    item: dict[str, Any],
    connection_id: str,
    google_calendar_id: str,
) -> CalendarEvent | None:
    """Chuẩn hóa một item từ Google Calendar API sang CalendarEvent (O26).

    Quy tắc nghiệp vụ:
    - Bỏ qua nếu status == 'cancelled'.
    - Bỏ qua nếu transparency == 'transparent' (sự kiện không chiếm lịch / Free).
    - Bỏ qua nếu không parse được start_at hoặc end_at, hoặc end_at <= start_at.
    """
    status = item.get("status", "confirmed").lower()
    if status == "cancelled":
        return None

    transparency = item.get("transparency", "opaque").lower()
    if transparency == "transparent":
        return None

    event_id = item.get("id")
    if not event_id:
        return None

    start_at = parse_event_datetime(item.get("start"))
    end_at = parse_event_datetime(item.get("end"))

    if not start_at or not end_at or end_at <= start_at:
        return None

    return CalendarEvent(
        connection_id=connection_id,
        google_calendar_id=google_calendar_id,
        google_event_id=str(event_id),
        title=item.get("summary"),
        start_at=start_at,
        end_at=end_at,
    )


def clip_busy_interval(
    start_at: datetime,
    end_at: datetime,
    query_start: datetime,
    query_end: datetime,
) -> tuple[datetime, datetime] | None:
    """Cắt xén một khoảng thời gian theo cửa sổ truy vấn [query_start, query_end].

    Trả về (clipped_start, clipped_end) theo UTC, hoặc None nếu nằm ngoài cửa sổ.
    """
    s_utc = start_at.astimezone(UTC)
    e_utc = end_at.astimezone(UTC)
    q_s_utc = query_start.astimezone(UTC)
    q_e_utc = query_end.astimezone(UTC)

    if e_utc <= q_s_utc or s_utc >= q_e_utc:
        return None

    clipped_start = max(s_utc, q_s_utc)
    clipped_end = min(e_utc, q_e_utc)

    if clipped_end <= clipped_start:
        return None

    return clipped_start, clipped_end


def merge_busy_intervals(intervals: list[BusyInterval]) -> list[BusyInterval]:
    """Sắp xếp và hợp nhất các BusyInterval có thời gian chồng lấn hoặc chạm nhau."""
    if not intervals:
        return []

    sorted_ints = sorted(intervals, key=lambda x: (x.start_at, x.end_at))
    merged: list[BusyInterval] = [sorted_ints[0]]

    for curr in sorted_ints[1:]:
        prev = merged[-1]
        if curr.start_at <= prev.end_at:
            # Gộp khoảng thời gian
            new_end = max(prev.end_at, curr.end_at)
            merged[-1] = BusyInterval(
                start_at=prev.start_at,
                end_at=new_end,
                source=prev.source,
            )
        else:
            merged.append(curr)

    return merged


@dataclass(frozen=True)
class CalendarIngestionResult:
    """Kết quả thu nạp dữ liệu từ các nguồn Calendar."""

    event_snapshots: list[CalendarEvent]
    busy_intervals: list[BusyInterval]
    calendar_errors: list[CalendarError]
    coverage_state: str


async def ingest_calendar_sources(
    db: AsyncSession,
    user_id: UUID | str,
    selected_sources: list[CalendarSource],
    excluded_sources: list[ExcludedCalendarSource],
    query_start: datetime,
    query_end: datetime,
    manual_busy_intervals: list[ManualBusyInterval] | None = None,
) -> CalendarIngestionResult:
    """Thu thập, chuẩn hóa sự kiện và tính toán khoảng bận từ các nguồn Google Calendar."""
    uid = _resolve_uuid(user_id)
    cal_service = CalendarService(db)
    client = cal_service.client

    now_utc = datetime.now(UTC)
    q_start_utc = query_start.astimezone(UTC)
    q_end_utc = query_end.astimezone(UTC)

    excluded_map = {
        (ex.connection_id, ex.google_calendar_id): ex for ex in (excluded_sources or [])
    }

    event_snapshots: list[CalendarEvent] = []
    busy_intervals: list[BusyInterval] = []
    calendar_errors: list[CalendarError] = []

    # 1. Bổ sung các khoảng bận nhập tay (ManualBusyInterval)
    if manual_busy_intervals:
        for mb in manual_busy_intervals:
            clipped = clip_busy_interval(mb.start_at, mb.end_at, q_start_utc, q_end_utc)
            if clipped:
                busy_intervals.append(
                    BusyInterval(
                        start_at=clipped[0],
                        end_at=clipped[1],
                        source=BusyIntervalSource(
                            kind="manual", client_interval_id=mb.client_interval_id
                        ),
                    )
                )

    if not selected_sources:
        # Người dùng không chọn nguồn Google nào
        merged = merge_busy_intervals(busy_intervals)
        return CalendarIngestionResult(
            event_snapshots=[],
            busy_intervals=merged,
            calendar_errors=[],
            coverage_state="manual_only",
        )

    # 2. Duyệt qua từng nguồn Calendar được chọn
    for src in selected_sources:
        src_key = (src.connection_id, src.google_calendar_id)
        if src_key in excluded_map:
            # Nguồn này người dùng đã xác nhận bỏ qua trong lần lập phương án này
            continue

        try:
            conn = await cal_service.get_connection_by_id(uid, _resolve_uuid(src.connection_id))
            if not conn:
                raise CalendarClientError(
                    f"Không tìm thấy kết nối {src.connection_id}", status_code=404
                )

            access_token = await cal_service.get_valid_access_token(conn)
            raw_events = await client.list_events(
                access_token=access_token,
                calendar_id=src.google_calendar_id,
                time_min=q_start_utc,
                time_max=q_end_utc,
            )

            # Chuẩn hóa các sự kiện đọc được
            for raw_ev in raw_events:
                normalized = normalize_google_event(
                    raw_ev, src.connection_id, src.google_calendar_id
                )
                if normalized:
                    event_snapshots.append(normalized)
                    clipped = clip_busy_interval(
                        normalized.start_at, normalized.end_at, q_start_utc, q_end_utc
                    )
                    if clipped:
                        busy_intervals.append(
                            BusyInterval(
                                start_at=clipped[0],
                                end_at=clipped[1],
                                source=BusyIntervalSource(
                                    kind="calendar_event",
                                    connection_id=src.connection_id,
                                    google_calendar_id=src.google_calendar_id,
                                    google_event_id=normalized.google_event_id,
                                ),
                            )
                        )

        except CalendarAuthError as exc:
            logger.warning("Auth error reading calendar %s: %s", src.google_calendar_id, exc)
            calendar_errors.append(
                CalendarError(
                    connection_id=src.connection_id,
                    google_calendar_id=src.google_calendar_id,
                    error_code="AUTH_EXPIRED",
                    retryable=False,
                    requires_reconnect=True,
                    observed_at=now_utc,
                )
            )
        except Exception as exc:
            logger.warning("Error reading calendar %s: %s", src.google_calendar_id, exc)
            calendar_errors.append(
                CalendarError(
                    connection_id=src.connection_id,
                    google_calendar_id=src.google_calendar_id,
                    error_code="CALENDAR_READ_FAILED",
                    retryable=True,
                    requires_reconnect=False,
                    observed_at=now_utc,
                )
            )

    # 3. Tính toán máy trạng thái độ phủ (coverage_state)
    if not calendar_errors:
        coverage_state = "manual_fallback" if excluded_sources else "complete"
    else:
        # Kiểm tra xem toàn bộ các lỗi có nằm trong excluded_sources hay chưa
        unconfirmed_errors = [
            err for err in calendar_errors if (err.connection_id, err.google_calendar_id) not in excluded_map
        ]
        if not unconfirmed_errors:
            coverage_state = "manual_fallback"
        else:
            coverage_state = "degraded"

    # 4. Hợp nhất danh sách khoảng bận
    merged_busy = merge_busy_intervals(busy_intervals)

    return CalendarIngestionResult(
        event_snapshots=event_snapshots,
        busy_intervals=merged_busy,
        calendar_errors=calendar_errors,
        coverage_state=coverage_state,
    )
