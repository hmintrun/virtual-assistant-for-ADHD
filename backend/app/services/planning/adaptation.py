"""A20: gợi ý hệ số từ mẫu Step sạch; không tự áp cài đặt hay gọi DB/AI.

Caller truyền mẫu cùng user, cùng phạm vi và ước lượng gốc, theo thứ tự hoàn tất
cũ đến mới. Các giá trị mặc định là cấu hình thử nghiệm, chưa là kết luận pilot.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _finite_number(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{name} phải là số hữu hạn")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError(f"{name} phải là số hữu hạn")
    return number


def evaluate_buffer_adaptation(
    step_samples: list[dict[str, Any]],
    current_buffer_multiplier: float,
    min_sample_count: int = 7,
    policy_min: float = 1.0,
    policy_max: float = 2.0,
    delta_threshold: float = 0.2,
    existing_suggestion_id: str | None = None,
    *,
    sliding_window_steps: int = 10,
    step_increment: float = 0.05,
    last_suggestion_at: datetime | None = None,
    current_time: datetime | None = None,
    cooldown_seconds: int = 7 * 24 * 3600,
    policy_revision: str = "v2_step_median_checked_inputs",
) -> dict[str, Any] | None:
    """Trả gợi ý pending hoặc None; cấu hình sai gây ValueError, mẫu bẩn bị loại.

    Một Step trùng cùng số liệu/nguồn chỉ tính một lần ở vị trí đầu. Nếu cùng ID
    có số liệu/nguồn hợp lệ bất nhất, loại cả nhóm để caller đối soát revision.
    Mẫu thiếu scope_modified=False hoặc nguồn timer_proxy/self_report bị loại.
    Backend chịu tổng hợp actual làm chính và bảo đảm cùng phạm vi/đúng user.

    Cửa sổ áp dụng sau lọc/loại trùng. Làm tròn gần nhất theo step_increment,
    đúng nửa bước làm tròn lên; sau đó kẹp miền policy. delta_threshold là độ
    lệch tuyệt đối của k, không phải tỷ lệ phần trăm. Không gợi ý khi k không đổi.

    Nếu bật cooldown và có lần gợi ý trước, caller phải truyền current_time.
    Mọi mốc truyền vào phải có múi giờ; hàm không tự lấy đồng hồ hệ thống.
    Giữ thứ tự các tham số positional cũ; các tùy chọn mới chỉ nhận keyword.
    """
    for name, value, minimum in (
        ("min_sample_count", min_sample_count, 1),
        ("sliding_window_steps", sliding_window_steps, 1),
        ("cooldown_seconds", cooldown_seconds, 0),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} phải là số nguyên >= {minimum}")
    if min_sample_count > sliding_window_steps:
        raise ValueError("Cửa sổ mẫu phải >= min_sample_count")
    current = _finite_number(current_buffer_multiplier, "current_buffer_multiplier")
    lower = _finite_number(policy_min, "policy_min")
    upper = _finite_number(policy_max, "policy_max")
    threshold = _finite_number(delta_threshold, "delta_threshold")
    increment = _finite_number(step_increment, "step_increment")
    if current < 1 or lower < 1 or upper < lower:
        raise ValueError("Hệ số hiện hành và policy_min phải >= 1; policy_max >= policy_min")
    if threshold < 0 or increment <= 0:
        raise ValueError("delta_threshold phải >= 0 và step_increment phải > 0")
    if not isinstance(policy_revision, str) or not policy_revision.strip():
        raise ValueError("Cần policy_revision không rỗng")
    for name, stamp in (("last_suggestion_at", last_suggestion_at), ("current_time", current_time)):
        if stamp is not None and (not isinstance(stamp, datetime) or stamp.utcoffset() is None):
            raise ValueError(f"{name} phải có múi giờ")
    if cooldown_seconds > 0 and last_suggestion_at is not None:
        if current_time is None:
            raise ValueError("Cần current_time để kiểm cooldown")
        elapsed = (
            current_time.astimezone(UTC) - last_suggestion_at.astimezone(UTC)
        ).total_seconds()
        if elapsed < 0:
            raise ValueError("last_suggestion_at không được nằm sau current_time")
        if elapsed < cooldown_seconds:
            return None

    valid: dict[str, tuple[Decimal, Decimal, str]] = {}
    conflicting_ids: set[str] = set()
    for sample in step_samples:
        if not isinstance(sample, dict):
            continue
        step_id = sample.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip():
            continue
        step_id = step_id.strip()
        if step_id in conflicting_ids or sample.get("status") != "completed":
            continue
        if sample.get("scope_modified") is not False:
            continue
        source = sample.get("actual_source")
        if source not in ("timer_proxy", "self_report"):
            continue
        try:
            base = _finite_number(sample.get("estimated_base_seconds"), "estimated_base_seconds")
            actual = _finite_number(sample.get("actual_work_seconds"), "actual_work_seconds")
        except ValueError:
            continue
        if base <= 0 or actual <= 0:
            continue
        record = (base, actual, source)
        if step_id in valid and valid[step_id] != record:
            valid.pop(step_id)
            conflicting_ids.add(step_id)
        elif step_id not in valid:
            valid[step_id] = record

    selected = list(valid.items())[-sliding_window_steps:]
    if len(selected) < min_sample_count:
        return None
    median_ratio = statistics.median(actual / base for _, (base, actual, _) in selected)
    rounded = (median_ratio / increment).to_integral_value(rounding=ROUND_HALF_UP) * increment
    suggested = min(upper, max(lower, rounded))
    if suggested == current or abs(suggested - current) <= threshold:
        return None
    direction = "tăng" if suggested > current else "giảm"
    return {
        "kind": "buffer_multiplier",
        "current_value": float(current),
        "suggested_value": float(suggested),
        "sample_count": len(selected),
        "sample_refs": [step_id for step_id, _ in selected],
        "reason": (
            f"Dữ liệu của {len(selected)} Step hoàn tất có tỷ số actual/estimate trung vị "
            f"{median_ratio:.3f}. Gợi ý {direction} hệ số từ {current:.3f} thành "
            f"{suggested:.3f}; bạn có thể xem và quyết định áp dụng."
        ),
        "policy_revision": policy_revision.strip(),
        "model_revision": None,
        "status": "pending",
        "supersedes_suggestion_id": existing_suggestion_id,
    }
