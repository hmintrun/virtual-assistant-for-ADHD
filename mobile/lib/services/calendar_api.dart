// Gọi API Google Calendar của Backend: kết nối, chọn lịch, đọc giờ bận.
// App KHÔNG giữ token Google — token nằm ở Backend, app chỉ dùng connection_id.
import 'package:dio/dio.dart';

import '../core/api_client.dart';

/// Một lịch trong tài khoản Google (Lịch chính, Lịch học, ...).
class CalendarItem {
  const CalendarItem({
    required this.id,
    required this.summary,
    required this.primary,
    required this.accessRole,
    required this.selectedForRead,
    required this.selectedForWrite,
  });

  final String id;
  final String summary;
  final bool primary;
  final String accessRole;
  final bool selectedForRead;
  final bool selectedForWrite;

  /// Chỉ ghi được vào lịch mình sở hữu hoặc có quyền sửa.
  bool get canWrite => accessRole == 'owner' || accessRole == 'writer';

  factory CalendarItem.fromJson(Map<String, dynamic> json) => CalendarItem(
        id: json['id'] as String,
        summary: json['summary'] as String? ?? '(Không tên)',
        primary: json['primary'] as bool? ?? false,
        accessRole: json['access_role'] as String? ?? 'reader',
        selectedForRead: json['selected_for_read'] as bool? ?? false,
        selectedForWrite: json['selected_for_write'] as bool? ?? false,
      );
}

/// Một tài khoản Google đã kết nối với app.
class CalendarConnection {
  const CalendarConnection({
    required this.connectionId,
    required this.accountRef,
    required this.state,
    required this.readCalendarIds,
    required this.writeCalendarId,
  });

  final String connectionId;
  final String accountRef;
  final String state;
  final List<String> readCalendarIds;
  final String? writeCalendarId;

  factory CalendarConnection.fromJson(Map<String, dynamic> json) =>
      CalendarConnection(
        connectionId: json['connection_id'] as String,
        accountRef: json['account_ref'] as String? ?? '',
        state: json['connection_state'] as String? ?? 'unknown',
        readCalendarIds:
            List<String>.from(json['read_calendar_ids'] as List? ?? const []),
        writeCalendarId: json['write_calendar_id'] as String?,
      );
}

/// Một sự kiện bận đọc từ Google (đã được Backend chuẩn hóa).
class CalendarBusyEvent {
  const CalendarBusyEvent({
    required this.googleCalendarId,
    required this.title,
    required this.startAt,
    required this.endAt,
  });

  final String googleCalendarId;
  final String? title;
  final DateTime startAt;
  final DateTime endAt;

  factory CalendarBusyEvent.fromJson(Map<String, dynamic> json) =>
      CalendarBusyEvent(
        googleCalendarId: json['google_calendar_id'] as String,
        title: json['title'] as String?,
        startAt: DateTime.parse(json['start_at'] as String).toLocal(),
        endAt: DateTime.parse(json['end_at'] as String).toLocal(),
      );
}

