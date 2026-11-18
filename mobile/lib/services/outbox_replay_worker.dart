import 'dart:async';
import 'dart:math';
import 'package:dio/dio.dart';

import '../core/api_client.dart';
import '../core/api_session.dart';
import '../models/outbox_record.dart';
import '../repositories/local_outbox_repository.dart';

/// Báo cáo tổng kết sau một đợt replay hàng đợi outbox ngoại tuyến.
class ReplayReport {
  const ReplayReport({
    required this.totalProcessed,
    this.syncedIds = const [],
    this.conflictIds = const [],
    this.terminalFailedIds = const [],
    this.transientFailedIds = const [],
    this.isPausedAuth = false,
  });

  final int totalProcessed;
  final List<String> syncedIds;
  final List<String> conflictIds;
  final List<String> terminalFailedIds;
  final List<String> transientFailedIds;
  final bool isPausedAuth;

  @override
  String toString() =>
      'ReplayReport(processed: $totalProcessed, synced: ${syncedIds.length}, '
      'conflicts: ${conflictIds.length}, terminal: ${terminalFailedIds.length}, '
      'transient: ${transientFailedIds.length}, pausedAuth: $isPausedAuth)';
}

/// Worker xử lý phát lại (replay) tuần tự FIFO các yêu cầu ngoại tuyến khi có mạng.
///
/// Tuân thủ nghiêm ngặt các nguyên tắc:
/// - Đính kèm header `Idempotency-Key` bảo đảm tính lũy đẳng (Zero Duplicate Execution).
/// - Xử lý lỗi 422/404/400 (Terminal) đưa vào dead-letter để không làm tắc nghẽn hàng đợi (No Head-of-Line Blocking).
/// - Tạm dừng khi gặp 401 chờ renew token của session hiện hành.
/// - Áp dụng Exponential Backoff ($2^n \times 2$ giây, tối đa 60s) cho lỗi mạng và 5xx.
class OutboxReplayWorker {
  OutboxReplayWorker({
    required LocalOutboxRepository repository,
    required ApiClient apiClient,
  })  : _repository = repository,
        _apiClient = apiClient;

  final LocalOutboxRepository _repository;
  final ApiClient _apiClient;

  bool _isReplaying = false;
  bool _isPausedAuth = false;
  Timer? _periodicTimer;

  bool get isReplaying => _isReplaying;
  bool get isPausedAuth => _isPausedAuth;

  /// Bắt đầu lắng nghe và kiểm tra định kỳ hàng đợi outbox (mỗi 15 giây).
  void startListening({Duration interval = const Duration(seconds: 15)}) {
    _periodicTimer?.cancel();
    _periodicTimer = Timer.periodic(interval, (_) => triggerReplay());
  }

  /// Dừng kiểm tra định kỳ.
  void stopListening() {
    _periodicTimer?.cancel();
    _periodicTimer = null;
  }

  /// Bỏ cờ tạm dừng xác thực khi người dùng vừa đăng nhập lại hoặc gia hạn token thành công.
  void resumeAuth() {
    _isPausedAuth = false;
  }

