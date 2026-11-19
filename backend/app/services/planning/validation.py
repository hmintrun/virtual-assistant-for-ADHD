"""Module validation: Hiện thực hóa bài toán [A02.2] - Kiểm điều kiện sang UF2.

Kiểm tra 4 nhóm thông tin bắt buộc (Nội dung, Phạm vi, Ràng buộc thời gian, Giờ rảnh xác nhận)
để quyết định cờ can_plan và sinh danh sách ClarificationIssue gộp 1 vòng duy nhất.
"""

from __future__ import annotations

import zoneinfo
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.schemas.availability import Availability, DraftAvailability
from app.schemas.calendar import BusyInterval
from app.schemas.common import ClarificationIssue, TimeConstraint, TimeInterval
from app.schemas.draft import TaskSummary


def _validate_timezone(tz_name: str | None) -> bool:
    """Xác thực chuỗi múi giờ IANA hợp lệ."""
    if not tz_name or not tz_name.strip():
        return False
    try:
        zoneinfo.ZoneInfo(tz_name.strip())
        return True
    except Exception:
        return False


def _get_time_constraint_endpoints(
    tc: TimeConstraint,
) -> tuple[datetime | None, datetime | None]:
    """Trích xuất mốc bắt đầu và kết thúc chuẩn hóa từ TimeConstraint."""
    start = tc.start_at
    end = tc.deadline_at if tc.kind == "deadline" else tc.finish_by_at
    return start, end


def _has_declared_time(availability: DraftAvailability, start: datetime, end: datetime) -> bool:
    """Chỉ kiểm có khung được phép trong window; chưa trừ bận hoặc kết luận khả thi."""
    tz = zoneinfo.ZoneInfo(availability.timezone)
    first_day, last_day = start.astimezone(tz).date(), end.astimezone(tz).date()
    overrides = {item.date: item.intervals for item in availability.date_overrides}

    def overlaps(day, interval):
        left = datetime.combine(day, interval.start_local, tzinfo=tz)
        right = datetime.combine(day, interval.end_local, tzinfo=tz)
        return left < end and right > start

    if any(
        first_day <= day <= last_day and any(overlaps(day, i) for i in intervals)
        for day, intervals in overrides.items()
    ):
        return True
    for rule in availability.weekly_rules:
        day = first_day + timedelta(days=(rule.weekday - first_day.isoweekday()) % 7)
        while day <= last_day:
            if day not in overrides and overlaps(day, rule):
                return True
            day += timedelta(days=7)
    return False


