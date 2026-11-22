import 'dart:math';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Dio client for the FastAPI v1 envelope contract.
class ApiClient {
  ApiClient({Dio? dio, FlutterSecureStorage? secureStorage})
      : _storage = secureStorage ?? const FlutterSecureStorage(),
        dio = dio ??
            Dio(BaseOptions(
              baseUrl: _baseUrl,
              connectTimeout: const Duration(seconds: 10),
              receiveTimeout: const Duration(seconds: 20),
              sendTimeout: const Duration(seconds: 20),
              contentType: Headers.jsonContentType,
              headers: const {'Accept': 'application/json'},
            )) {
    this.dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) async {
        final token = await _storage.read(key: 'access_token');
        if (token != null && token.isNotEmpty) {
          options.headers['Authorization'] = 'Bearer $token';
        }
        handler.next(options);
      },
    ));
  }

  static const _configuredBaseUrl = String.fromEnvironment('API_BASE_URL');
  static String get _baseUrl {
    if (_configuredBaseUrl.isNotEmpty) return _configuredBaseUrl;
    if (kIsWeb) return 'http://127.0.0.1:8000/api/v1';
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000/api/v1';
    }
    return 'http://127.0.0.1:8000/api/v1';
  }

  final Dio dio;
  final FlutterSecureStorage _storage;

  static String newIdempotencyKey(String operation) {
    final random = Random.secure().nextInt(1 << 32).toRadixString(16);
    return '${operation}_${DateTime.now().microsecondsSinceEpoch}_$random';
  }

  static String stableIdempotencyKey(String operation, String payload) {
    var hash = 0x811c9dc5;
    for (final unit in payload.codeUnits) {
      hash = ((hash ^ unit) * 0x01000193) & 0xffffffff;
    }
    return '${operation}_${hash.toRadixString(16)}';
  }

  Future<Map<String, dynamic>> getData(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    try {
      final response = await dio.get<Map<String, dynamic>>(
        path,
        queryParameters: queryParameters,
      );
      return _unwrap(response.data);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<Map<String, dynamic>> postData(
    String path, {
    Object? data,
    String? idempotencyKey,
    bool idempotent = true,
    Duration? receiveTimeout,
  }) async {
    try {
      final response = await dio.post<Map<String, dynamic>>(
        path,
        data: data,
        options: Options(
            receiveTimeout: receiveTimeout,
            headers: idempotent
                ? {
                    'Idempotency-Key':
                        idempotencyKey ?? newIdempotencyKey('mobile')
                  }
                : null),
      );
      return _unwrap(response.data);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<Map<String, dynamic>> patchData(
    String path, {
    required Object data,
    String? idempotencyKey,
    Duration? receiveTimeout,
  }) async {
    try {
      final response = await dio.patch<Map<String, dynamic>>(
        path,
        data: data,
        options: Options(
          receiveTimeout: receiveTimeout,
          headers: {
            'Idempotency-Key':
                idempotencyKey ?? newIdempotencyKey('mobile_patch'),
          },
        ),
      );
      return _unwrap(response.data);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Map<String, dynamic> _unwrap(Map<String, dynamic>? body) {
    if (body == null || body['data'] is! Map<String, dynamic>) {
      throw const ApiException('Backend trả về dữ liệu không đúng envelope.');
    }
    return body['data'] as Map<String, dynamic>;
  }
}

class ApiException implements Exception {
  const ApiException(
    this.message, {
    this.code,
    this.statusCode,
    this.fieldErrors = const [],
    this.currentRevisions = const {},
    this.nextAction,
  });

  final String message;
  final String? code;
  final int? statusCode;
  final List<Map<String, dynamic>> fieldErrors;
  final Map<String, int> currentRevisions;
  final String? nextAction;

  factory ApiException.fromDio(DioException error) {
    final body = error.response?.data;
    final detail = body is Map<String, dynamic>
        ? body['error'] as Map<String, dynamic>?
        : null;
    final serverMessage = detail?['message'] as String?;
    final message = serverMessage ??
        switch (error.type) {
          DioExceptionType.connectionTimeout =>
            'Kết nối đến máy chủ quá lâu. Kiểm tra mạng và thử lại.',
          DioExceptionType.sendTimeout =>
            'Gửi yêu cầu quá lâu. Kiểm tra mạng rồi thử lại.',
          DioExceptionType.receiveTimeout =>
            'Máy chủ phản hồi quá lâu. Bạn có thể thử tải lại sau.',
          DioExceptionType.connectionError =>
            'Chưa kết nối được máy chủ. Kiểm tra backend, mạng và địa chỉ API.',
          DioExceptionType.badCertificate =>
            'Kết nối bảo mật tới máy chủ không thành công.',
          _ => error.message ??
              'Không thể kết nối backend. Hãy kiểm tra server và địa chỉ API.',
        };
    return ApiException(
      message,
      code: detail?['code'] as String?,
      statusCode: error.response?.statusCode,
      fieldErrors: (detail?['field_errors'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .toList(growable: false),
      currentRevisions: Map<String, int>.from(
        detail?['current_revisions'] as Map<String, dynamic>? ?? const {},
      ),
      nextAction: detail?['next_action'] as String?,
    );
  }

  @override
  String toString() => message;
}
