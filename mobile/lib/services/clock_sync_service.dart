import 'dart:async';
import 'package:dio/dio.dart';

import '../core/api_client.dart';

/// Kết quả đồng bộ và bù lệch đồng hồ với máy chủ.
class ClockSyncResult {
  const ClockSyncResult({
    required this.offsetMs,
    required this.rttMs,
    required this.serverTimeEstimated,
    required this.syncedAt,
  });

  /// Độ lệch mili-giây giữa máy chủ và thiết bị (Server - Device).
  final int offsetMs;

  /// Thời gian khứ hồi mạng (Round-Trip Time) tính bằng mili-giây.
  final int rttMs;

  /// Ước lượng thời điểm hiện tại của máy chủ tại mốc hoàn tất yêu cầu.
  final DateTime serverTimeEstimated;

  /// Thời điểm cục bộ khi đo xong.
  final DateTime syncedAt;

  @override
  String toString() =>
      'ClockSyncResult(offsetMs: $offsetMs, rttMs: $rttMs, serverTime: $serverTimeEstimated)';
}

/// Dịch vụ đo và bù lệch thời gian thực giữa thiết bị di động và máy chủ backend.
///
/// Ngăn ngừa triệt để các lỗi logic thứ tự thời gian như:
/// - `responded_at < asked_at` trong Check-In (UF5).
/// - Báo thức Android Doze Mode trôi giây làm gián đoạn trải nghiệm tập trung.
class ClockSyncService {
  ClockSyncService({DateTime Function()? clock}) : _clock = clock ?? DateTime.now;

  static final ClockSyncService instance = ClockSyncService();

  final DateTime Function() _clock;

  int _offsetMs = 0;
  DateTime? _lastSyncAt;

  /// Độ lệch đồng hồ hiện tại (mili-giây). Dương nếu server nhanh hơn thiết bị.
  int get clockOffsetMs => _offsetMs;

  /// Mốc thời gian đồng bộ gần nhất.
  DateTime? get lastSyncAt => _lastSyncAt;

  /// Cập nhật thủ công độ lệch đồng hồ (dùng cho Unit Test hoặc Mocking).
  void setOffsetMs(int offsetMs) {
    _offsetMs = offsetMs;
    _lastSyncAt = _clock().toUtc();
  }

  /// Trả về thời gian hiện tại đã được bù lệch chuẩn theo giờ máy chủ (luôn ở chuẩn UTC).
  DateTime nowAdjusted([DateTime? deviceNow]) {
    final base = (deviceNow ?? _clock()).toUtc();
    return base.add(Duration(milliseconds: _offsetMs));
  }

  /// Định dạng chuỗi ISO-8601 có múi giờ chuẩn UTC của thời gian đã bù lệch.
  String nowAdjustedIso([DateTime? deviceNow]) {
    return nowAdjusted(deviceNow).toIso8601String();
  }

  /// Thực hiện đo RTT và bù lệch thời gian qua endpoint `/capabilities` của máy chủ.
  Future<ClockSyncResult> syncWithServer(
    ApiClient client, {
    String endpoint = '/capabilities',
  }) async {
    final t0 = _clock().toUtc();
    Response<Map<String, dynamic>> response;
    try {
      response = await client.dio.get<Map<String, dynamic>>(endpoint);
    } on DioException catch (e) {
      throw ApiException.fromDio(e);
    }
    final t1 = _clock().toUtc();

    final data = response.data;
    final meta = data?['meta'] as Map<String, dynamic>?;
    String? rawServerTime = meta?['server_time'] as String? ?? data?['server_time'] as String?;
    if (rawServerTime == null && data != null && data['data'] is Map<String, dynamic>) {
      rawServerTime = (data['data'] as Map<String, dynamic>)['server_time'] as String?;
    }

    if (rawServerTime == null) {
      throw const ApiException('Phản hồi từ máy chủ thiếu trường meta.server_time.');
    }

    final serverTime = DateTime.parse(rawServerTime).toUtc();
    final rttMs = t1.difference(t0).inMilliseconds;

    // Ước lượng server_time tại mốc t1: server_time + (RTT / 2)
    final serverTimeEstimated = serverTime.add(Duration(milliseconds: (rttMs / 2).round()));

    // Offset = ServerTimeEstimated - t1
    _offsetMs = serverTimeEstimated.difference(t1).inMilliseconds;
    _lastSyncAt = t1;

    return ClockSyncResult(
      offsetMs: _offsetMs,
      rttMs: rttMs,
      serverTimeEstimated: serverTimeEstimated,
      syncedAt: t1,
    );
  }
}