def validate_planning_prerequisites(
    summary: TaskSummary,
    availability: Availability | DraftAvailability | None,
    reference_now: datetime | None = None,
) -> tuple[bool, list[ClarificationIssue]]:
    """Kiểm tra điều kiện tiên quyết để chuyển từ UF1 sang UF2 (A02.2).

    Args:
        summary: Bảng tóm tắt công việc TaskSummary (O04).
        availability: Cấu hình giờ rảnh đã xác nhận Availability (O02).

    Returns:
        tuple (can_plan: bool, issues: list[ClarificationIssue])
    """
    issues: list[ClarificationIssue] = []

    if not summary.title or not summary.title.strip():
        issues.append(
            ClarificationIssue(
                code="MISSING_TITLE",
                field_path="title",
                message="Tên công việc không được để trống.",
                question="Bạn muốn đặt tên cho công việc này là gì?",
                blocking=True,
            )
        )

    has_desc = bool(summary.description and summary.description.strip())
    has_goal = bool(summary.goal and summary.goal.strip())
    if not has_desc and not has_goal:
        issues.append(
            ClarificationIssue(
                code="MISSING_CONTENT",
                field_path="description",
                message="Cần có mô tả nội dung hoặc mục tiêu công việc.",
                question="Nội dung cụ thể bạn cần thực hiện là gì?",
                blocking=True,
            )
        )

    if not summary.scope or not summary.scope.strip():
        issues.append(
            ClarificationIssue(
                code="MISSING_SCOPE",
                field_path="scope",
                message="Phạm vi và đầu ra mong muốn chưa được xác định.",
                question="Kết quả hoặc sản phẩm hoàn thành cụ thể của việc này là gì?",
                blocking=True,
            )
        )

    if summary.importance is not None and summary.importance < 1:
        issues.append(
            ClarificationIssue(
                code="INVALID_IMPORTANCE",
                field_path="importance",
                message="Mức độ quan trọng phải là số nguyên dương (>= 1).",
                question="Bạn muốn đặt mức ưu tiên cho công việc này là bao nhiêu (1, 2, 3...)?",
                blocking=True,
            )
        )

    start_utc: datetime | None = None
    end_utc: datetime | None = None

    if summary.time_constraint is None:
        issues.append(
            ClarificationIssue(
                code="MISSING_TIME_CONSTRAINT",
                field_path="time_constraint",
                message="Chưa có khung thời gian hoặc hạn chót hoàn thành.",
                question="Khi nào bạn muốn bắt đầu và hạn chót hoàn tất công việc là lúc nào?",
                blocking=True,
            )
        )
    else:
        tc = summary.time_constraint
        if not _validate_timezone(tc.input_timezone):
            issues.append(
                ClarificationIssue(
                    code="INVALID_TIMEZONE",
                    field_path="time_constraint.input_timezone",
                    message=f"Múi giờ '{tc.input_timezone}' không hợp lệ theo chuẩn IANA.",
                    question="Bạn đang ở múi giờ nào (ví dụ: Asia/Ho_Chi_Minh)?",
                    blocking=True,
                )
            )

        start_dt, end_dt = _get_time_constraint_endpoints(tc)
        field_end_name = "deadline_at" if tc.kind == "deadline" else "finish_by_at"

        if start_dt is None:
            issues.append(
                ClarificationIssue(
                    code="MISSING_START_TIME",
                    field_path="time_constraint.start_at",
                    message="Thời điểm bắt đầu sớm nhất chưa được xác định.",
                    question="Bạn có thể bắt đầu làm việc này từ lúc nào?",
                    blocking=True,
                )
            )

        if end_dt is None:
            issues.append(
                ClarificationIssue(
                    code="MISSING_END_TIME",
                    field_path=f"time_constraint.{field_end_name}",
                    message="Thời hạn hoàn thành chưa được xác định.",
                    question="Hạn chót bạn phải hoàn thành công việc này là khi nào?",
                    blocking=True,
                )
            )

        if start_dt is not None and end_dt is not None:
            if start_dt.utcoffset() is None or end_dt.utcoffset() is None:
                issues.append(
                    ClarificationIssue(
                        code="TIMEZONE_OFFSET_MISSING",
                        field_path="time_constraint",
                        message="Mốc thời gian bắt buộc phải có thông tin múi giờ.",
                        question="Vui lòng cung cấp giờ kèm múi giờ rõ ràng.",
                        blocking=True,
                    )
                )
            else:
                start_utc = start_dt.astimezone(UTC)
                end_utc = end_dt.astimezone(UTC)
                if start_utc >= end_utc:
                    issues.append(
                        ClarificationIssue(
                            code="INVALID_TIME_ORDER",
                            field_path=f"time_constraint.{field_end_name}",
                            message="Thời điểm bắt đầu phải trước thời hạn hoàn thành.",
                            question="Thời điểm bắt đầu đang muộn hơn hạn chót, bạn muốn điều chỉnh lại thế nào?",
                            blocking=True,
                        )
                    )

    if end_utc is not None and reference_now is not None and end_utc <= reference_now:
        issues.append(
            ClarificationIssue(
                code="END_TIME_PASSED",
                field_path="time_constraint",
                message="Mốc hoàn tất đã qua; bản nháp vẫn được giữ.",
                question="Bạn muốn đổi thời gian hoàn tất thành khi nào?",
                blocking=True,
            )
        )

    if summary.availability is not None:
        availability = summary.availability
    if summary.availability is None and summary.availability_ref is None:
        issues.append(
            ClarificationIssue(
                code="MISSING_AVAILABILITY",
                field_path="availability",
                message="Bạn chưa cung cấp giờ rảnh để thực hiện công việc này.",
                question="Bạn có thể thực hiện công việc này vào những ngày và khung giờ nào?",
                blocking=True,
            )
        )
    elif availability is None:
        issues.append(
            ClarificationIssue(
                code="AVAILABILITY_NOT_FOUND",
                field_path="availability_ref",
                message="Không tìm thấy dữ liệu giờ rảnh tương ứng.",
                question="Vui lòng kiểm tra lại cấu hình giờ rảnh của bạn.",
                blocking=True,
            )
        )
    else:
        if isinstance(availability, Availability) and availability.confirmed_at is None:
            issues.append(
                ClarificationIssue(
                    code="AVAILABILITY_NOT_CONFIRMED",
                    field_path="availability_ref.confirmed_at",
                    message="Bộ giờ rảnh chưa được bạn xác nhận.",
                    question="Bạn có đồng ý dùng khung giờ rảnh đã khai báo để xếp lịch không?",
                    blocking=True,
                )
            )

        for idx, rule in enumerate(availability.weekly_rules):
            if not (1 <= rule.weekday <= 7):
                issues.append(
                    ClarificationIssue(
                        code="INVALID_WEEKDAY",
                        field_path=f"availability.weekly_rules[{idx}].weekday",
                        message=f"Ngày trong tuần {rule.weekday} không hợp lệ (phải từ 1 đến 7).",
                        question="Vui lòng chọn ngày trong tuần từ Thứ Hai (1) đến Chủ Nhật (7).",
                        blocking=True,
                    )
                )
            if rule.start_local >= rule.end_local:
                issues.append(
                    ClarificationIssue(
                        code="INVALID_RULE_INTERVAL",
                        field_path=f"availability.weekly_rules[{idx}]",
                        message=f"Khung giờ {rule.start_local} - {rule.end_local} không hợp lệ (giờ bắt đầu phải trước giờ kết thúc).",
                        question=f"Khung giờ thứ {rule.weekday} bị ngược, bạn muốn sửa lại ra sao?",
                        blocking=True,
                    )
                )

        seen_dates: set[date | str] = set()
        for idx, override in enumerate(availability.date_overrides):
            if override.date in seen_dates:
                issues.append(
                    ClarificationIssue(
                        code="DUPLICATE_OVERRIDE_DATE",
                        field_path=f"availability.date_overrides[{idx}].date",
                        message=f"Ngày ngoại lệ {override.date} bị lặp lại.",
                        question=f"Ngày {override.date} có 2 cấu hình ngoại lệ, bạn muốn giữ khung giờ nào?",
                        blocking=True,
                    )
                )
            seen_dates.add(override.date)
            for int_idx, interval in enumerate(override.intervals):
                if interval.start_local >= interval.end_local:
                    issues.append(
                        ClarificationIssue(
                            code="INVALID_OVERRIDE_INTERVAL",
                            field_path=f"availability.date_overrides[{idx}].intervals[{int_idx}]",
                            message=f"Khung giờ ngoại lệ {interval.start_local} - {interval.end_local} không hợp lệ.",
                            question=f"Khung giờ ngày {override.date} bị ngược, bạn muốn chỉnh lại thế nào?",
                            blocking=True,
                        )
                    )

        has_rules = bool(
            availability.weekly_rules or any(ov.intervals for ov in availability.date_overrides)
        )
        if not has_rules:
            issues.append(
                ClarificationIssue(
                    code="EMPTY_AVAILABILITY",
                    field_path="availability",
                    message="Bạn chưa có khung giờ rảnh nào được cài đặt.",
                    question="Vui lòng thiết lập ít nhất một khung giờ rảnh trong tuần để AI xếp lịch.",
                    blocking=True,
                )
            )

    if summary.availability is not None and start_utc is not None and end_utc is not None:
        effective_start = max(start_utc, reference_now) if reference_now is not None else start_utc
        if effective_start < end_utc and not _has_declared_time(
            summary.availability, effective_start, end_utc
        ):
            issues.append(
                ClarificationIssue(
                    code="AVAILABILITY_OUTSIDE_TIME_CONSTRAINT",
                    field_path="availability",
                    message="Chưa có giờ rảnh nào nằm trong khoảng thời gian còn được phép thực hiện.",
                    question="Bạn bổ sung giờ rảnh trong thời gian thực hiện hoặc điều chỉnh mốc thời gian nhé?",
                    blocking=True,
                )
            )

    existing_issues = summary.clarification_issues or []
    seen_issue_keys = {(iss.field_path, iss.code) for iss in issues}
    for existing in existing_issues:
        key = (existing.field_path, existing.code)
        if key not in seen_issue_keys:
            issues.append(existing)
            seen_issue_keys.add(key)

    has_blocking = any(iss.blocking for iss in issues)
    can_plan = not has_blocking

    return can_plan, issues


