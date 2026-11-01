import 'dart:io' show Platform;
import 'dart:math';

import 'package:flutter/foundation.dart';

class ApiConfig {
  ApiConfig._();

  /// Địa chỉ Backend API tự động thích ứng với nền tảng:
  /// - Android Emulator: 10.0.2.2:8000/api/v1
  /// - Windows / Web / iOS Simulator: 127.0.0.1:8000/api/v1
  static String get defaultBaseUrl {
    if (kIsWeb) return 'http://127.0.0.1:8000/api/v1';
    try {
      if (Platform.isAndroid) {
        return 'http://10.0.2.2:8000/api/v1';
      }
    } catch (_) {
      // Fallback for environments where Platform is unsupported
    }
    return 'http://127.0.0.1:8000/api/v1';
  }

  static String baseUrl = defaultBaseUrl;

  /// Thời gian chờ kết nối (5 giây)
  static const Duration connectTimeout = Duration(seconds: 5);

  /// Thời gian chờ nhận phản hồi (8 giây - bao quát trần Hedged Execution 6.0s)
  static const Duration receiveTimeout = Duration(seconds: 8);

  /// Sinh mã Idempotency-Key chống gửi trùng cho các lệnh POST/PATCH
  static String generateIdempotencyKey(String prefix) {
    final timestamp = DateTime.now().millisecondsSinceEpoch;
    final randomSuffix = Random().nextInt(999999).toString().padLeft(6, '0');
    return '${prefix}_${timestamp}_$randomSuffix';
  }
}