class CalendarApi {
  CalendarApi({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;

  /// Deep link Backend redirect về sau khi user cấp quyền xong.
  static const String returnTarget = 'proplan://calendar/connected';

  /// Mã lỗi Backend khi không đọc được lịch đã chọn (thu hồi quyền, mất mạng).
  static const String calendarUnavailableCode = 'CALENDAR_UNAVAILABLE';

  static const String manualFallbackReason =
      'Không đọc được Google Calendar; user chọn xếp theo giờ rảnh nhập tay.';

  /// B1: xin URL trang đồng ý của Google. App mở URL này bằng trình duyệt.
  Future<Uri> startAuthorization() async {
    final data = await _api.postData(
      '/calendar/authorizations',
      data: {'device_id': 'android'},
    );
    return Uri.parse(data['auth_url'] as String);
  }

  /// Các tài khoản đã kết nối (rỗng = chưa kết nối).
  Future<List<CalendarConnection>> listConnections() async {
    final items = await _getList('/calendar/connections');
    return items.map(CalendarConnection.fromJson).toList();
  }

  /// B2: danh sách lịch trong tài khoản để user chọn.
  Future<List<CalendarItem>> listCalendars(String connectionId) async {
    final data =
        await _api.getData('/calendar/connections/$connectionId/calendars');
    final items = data['items'] as List? ?? const [];
    return items
        .map((e) => CalendarItem.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// B3: lưu lựa chọn lịch đọc giờ bận + lịch ghi phiên.
  /// Backend bắt 2 bước (xem trước → xác nhận) để không đổi cấu hình nhầm.
  Future<CalendarConnection> saveSelection({
    required String connectionId,
    required List<String> readCalendarIds,
    String? writeCalendarId,
  }) async {
    final preview = await _api.postData(
      '/calendar/connections/$connectionId/configuration-previews',
      data: {
        'read_calendar_ids': readCalendarIds,
        'write_calendar_id': writeCalendarId,
      },
    );
    final confirmed = await _api.postData(
      '/calendar/configuration-previews/${preview['preview_id']}/confirm',
      data: {
        if (preview['preview_revision'] case final int revision)
          'expected_preview_revision': revision,
      },
    );
    return CalendarConnection.fromJson(confirmed);
  }

  /// B4: đọc sự kiện bận của 1 lịch trong khoảng [from, to).
  Future<List<CalendarBusyEvent>> listBusyEvents({
    required String connectionId,
    required String googleCalendarId,
    required DateTime from,
    required DateTime to,
  }) async {
    final items = await _getList(
      '/calendar/connections/$connectionId/events',
      queryParameters: {
        'google_calendar_id': googleCalendarId,
        // Gửi UTC có hậu tố Z để Backend không phải đoán múi giờ.
        'from': from.toUtc().toIso8601String(),
        'to': to.toUtc().toIso8601String(),
      },
    );
    return items.map(CalendarBusyEvent.fromJson).toList();
  }

  /// Ngắt kết nối: Backend thôi đọc/ghi lịch của tài khoản này. Kết nối lại
  /// bằng [startAuthorization] như lần đầu.
  Future<void> disconnect(String connectionId) async {
    await _api.postData(
      '/calendar/connections/$connectionId/disconnect',
      data: const {},
    );
  }

  /// UF2: chụp lịch bận của các lịch đã chọn cho một Draft, để thuật toán
  /// Python loại các khe bận khi xếp phiên.
  ///
  /// Trả về null khi user chưa chọn lịch đọc nào → Backend xếp theo giờ rảnh
  /// nhập tay như cũ. Lỗi đọc lịch ném [ApiException] với code
  /// [calendarUnavailableCode] (KHÔNG coi là "rảnh").
  ///
  /// [useManualAvailability] = true khi user đã bấm "Xếp theo giờ rảnh nhập
  /// tay" sau lỗi đọc lịch: mọi nguồn được gửi kèm trong `excluded_sources`
  /// cùng lý do và thời điểm user xác nhận, Backend xếp theo giờ rảnh đã nhập.
  Future<Map<String, dynamic>?> createPlanningContextForDraft({
    required String draftId,
    required int summaryRevision,
    required DateTime from,
    required DateTime to,
    bool useManualAvailability = false,
  }) async {
    final sources = [
      for (final connection in await listConnections())
        // needs_reconnect (quyền bị thu hồi/hết hạn) vẫn là nguồn đã chọn:
        // gửi lên để Backend báo lỗi rõ thay vì âm thầm coi là rảnh.
        if (connection.state != 'disconnected')
          for (final calendarId in connection.readCalendarIds)
            {
              'connection_id': connection.connectionId,
              'google_calendar_id': calendarId,
            },
    ];
    if (sources.isEmpty) return null;
    final confirmedAt = DateTime.now().toUtc().toIso8601String();
    final context = await _api.postData(
      '/planning-contexts',
      data: {
        'source_draft_id': draftId,
        'expected_summary_revision': summaryRevision,
        'from': from.toUtc().toIso8601String(),
        'to': to.toUtc().toIso8601String(),
        'selected_calendar_sources': sources,
        if (useManualAvailability)
          'excluded_sources': [
            for (final source in sources)
              {
                ...source,
                'reason': manualFallbackReason,
                'user_confirmed_at': confirmedAt,
              },
          ],
      },
    );
    return {
      'context_id': context['context_id'] as String,
      'revision': context['revision'] as int,
    };
  }

  /// ApiClient.getData chỉ nhận `data` là Map; vài endpoint trả `data` là List.
  Future<List<Map<String, dynamic>>> _getList(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    try {
      final response = await _api.dio.get<Map<String, dynamic>>(
        path,
        queryParameters: queryParameters,
      );
      final data = response.data?['data'];
      if (data is! List) {
        throw const ApiException('Backend trả về dữ liệu không đúng envelope.');
      }
      return data.cast<Map<String, dynamic>>();
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }
}