  /// Kích hoạt một chu kỳ replay các bản ghi sẵn sàng trong hàng đợi outbox.
  Future<ReplayReport> triggerReplay({int batchLimit = 20, DateTime? asOf}) async {
    if (_isReplaying) {
      return const ReplayReport(totalProcessed: 0);
    }

    if (_isPausedAuth) {
      final session = ApiSession.current;
      if (session != null && session.active) {
        final renewed = await session.renew?.call() ?? false;
        if (renewed) {
          _isPausedAuth = false;
        } else {
          return const ReplayReport(totalProcessed: 0, isPausedAuth: true);
        }
      } else {
        return const ReplayReport(totalProcessed: 0, isPausedAuth: true);
      }
    }

    _isReplaying = true;

    final syncedIds = <String>[];
    final conflictIds = <String>[];
    final terminalFailedIds = <String>[];
    final transientFailedIds = <String>[];

    try {
      final nowUtc = (asOf ?? DateTime.now()).toUtc();
      final batch = await _repository.fetchPendingBatch(
        limit: batchLimit,
        asOf: nowUtc,
      );

      if (batch.isEmpty) {
        return const ReplayReport(totalProcessed: 0);
      }

      for (final record in batch) {
        // Kiểm tra xem session có đổi chủ không
        final session = ApiSession.current;
        if (session != null && !session.active) {
          _isPausedAuth = true;
          break;
        }

        try {
          await _sendRecord(record);
          await _repository.markSynced(record.clientEventId);
          syncedIds.add(record.clientEventId);
        } on DioException catch (dioError) {
          final statusCode = dioError.response?.statusCode;
          final errorMessage = dioError.message ?? dioError.toString();
          final isConnectionError = dioError.type == DioExceptionType.connectionTimeout ||
              dioError.type == DioExceptionType.sendTimeout ||
              dioError.type == DioExceptionType.receiveTimeout ||
              dioError.type == DioExceptionType.connectionError;

          if (statusCode == 401) {
            _isPausedAuth = true;
            // Kích hoạt renew token nhưng không tăng retry count của bản ghi
            final renewed = await session?.renew?.call() ?? false;
            if (!renewed) {
              session?.onUnauthorized?.call();
            }
            break; // Tạm dừng lô hiện tại chờ token mới
          } else if (dioError.type == DioExceptionType.cancel) {
            // Yêu cầu bị hủy do phiên đăng xuất hoặc chuyển tài khoản
            _isPausedAuth = true;
            break;
          } else if (statusCode == 409) {
            await _repository.markConflict(
              record.clientEventId,
              reason: 'HTTP 409: $errorMessage',
            );
            conflictIds.add(record.clientEventId);
            // 409 là xung đột logic (revision conflict), không làm nghẽn bản ghi tiếp theo
          } else if (statusCode != null && statusCode >= 400 && statusCode < 500 && statusCode != 429) {
            // Lỗi client terminal (400, 403, 404, 405, 422...): payload sai contract, không thể thử lại
            await _repository.markFailedTerminal(
              record.clientEventId,
              'HTTP $statusCode: $errorMessage',
            );
            terminalFailedIds.add(record.clientEventId);
          } else if (isConnectionError || statusCode == null) {
            // Lỗi mất mạng ngoại tuyến hoặc timeout:
            // Tuân thủ Zero Data Loss: KHÔNG bao giờ dead-letter dữ liệu của người dùng khi offline.
            final delaySec = min(60, (pow(2, min(record.retryCount, 4)) * 2).toInt());
            await _repository.scheduleRetry(
              record.clientEventId,
              nextRetrySeconds: delaySec,
              error: 'Network unreachable: $errorMessage',
              asOf: nowUtc,
            );
            transientFailedIds.add(record.clientEventId);
            break; // Mất mạng, tạm ngắt đợt replay hiện tại để chờ kết nối
          } else {
            // Lỗi 5xx hoặc 429 từ phía máy chủ
            final nextRetry = record.retryCount + 1;
            if (nextRetry >= 5) {
              await _repository.markFailedTerminal(
                record.clientEventId,
                'Exceeded max server retries (5): HTTP $statusCode $errorMessage',
              );
              terminalFailedIds.add(record.clientEventId);
            } else {
              final delaySec = min(60, (pow(2, min(record.retryCount, 4)) * 2).toInt());
              await _repository.scheduleRetry(
                record.clientEventId,
                nextRetrySeconds: delaySec,
                error: 'HTTP $statusCode: $errorMessage',
                asOf: nowUtc,
              );
              transientFailedIds.add(record.clientEventId);
            }
            break;
          }
        } on ApiException catch (apiError) {
          final statusCode = apiError.statusCode;
          final errorMessage = apiError.message;
          if (statusCode == 401) {
            _isPausedAuth = true;
            final renewed = await session?.renew?.call() ?? false;
            if (!renewed) {
              session?.onUnauthorized?.call();
            }
            break;
          } else if (statusCode == 409) {
            await _repository.markConflict(
              record.clientEventId,
              reason: 'HTTP 409: $errorMessage',
            );
            conflictIds.add(record.clientEventId);
          } else if (statusCode != null && statusCode >= 400 && statusCode < 500 && statusCode != 429) {
            await _repository.markFailedTerminal(
              record.clientEventId,
              'HTTP $statusCode: $errorMessage',
            );
            terminalFailedIds.add(record.clientEventId);
          } else if (statusCode == null) {
            final delaySec = min(60, (pow(2, min(record.retryCount, 4)) * 2).toInt());
            await _repository.scheduleRetry(
              record.clientEventId,
              nextRetrySeconds: delaySec,
              error: 'Network unreachable: $errorMessage',
              asOf: nowUtc,
            );
            transientFailedIds.add(record.clientEventId);
            break;
          } else {
            final nextRetry = record.retryCount + 1;
            if (nextRetry >= 5) {
              await _repository.markFailedTerminal(
                record.clientEventId,
                'Exceeded max server retries (5): HTTP $statusCode $errorMessage',
              );
              terminalFailedIds.add(record.clientEventId);
            } else {
              final delaySec = min(60, (pow(2, min(record.retryCount, 4)) * 2).toInt());
              await _repository.scheduleRetry(
                record.clientEventId,
                nextRetrySeconds: delaySec,
                error: 'HTTP $statusCode: $errorMessage',
                asOf: nowUtc,
              );
              transientFailedIds.add(record.clientEventId);
            }
            break;
          }
        } catch (otherError) {
          await _repository.markFailedTerminal(
            record.clientEventId,
            'Unexpected error: $otherError',
          );
          terminalFailedIds.add(record.clientEventId);
        }
      }

      return ReplayReport(
        totalProcessed: syncedIds.length +
            conflictIds.length +
            terminalFailedIds.length +
            transientFailedIds.length,
        syncedIds: syncedIds,
        conflictIds: conflictIds,
        terminalFailedIds: terminalFailedIds,
        transientFailedIds: transientFailedIds,
        isPausedAuth: _isPausedAuth,
      );
    } finally {
      _isReplaying = false;
    }
  }

