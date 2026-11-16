import 'dart:math';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'api_session.dart';

/// Dio client for the FastAPI v1 envelope contract.
class ApiClient {
  ApiClient({Dio? dio, bool bindSession = true})
      : _session = bindSession ? ApiSession.current : null,
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
          onRequest: (options, handler) {
            final session = _session;
            if (session != null && !session.active) {
              handler.reject(DioException(
                  requestOptions: options, type: DioExceptionType.cancel));
              return;
            }
            final token = session?.token;
            if (token != null)
              options.headers['Authorization'] = 'Bearer $token';
            handler.next(options);
          },
          onResponse: (response, handler) {
            if (_session?.active == false) {
              handler.reject(DioException(
                requestOptions: response.requestOptions,
                type: DioExceptionType.cancel,
              ));
              return;
            }
            handler.next(response);
          },
          onError: (error, handler) async {
            final session = _session;
            final request = error.requestOptions;
            if (session == null ||
                !session.active ||
                error.response?.statusCode != 401 ||
                _isCalendarAuthorizationError(error) ||
                request.extra['auth_retried'] == true) {
              handler.next(error);
              return;
            }
            final sentToken = request.headers['Authorization'];
            final tokenChanged =
                session.token != null && sentToken != 'Bearer ${session.token}';
            final renewed =
                tokenChanged || (await session.renew?.call() ?? false);
            if (!renewed || !session.active) {
              if (session.active) session.onUnauthorized?.call();
              handler.next(error);
              return;
            }
            try {
              handler.resolve(await this.dio.fetch<dynamic>(request.copyWith(
                    data: request.data is FormData
                        ? (request.data as FormData).clone()
                        : request.data,
                    extra: {...request.extra, 'auth_retried': true},
                  )));
            } on DioException catch (retryError) {
              if (session.active &&
                  retryError.response?.statusCode == 401 &&
                  !_isCalendarAuthorizationError(retryError)) {
                session.onUnauthorized?.call();
              }
              handler.next(retryError);
            }
          },
        ));
  }

  static bool _isCalendarAuthorizationError(DioException error) {
    final body = error.response?.data;
    if (body is! Map) return false;
    final details = body['error'];
    return details is Map && details['code'] == 'CALENDAR_AUTH_REQUIRED';
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
  final ApiSession? _session;

  static String newIdempotencyKey(String operation) {
    final secureRandom = Random.secure();
    // Combine two 16-bit values without a 32-bit shift on Dart web.
    final random = List.generate(
      2,
      (_) => secureRandom.nextInt(0x10000).toRadixString(16).padLeft(4, '0'),
    ).join();
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

  String? get ownerId => _session?.active == true ? _session!.ownerId : null;

  Future<Map<String, dynamic>?> getOptionalData(String path) async {
    try {
      final response = await dio.get<Map<String, dynamic>>(path);
      final body = response.data;
      if (body != null && body.containsKey('data') && body['data'] == null) {
        return null;
      }
      return _unwrap(body);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<List<Map<String, dynamic>>> getListData(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    try {
      final response = await dio.get<Map<String, dynamic>>(
        path,
        queryParameters: queryParameters,
      );
      final items = response.data?['data'];
      if (items is! List) {
        throw const ApiException('Backend trả về danh sách không hợp lệ.');
      }
      return items.whereType<Map<String, dynamic>>().toList(growable: false);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<Map<String, dynamic>> postData(
    String path, {
    Object? data,
    String? idempotencyKey,
    bool idempotent = true,
  }) async {
    try {
      final response = await dio.post<Map<String, dynamic>>(
        path,
        data: data,
        options: Options(
            receiveTimeout: path == '/voice/transcriptions'
                ? const Duration(seconds: 45)
                : null,
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
  }) async {
    try {
      final response = await dio.patch<Map<String, dynamic>>(
        path,
        data: data,
        options: Options(headers: {
          'Idempotency-Key':
              idempotencyKey ?? newIdempotencyKey('mobile_patch'),
        }),
      );
      return _unwrap(response.data);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<Map<String, dynamic>> putData(
    String path, {
    required Object data,
    String? idempotencyKey,
  }) async {
    try {
      final response = await dio.put<Map<String, dynamic>>(
        path,
        data: data,
        options: Options(headers: {
          'Idempotency-Key': idempotencyKey ?? newIdempotencyKey('mobile_put'),
        }),
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
    final legacyDetail = body is Map<String, dynamic> ? body['detail'] : null;
    final serverMessage = detail?['message'] as String? ??
        (legacyDetail is String ? legacyDetail : null);
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
