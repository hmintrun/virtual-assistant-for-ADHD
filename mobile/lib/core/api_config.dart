import 'dart:math';

class ApiConfig {
  ApiConfig._();

  /// Địa chỉ Backend API:
  /// - Hỗ trợ truyền qua --dart-define=API_BASE_URL=http://...
  /// - Mặc định cho thiết bị thật (qua cáp USB với adb reverse tcp:8000 tcp:8000) và Desktop/Web: 127.0.0.1:8000
  static String get defaultBaseUrl {
    const envUrl = String.fromEnvironment('API_BASE_URL');
    if (envUrl.isNotEmpty) return envUrl;
    return 'http://127.0.0.1:8000/api/v1';
  }

  static String baseUrl = defaultBaseUrl;

  /// Thời gian chờ kết nối (5 giây)
  static const Duration connectTimeout = Duration(seconds: 5);

  /// Thời gian chờ nhận phản hồi (25 giây - bao quát trần Hedged Execution và mạng di động)
  static const Duration receiveTimeout = Duration(seconds: 25);

  /// Sinh mã Idempotency-Key chống gửi trùng cho các lệnh POST/PATCH
  static String generateIdempotencyKey(String prefix) {
    final timestamp = DateTime.now().millisecondsSinceEpoch;
    final randomSuffix = Random().nextInt(999999).toString().padLeft(6, '0');
    return '${prefix}_${timestamp}_$randomSuffix';
  }
}
