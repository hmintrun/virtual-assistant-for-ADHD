"""Module validation: Hiện thực hóa bài toán [A02.2] - Kiểm điều kiện sang UF2.

Kiểm tra 4 nhóm thông tin bắt buộc (Nội dung, Phạm vi, Ràng buộc thời gian, Giờ rảnh xác nhận)
để quyết định cờ can_plan và sinh danh sách ClarificationIssue gộp 1 vòng duy nhất.
"""

from __future__ import annotations

import zoneinfo
from datetime import UTC, date, datetime, timedelta

from app.schemas.availability import Availability, DraftAvailability
from app.schemas.common import ClarificationIssue, TimeConstraint
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
