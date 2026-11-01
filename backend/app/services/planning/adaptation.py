"""Module adaptation: Hiện thực hóa bài toán [A20] - Nghiên cứu & Học hệ số dự phòng thích ứng.

Đảm nhận:
- Lọc mẫu sạch cấp Step: Step đã hoàn thành trọn vẹn, không đổi phạm vi, có actual_work_seconds từ nguồn hợp lệ.
- Tính tỷ số mẫu rho_i = actual_work_seconds / estimated_base_seconds cho từng bước.
- Tính trung vị (Median) kháng nhiễu trên tập mẫu sạch để chống sai lệch từ những ngày xao nhãng đột biến.
- Kẹp miền Policy [k_min, k_max] và chỉ sinh gợi ý O19 khi đủ cỡ mẫu và độ lệch >= ngưỡng delta_threshold (20%).
"""

from __future__ import annotations

import statistics
from typing import Any


def evaluate_buffer_adaptation(
    step_samples: list[dict[str, Any]],
    current_buffer_multiplier: float,
    min_sample_count: int = 5,
    policy_min: float = 1.0,
    policy_max: float = 2.0,
    delta_threshold: float = 0.2,
    existing_suggestion_id: str | None = None,
) -> dict[str, Any] | None:
    """Đánh giá dữ liệu lịch sử và đề xuất cập nhật hệ số đệm k_buffer cá nhân hóa (A20).

    Args:
        step_samples: Danh sách mẫu dữ liệu các Step đã thực hiện.
        current_buffer_multiplier: Hệ số đệm hiện tại đang có hiệu lực.
        min_sample_count: Ngưỡng số mẫu tối thiểu cần có (mặc định 5).
        policy_min: Ngưỡng dưới hợp lệ theo chính sách (mặc định 1.0).
        policy_max: Ngưỡng trên hợp lệ theo chính sách (mặc định 2.0).
        delta_threshold: Ngưỡng độ lệch tối thiểu để kích hoạt gợi ý (mặc định 0.2 tức 20%).
        existing_suggestion_id: ID của thẻ gợi ý cũ (nếu có) để gắn supersedes.

    Returns:
        dict cấu trúc AdaptationSuggestion nếu đủ điều kiện, hoặc None nếu chưa đủ mẫu/chưa đủ lệch.
    """
    valid_ratios: list[float] = []
    valid_step_ids: list[str] = []

    for s in step_samples:
        if s.get("status") != "completed":
            continue
        if s.get("scope_modified", False):
            continue

        base_sec = s.get("estimated_base_seconds", 0)
        actual_sec = s.get("actual_work_seconds")

        if not base_sec or base_sec <= 0:
            continue
        if actual_sec is None or actual_sec <= 0:
            continue

        ratio = actual_sec / base_sec
        valid_ratios.append(ratio)
        valid_step_ids.append(str(s.get("step_id", "")))

    sample_count = len(valid_ratios)

    if sample_count < min_sample_count:
        return None

    median_ratio = statistics.median(valid_ratios)
    rounded_ratio = round(median_ratio, 2)

    suggested_k = min(policy_max, max(policy_min, rounded_ratio))

    diff = abs(suggested_k - current_buffer_multiplier)
    if diff < delta_threshold:
        return None

    direction = "tăng" if suggested_k > current_buffer_multiplier else "giảm"
    reason = (
        f"Dựa trên dữ liệu thực tế của {sample_count} bước công việc đã hoàn tất, "
        f"tỷ số thời gian thực tế trung vị là {rounded_ratio:.2f}. "
        f"Trợ lý gợi ý {direction} hệ số dự phòng từ {current_buffer_multiplier:.2f} "
        f"thành {suggested_k:.2f} để kế hoạch các tuần tới sát với nhịp làm việc của bạn hơn."
    )

    return {
        "kind": "buffer_multiplier",
        "current_value": current_buffer_multiplier,
        "suggested_value": suggested_k,
        "sample_count": sample_count,
        "sample_refs": [{"step_id": sid} for sid in valid_step_ids if sid],
        "reason": reason,
        "policy_revision": "v1_robust_median",
        "model_revision": None,  # Thuần toán học thống kê, không dùng LLM
        "status": "pending",
        "supersedes_suggestion_id": existing_suggestion_id,
    }