@dataclass(frozen=True)
class SessionStartFeasibilityResult:
    """Kết quả kiểm tra tính khả thi điều kiện bắt đầu phiên tập trung (A12.1)."""

    is_feasible: bool
    error_code: str | None = None
    message: str = ""
    suggested_action: str = ""
    adjusted_duration_seconds: int | None = None


def validate_session_start_feasibility(
    session: Any,
    requested_start_at: datetime,
    live_run: Any = None,
    busy_intervals: list[BusyInterval] | None = None,
    adjacent_sessions: list[Any] | None = None,
    free_intervals: list[TimeInterval] | None = None,
    min_inter_session_gap_seconds: int = 900,
    max_early_start_seconds: int = 900,
    max_start_delay_seconds: int = 900,
) -> SessionStartFeasibilityResult:
    """Kiểm tra toàn diện 5 Guardrails trước khi cho phép bắt đầu phiên tập trung (A12.1).

    Guardrail 1: Quyền sở hữu và trạng thái phiên (scheduled / waiting).
    Guardrail 2: Cửa sổ thời gian bắt đầu (Early <= 15p, Delay <= 15p).
    Guardrail 3: Ràng buộc 1-Step UI (Không có phiên nào khác đang live).
    Guardrail 4: Không đè lên lịch bận Calendar (Busy Intervals).
    Guardrail 5: Đảm bảo khoảng nghỉ tối thiểu 15p (Inter-session gap) với phiên liền kề.
    """
    # Guardrail 1: Kiểm tra trạng thái Session
    status = getattr(session, "status", "scheduled")
    start_state = getattr(session, "start_state", "waiting")

    if status == "completed":
        return SessionStartFeasibilityResult(
            is_feasible=False,
            error_code="SESSION_ALREADY_COMPLETED",
            message="Phiên làm việc này đã hoàn thành.",
            suggested_action="view_summary",
        )
    if status == "cancelled":
        return SessionStartFeasibilityResult(
            is_feasible=False,
            error_code="SESSION_CANCELLED",
            message="Phiên làm việc này đã bị hủy.",
            suggested_action="reschedule_uf6",
        )
    if status == "missed" or start_state == "not_started":
        return SessionStartFeasibilityResult(
            is_feasible=False,
            error_code="SESSION_MISSED",
            message="Phiên làm việc đã bị quá hạn bắt đầu (quá 15 phút). Hãy dời lịch nhé.",
            suggested_action="reschedule_uf6",
        )
    if status not in ("scheduled", "in_progress"):
        return SessionStartFeasibilityResult(
            is_feasible=False,
            error_code="INVALID_SESSION_STATUS",
            message="Trạng thái phiên không hợp lệ để bắt đầu.",
            suggested_action="reload",
        )

    # Guardrail 2: Kiểm tra cửa sổ thời gian bắt đầu (Early / Late)
    req_utc = requested_start_at.astimezone(UTC)
    sched_start_utc = session.scheduled_start.astimezone(UTC)

    earliest_allowed = sched_start_utc - timedelta(seconds=max_early_start_seconds)
    if req_utc < earliest_allowed:
        diff_min = int((sched_start_utc - req_utc).total_seconds() // 60)
        return SessionStartFeasibilityResult(
            is_feasible=False,
            error_code="START_TOO_EARLY",
            message=f"Chưa đến giờ bắt đầu phiên (còn {diff_min} phút). Bạn có thể xem micro-task khởi động 2 phút trước nhé.",
            suggested_action="wait",
        )

    latest_allowed = sched_start_utc + timedelta(seconds=max_start_delay_seconds)
    if req_utc > latest_allowed:
        return SessionStartFeasibilityResult(
            is_feasible=False,
            error_code="START_EXPIRED",
            message="Đã quá 15 phút so với giờ hẹn bắt đầu. ProPlan sẽ giúp bạn dời phiên này sang khung giờ khác thoải mái hơn.",
            suggested_action="reschedule_uf6",
        )

    # Guardrail 3: Ràng buộc 1-Step UI (Không có phiên live nào khác)
    if live_run is not None:
        run_state = getattr(live_run, "run_state", None)
        run_session_id = str(getattr(live_run, "session_id", ""))
        current_session_id = str(getattr(session, "id", getattr(session, "session_id", "")))

        if run_state in ("running", "paused") and run_session_id != current_session_id:
            return SessionStartFeasibilityResult(
                is_feasible=False,
                error_code="ANOTHER_SESSION_ACTIVE",
                message="Bạn đang có một phiên tập trung khác đang chạy. Hãy hoàn thành phiên đó trước nhé.",
                suggested_action="complete_active",
            )

    # Guardrail 4: Xung đột với sự kiện bận từ Calendar
    work_seconds = getattr(session, "work_seconds", 1500)
    rest_seconds = getattr(session, "rest_seconds", 300)
    run_start = req_utc
    run_end = req_utc + timedelta(seconds=work_seconds + rest_seconds)

    if busy_intervals:
        for busy in busy_intervals:
            b_s = busy.start_at.astimezone(UTC)
            b_e = busy.end_at.astimezone(UTC)
            # Kiểm tra khoảng giao nhau
            if max(run_start, b_s) < min(run_end, b_e):
                return SessionStartFeasibilityResult(
                    is_feasible=False,
                    error_code="CONFLICTS_WITH_BUSY_EVENT",
                    message="Thời gian thực hiện phiên này bị trùng vào một lịch bận đã có. Hãy dời lịch để không bị ngắt quãng nhé.",
                    suggested_action="reschedule_uf6",
                )

    # Guardrail 5: Đảm bảo khoảng nghỉ tối thiểu (Inter-session Gap >= 15p)
    if adjacent_sessions:
        current_session_id = str(getattr(session, "id", getattr(session, "session_id", "")))
        for adj in adjacent_sessions:
            adj_id = str(getattr(adj, "id", getattr(adj, "session_id", "")))
            if adj_id == current_session_id:
                continue

            adj_start = adj.scheduled_start.astimezone(UTC)
            adj_end = adj.scheduled_end.astimezone(UTC)

            # Chồng lấn trực tiếp với phiên lân cận
            if max(run_start, adj_start) < min(run_end, adj_end):
                return SessionStartFeasibilityResult(
                    is_feasible=False,
                    error_code="INSUFFICIENT_INTER_SESSION_GAP",
                    message="Thời gian phiên này bị chồng lấn vào một phiên khác trong kế hoạch.",
                    suggested_action="reschedule_uf6",
                )

            # Phiên kế tiếp ngay sau phiên hiện tại
            if adj_start >= run_end:
                gap = (adj_start - run_end).total_seconds()
                if gap < min_inter_session_gap_seconds:
                    return SessionStartFeasibilityResult(
                        is_feasible=False,
                        error_code="INSUFFICIENT_INTER_SESSION_GAP",
                        message=f"Thời gian kết thúc quá sát phiên tiếp theo (cần tối thiểu {min_inter_session_gap_seconds // 60} phút nghỉ ngơi).",
                        suggested_action="reschedule_uf6",
                    )

            # Phiên liền trước phiên hiện tại
            if adj_end <= run_start:
                gap = (run_start - adj_end).total_seconds()
                if gap < min_inter_session_gap_seconds:
                    return SessionStartFeasibilityResult(
                        is_feasible=False,
                        error_code="INSUFFICIENT_INTER_SESSION_GAP",
                        message=f"Thời gian bắt đầu quá gần với phiên vừa kết thúc (cần tối thiểu {min_inter_session_gap_seconds // 60} phút nghỉ ngơi).",
                        suggested_action="wait",
                    )

    return SessionStartFeasibilityResult(
        is_feasible=True,
        error_code=None,
        message="Điều kiện bắt đầu phiên hoàn toàn khả thi.",
        suggested_action="start_now",
    )