  /// Xả toàn bộ hàng đợi outbox cho đến khi hết bản ghi chờ hoặc gặp lỗi tạm ngắt.
  Future<ReplayReport> flushOutbox({int maxBatches = 10, int batchLimit = 20, DateTime? asOf}) async {
    var totalProcessed = 0;
    final allSynced = <String>[];
    final allConflicts = <String>[];
    final allTerminal = <String>[];
    final allTransient = <String>[];

    for (var i = 0; i < maxBatches; i++) {
      final report = await triggerReplay(batchLimit: batchLimit, asOf: asOf);
      totalProcessed += report.totalProcessed;
      allSynced.addAll(report.syncedIds);
      allConflicts.addAll(report.conflictIds);
      allTerminal.addAll(report.terminalFailedIds);
      allTransient.addAll(report.transientFailedIds);

      if (report.totalProcessed == 0 || report.isPausedAuth || report.transientFailedIds.isNotEmpty) {
        break;
      }
    }

    return ReplayReport(
      totalProcessed: totalProcessed,
      syncedIds: allSynced,
      conflictIds: allConflicts,
      terminalFailedIds: allTerminal,
      transientFailedIds: allTransient,
      isPausedAuth: _isPausedAuth,
    );
  }

  Future<Response<dynamic>> _sendRecord(OutboxRecord record) {
    final method = record.httpMethod.toUpperCase();
    return _apiClient.dio.request<dynamic>(
      record.endpoint,
      data: record.payload.isNotEmpty ? record.payload : null,
      options: Options(
        method: method,
        headers: {
          'Idempotency-Key': record.clientEventId,
        },
      ),
    );
  }
}
