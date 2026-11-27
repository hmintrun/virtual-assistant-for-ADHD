// W3-15: khi không đọc được Google Calendar (thu hồi quyền, mất mạng), app
// KHÔNG âm thầm coi là rảnh. User thấy tình trạng nguồn và tự chọn xếp theo
// giờ rảnh đã nhập tay.
import 'package:flutter/material.dart';

import '../core/api_client.dart';
import '../services/calendar_api.dart';

bool isCalendarUnavailable(Object error) =>
    error is ApiException &&
    (error.code == CalendarApi.calendarUnavailableCode ||
        error.requiresCalendarReconnect);

void showCalendarFallbackSnackBar(
  BuildContext context, {
  required VoidCallback onUseManualAvailability,
}) {
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(
      duration: const Duration(seconds: 10),
      content: const Text(
        'Chưa đọc được Google Calendar (mất quyền hoặc mất mạng). '
        'Kết nối lại trong Tài khoản, hoặc xếp theo giờ rảnh bạn đã nhập.',
      ),
      action: SnackBarAction(
        label: 'Dùng giờ rảnh nhập tay',
        onPressed: onUseManualAvailability,
      ),
    ),
  );
}
