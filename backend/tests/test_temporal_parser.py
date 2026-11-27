"""Unit test cho Mini-Duckling `app.utils.temporal_parser` (Trụ cột 2b)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.utils.temporal_parser import (
    TemporalAnchor,
    _apply_period_to_hour,
    extract_temporal_anchors,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Thứ Tư 2026-11-18 10:00+07:00
REF_WED = datetime(2026, 11, 18, 10, 0, tzinfo=VN_TZ)


class TestTemporalParserWeekdaysAndOrdinals:
    def test_extracts_all_vietnamese_weekdays(self) -> None:
        cases = (
            ("rảnh Thứ 2 19:00-21:00", 1, "2026-11-23"),
            ("rảnh thứ hai 19:00-21:00", 1, "2026-11-23"),
            ("học thứ 3 09:00-11:00", 2, "2026-11-24"),
            ("làm Thứ Tư 14:00-16:00", 3, "2026-11-18"),
            ("họp Thứ Năm 08:00-10:00", 4, "2026-11-19"),
            ("nộp Thứ 6 20:00", 5, "2026-11-20"),
            ("ôn Thứ Bảy 09:00-11:00", 6, "2026-11-21"),
            ("nghỉ Chủ nhật 14:00-16:00", 7, "2026-11-22"),
            ("rảnh CN 19:00-21:00", 7, "2026-11-22"),
        )
        for text, expected_wd, expected_date in cases:
            anchors = extract_temporal_anchors(text, reference_now=REF_WED)
            assert len(anchors) == 1, text
            assert isinstance(anchors[0], TemporalAnchor)
            assert anchors[0].iso_weekday == expected_wd, text
            assert anchors[0].resolved_date == expected_date, text
            assert anchors[0].has_explicit_clock_time is True, text

    def test_ordinal_false_positives_are_stripped(self) -> None:
        ordinals = (
            "Làm lần thứ 2 bài tập, rảnh 19:00-21:00",
            "Xem lại bước thứ 3",
            "Viết phần thứ 4 cho báo cáo",
            "Ôn chương thứ 5 môn Toán",
            "Tổng kết tuần thứ 6",
            "Nhật ký ngày thứ 7 của dự án",
            "Ghi chú buổi thứ 2",
            "Kiểm tra mục thứ 3",
            "Giải câu thứ 4",
            "Hoàn thiện bài thứ 5",
        )
        for text in ordinals:
            anchors = extract_temporal_anchors(text, reference_now=REF_WED)
            for anchor in anchors:
                assert anchor.iso_weekday is None, (text, anchor)

    def test_compound_vocabulary_and_multi_weekday_span(self) -> None:
        # "thứ tư duy" / "thứ tư vấn" / "thứ ba mươi" không phải ngày trong tuần
        assert extract_temporal_anchors("Phân tích thứ tư duy hệ thống", reference_now=REF_WED) == []
        assert extract_temporal_anchors("Đặt lịch thứ tư vấn khách hàng", reference_now=REF_WED) == []

        # Dải nhiều thứ trong cùng 1 span -> iso_weekday=None (tránh đoán 1 ngày duy nhất)
        multi = extract_temporal_anchors("Từ thứ 2 đến thứ 5 19:00-21:00", reference_now=REF_WED)
        assert len(multi) == 1
        assert multi[0].iso_weekday is None
        assert multi[0].start_local == "19:00"
        assert multi[0].end_local == "21:00"


class TestTemporalParserDatesAndConflicts:
    def test_explicit_and_relative_dates(self) -> None:
        anchors_text = extract_temporal_anchors(
            "Hạn ngày 20 tháng 11 năm 2026 lúc 22:00", reference_now=REF_WED
        )
        assert len(anchors_text) == 1
        assert anchors_text[0].explicit_date == "2026-11-20"
        assert anchors_text[0].resolved_date == "2026-11-20"
        assert anchors_text[0].start_local == "22:00"

        anchors_short = extract_temporal_anchors("Nộp 20/11 lúc 18:00", reference_now=REF_WED)
        assert len(anchors_short) == 1
        assert anchors_short[0].explicit_date == "2026-11-20"
        assert anchors_short[0].resolved_date == "2026-11-20"
        assert anchors_short[0].start_local == "18:00"

        anchors_iso = extract_temporal_anchors("Mốc 2026-11-25 09:00", reference_now=REF_WED)
        assert anchors_iso[0].explicit_date == "2026-11-25"

        # Qua giao thừa tháng 12 -> tháng 1 tự tăng năm
        dec_now = datetime(2026, 12, 20, 9, 0, tzinfo=VN_TZ)
        rollover = extract_temporal_anchors("Rảnh 5/1 19:00-21:00", reference_now=dec_now)
        assert rollover[0].explicit_date == "2027-01-05"

    def test_relative_deictic_and_week_markers(self) -> None:
        today_cases = ("hôm nay", "sáng nay", "chiều nay", "tối nay")
        for phrase in today_cases:
            anchors = extract_temporal_anchors(f"{phrase} 19:00-21:00", reference_now=REF_WED)
            assert len(anchors) == 1
            assert anchors[0].is_single_day_deictic is True
            assert anchors[0].resolved_date == "2026-11-18"

        mai = extract_temporal_anchors("ngày mai 08:00-10:00", reference_now=REF_WED)
        assert len(mai) == 1
        assert mai[0].is_single_day_deictic is True
        assert mai[0].resolved_date == "2026-11-19"

        mot = extract_temporal_anchors("mốt 14:00-16:00", reference_now=REF_WED)
        assert len(mot) == 1
        assert mot[0].is_single_day_deictic is True
        assert mot[0].resolved_date == "2026-11-20"

        next_week = extract_temporal_anchors("Tuần sau thứ 3 19:00-21:00", reference_now=REF_WED)
        assert len(next_week) == 1
        assert next_week[0].is_next_week is True
        assert next_week[0].iso_weekday == 2
        # Thứ 3 tuần kế tiếp của tuần Wed 18/11 (Mon 23 -> Tue 24)
        assert next_week[0].resolved_date == "2026-11-24"

        weekend = extract_temporal_anchors("Cuối tuần 09:00-11:00", reference_now=REF_WED)
        assert len(weekend) == 1
        assert weekend[0].resolved_date is None

    def test_weekday_date_conflict_detection(self) -> None:
        # 2026-11-20 là Thứ 6 (iso_weekday=5)
        match_ok = extract_temporal_anchors("Thứ 6 ngày 20/11/2026 19:00-21:00", reference_now=REF_WED)
        assert len(match_ok) == 1
        assert match_ok[0].weekday_date_conflict is False
        assert match_ok[0].iso_weekday == 5
        assert match_ok[0].explicit_date == "2026-11-20"

        conflict = extract_temporal_anchors("Thứ 2 ngày 20/11/2026 19:00-21:00", reference_now=REF_WED)
        assert len(conflict) == 1
        assert conflict[0].weekday_date_conflict is True
        assert conflict[0].iso_weekday == 1
        assert conflict[0].explicit_date == "2026-11-20"


class TestTemporalParserClockPresenceAndClauses:
    def test_clock_presence_and_period_resolution(self) -> None:
        no_clock = extract_temporal_anchors("Rảnh tối mai để làm bài", reference_now=REF_WED)
        assert len(no_clock) == 1
        assert no_clock[0].period == "tối"
        assert no_clock[0].has_explicit_clock_time is False
        assert no_clock[0].start_local is None
        assert no_clock[0].end_local is None

        approx = extract_temporal_anchors("7:00 đến khoảng 9:30 tối", reference_now=REF_WED)
        assert len(approx) == 1
        assert approx[0].has_explicit_clock_time is True
        assert approx[0].start_local == "19:00"
        assert approx[0].end_local == "21:30"

        kem = extract_temporal_anchors("Nộp bài ngày mai lúc 8:00 kém 15 tối", reference_now=REF_WED)
        assert len(kem) == 1
        assert kem[0].has_explicit_clock_time is True
        assert kem[0].start_local == "19:45"

        # "2 giờ" là thời lượng (làm 2 giờ), KHÔNG phải 02:00 sáng
        duration_only = extract_temporal_anchors("Làm 2 giờ tối nay", reference_now=REF_WED)
        assert len(duration_only) == 1
        assert duration_only[0].has_explicit_clock_time is False
        assert duration_only[0].start_local is None

    def test_clause_offsets_and_utc_reference_conversion(self) -> None:
        text = "Rảnh tối mai 19:00-21:00, hạn Thứ 6 20/11 lúc 22:00"
        utc_ref = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)  # 10:00+07:00
        anchors = extract_temporal_anchors(text, reference_now=utc_ref, timezone="Asia/Ho_Chi_Minh")
        assert len(anchors) == 2
        assert anchors[0].resolved_date == "2026-11-19"
        assert anchors[0].start_local == "19:00"
        assert anchors[0].end_local == "21:00"
        assert text[anchors[0].span_start : anchors[0].span_end] == anchors[0].raw_span

        assert anchors[1].iso_weekday == 5
        assert anchors[1].resolved_date == "2026-11-20"
        assert anchors[1].start_local == "22:00"
        assert anchors[1].weekday_date_conflict is False
        assert text[anchors[1].span_start : anchors[1].span_end] == anchors[1].raw_span

    def test_slash_fractions_ratios_and_24_7_filtered_unless_calendar_cued(self) -> None:
        non_date_inputs = (
            "Hỗ trợ hệ thống 24/7",
            "Đạt 8/10% chỉ tiêu",
            "Tăng trưởng 3/4%",
            "Giải 3/4 bài tập về nhà",
            "Đọc 2/3 chương đầu",
            "Làm 3/4 phần báo cáo",
            "Viết 4/5 trang nháp",
            "Hoàn thành 3/4",
            "Điểm 9/10 môn Toán",
            "Tỷ lệ 3/5",
            "Làm 3/4 giờ tối nay",
        )
        for text in non_date_inputs:
            anchors = extract_temporal_anchors(text, reference_now=REF_WED)
            for a in anchors:
                assert a.explicit_date is None, (text, a)
                assert a.start_local is None, (text, a)

        # Có từ khóa lịch đứng ngay trước hoặc có năm đầy đủ -> nhận đúng ngày lịch
        cal_cued = (
            ("Nộp ngày 3/4 lúc 20:00", "2026-04-03", "20:00"),
            ("Hạn 24/7 lúc 18:00", "2026-07-24", "18:00"),
            ("Làm vào 20/11 lúc 09:00", "2026-11-20", "09:00"),
            ("3/4/2026 lúc 14:00", "2026-04-03", "14:00"),
        )
        for text, exp_date, exp_start in cal_cued:
            anchors = extract_temporal_anchors(text, reference_now=REF_WED)
            assert len(anchors) == 1, (text, anchors)
            assert anchors[0].explicit_date == exp_date, text
            assert anchors[0].start_local == exp_start, text

    def test_conjunction_clause_split_prevents_period_leakage(self) -> None:
        a1 = extract_temporal_anchors("Rảnh sáng 8h-10h và tối 19h-21h", reference_now=REF_WED)
        assert len(a1) == 2
        assert (a1[0].period, a1[0].start_local, a1[0].end_local) == ("sáng", "08:00", "10:00")
        assert (a1[1].period, a1[1].start_local, a1[1].end_local) == ("tối", "19:00", "21:00")

        a2 = extract_temporal_anchors(
            "7 giờ đến 9 giờ sáng và tối 19h-21h", reference_now=REF_WED
        )
        assert len(a2) == 2
        assert (a2[0].period, a2[0].start_local, a2[0].end_local) == ("sáng", "07:00", "09:00")
        assert (a2[1].period, a2[1].start_local, a2[1].end_local) == ("tối", "19:00", "21:00")

        a3 = extract_temporal_anchors("Rảnh tối nay và tối mai 19:00-21:00", reference_now=REF_WED)
        assert len(a3) == 2
        assert a3[0].is_single_day_deictic is True
        assert a3[0].resolved_date == "2026-11-18"
        assert a3[1].is_single_day_deictic is True
        assert a3[1].resolved_date == "2026-11-19"

        # Các câu bắt đầu trực tiếp bằng mốc giờ/thứ/ngày không có từ dẫn ở đầu
        lead_free_8 = extract_temporal_anchors("8:00 và tối 19:00", reference_now=REF_WED)
        assert len(lead_free_8) == 2
        assert (lead_free_8[0].period, lead_free_8[0].start_local) == (None, "08:00")
        assert (lead_free_8[1].period, lead_free_8[1].start_local) == ("tối", "19:00")

        lead_free_7h = extract_temporal_anchors("7h và tối 19h", reference_now=REF_WED)
        assert len(lead_free_7h) == 2
        assert (lead_free_7h[0].period, lead_free_7h[0].has_explicit_clock_time) == (None, True)
        assert (lead_free_7h[1].period, lead_free_7h[1].start_local) == ("tối", "19:00")

        lead_free_wd = extract_temporal_anchors(
            "Thứ 2 và tối Thứ 4 19:00-21:00", reference_now=REF_WED
        )
        assert len(lead_free_wd) == 2
        assert (lead_free_wd[0].iso_weekday, lead_free_wd[0].period) == (1, None)
        assert (
            lead_free_wd[1].iso_weekday,
            lead_free_wd[1].period,
            lead_free_wd[1].start_local,
            lead_free_wd[1].end_local,
        ) == (3, "tối", "19:00", "21:00")

        lead_free_gio = extract_temporal_anchors("7 giờ và tối 19 giờ", reference_now=REF_WED)
        assert len(lead_free_gio) == 2
        assert (lead_free_gio[0].period, lead_free_gio[0].has_explicit_clock_time) == (None, True)
        assert (lead_free_gio[1].period, lead_free_gio[1].start_local) == ("tối", "19:00")

        lead_free_slash = extract_temporal_anchors("20/1 và sáng 21/1", reference_now=REF_WED)
        assert len(lead_free_slash) == 2
        assert (lead_free_slash[0].explicit_date, lead_free_slash[0].period) == ("2027-01-20", None)
        assert (lead_free_slash[1].explicit_date, lead_free_slash[1].period) == (
            "2027-01-21",
            "sáng",
        )

        # Negative checks: không tách khi vế trái không phải tín hiệu thời gian ("tiết 2", "4G")
        non_split_tiet = extract_temporal_anchors("tiết 2 và sáng mai", reference_now=REF_WED)
        assert len(non_split_tiet) == 1
        assert non_split_tiet[0].raw_span == "tiết 2 và sáng mai"

        non_split_4g = extract_temporal_anchors("4G và tối nay", reference_now=REF_WED)
        assert len(non_split_4g) == 1
        assert non_split_4g[0].raw_span == "4G và tối nay"
        assert non_split_4g[0].has_explicit_clock_time is False

    def test_uppercase_G_and_bare_grams_do_not_trigger_clock_false_positives(self) -> None:
        assert extract_temporal_anchors("Đăng ký gói mạng 4G và 5G", reference_now=REF_WED) == []
        assert extract_temporal_anchors("Cân 20g bột và 5g muối", reference_now=REF_WED) == []

        g5_evening = extract_temporal_anchors("Test mạng 5G tối nay", reference_now=REF_WED)
        assert len(g5_evening) == 1
        assert g5_evening[0].has_explicit_clock_time is False
        assert g5_evening[0].start_local is None

        grams_then_clock = extract_temporal_anchors(
            "Pha 20g bột lúc 08:30 sáng mai", reference_now=REF_WED
        )
        assert len(grams_then_clock) == 1
        assert grams_then_clock[0].start_local == "08:30"
        assert grams_then_clock[0].has_explicit_clock_time is True

        cued_g = extract_temporal_anchors("Họp ngày mai lúc 20g", reference_now=REF_WED)
        assert len(cued_g) == 1
        assert cued_g[0].start_local == "20:00"
        assert cued_g[0].has_explicit_clock_time is True

    def test_extended_ordinals_noon_range_and_leading_period_kem(self) -> None:
        more_ordinals = (
            "Chọn phương án thứ 2",
            "Trình bày ý thứ 3",
            "Xếp hạng thứ 4",
            "Đọc tập thứ 5",
            "Giữ vị trí thứ 6",
        )
        for text in more_ordinals:
            assert extract_temporal_anchors(text, reference_now=REF_WED) == [], text

        noon_range = extract_temporal_anchors("Rảnh trưa 12:00 đến 2:00", reference_now=REF_WED)
        assert len(noon_range) == 1
        assert (noon_range[0].start_local, noon_range[0].end_local) == ("12:00", "14:00")

        cross_period = extract_temporal_anchors(
            "Rảnh từ 11:00 đến tối 20:00", reference_now=REF_WED
        )
        assert len(cross_period) == 1
        assert (cross_period[0].start_local, cross_period[0].end_local) == ("11:00", "20:00")

        assert _apply_period_to_hour(12, "tối", is_end=False) == 12
        assert _apply_period_to_hour(12, "tối", is_end=True) == 23

        lead_kem = extract_temporal_anchors("Họp tối mai 8 giờ kém 15", reference_now=REF_WED)
        assert len(lead_kem) == 1
        assert lead_kem[0].start_local == "19:45"
        assert lead_kem[0].has_explicit_clock_time is True
