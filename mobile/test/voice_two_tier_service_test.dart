import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/services/voice_two_tier_service.dart';

TwoTierSttService _svc(
  Future<TwoTierTranscript> Function() call, {
  Duration timeout = const Duration(milliseconds: 50),
}) =>
    TwoTierSttService(
      timeout: timeout,
      backend: (path, {required recordingId, thoughtId}) => call(),
    );

void main() {
  test('Backend chép được → trả chữ, giữ cờ low_confidence', () async {
    final result = await _svc(
      () async =>
          const TwoTierTranscript(text: 'Làm slide', lowConfidence: true),
    ).transcribeTwoTier('a.m4a', recordingId: 'r1');
    expect(result.text, 'Làm slide');
    expect(result.lowConfidence, isTrue);
  });

  group('lỗi hạ tầng → chuyển sang nghe trên máy', () {
    Future<void> expectFallback(Object error, String reason) async {
      await expectLater(
        _svc(() async => throw error).transcribeTwoTier('a', recordingId: 'r'),
        throwsA(isA<SttNeedsOnDeviceFallback>()
            .having((e) => e.reason, 'reason', reason)),
      );
    }

    test('mất mạng (Dio không có phản hồi)',
        () => expectFallback(const ApiException('offline'), 'network'));
    test(
        'STT_UNAVAILABLE 503',
        () => expectFallback(
            const ApiException('x', code: 'STT_UNAVAILABLE', statusCode: 503),
            'STT_UNAVAILABLE'));
    test(
        'STT_RATE_LIMITED 429',
        () => expectFallback(
            const ApiException('x', code: 'STT_RATE_LIMITED', statusCode: 429),
            'STT_RATE_LIMITED'));
    test(
        '502 không mã',
        () => expectFallback(
            const ApiException('x', statusCode: 502), 'http_502'));

    test('quá 45s (ở đây rút ngắn) → timeout', () async {
      final never = Completer<TwoTierTranscript>();
      await expectLater(
        _svc(() => never.future).transcribeTwoTier('a', recordingId: 'r'),
        throwsA(isA<SttNeedsOnDeviceFallback>()
            .having((e) => e.reason, 'reason', 'timeout')),
      );
    });
  });

  group('lỗi audio/xác thực → KHÔNG fallback, nhờ ghi âm lại', () {
    for (final code in [
      'NO_SPEECH_DETECTED',
      'AUDIO_TOO_LARGE',
      'UNSUPPORTED_AUDIO',
    ]) {
      test(code, () async {
        await expectLater(
          _svc(() async => throw ApiException('x', code: code, statusCode: 422))
              .transcribeTwoTier('a', recordingId: 'r'),
          throwsA(isA<ApiException>().having((e) => e.code, 'code', code)),
        );
      });
    }

    test('401 chưa đăng nhập', () async {
      await expectLater(
        _svc(() async => throw const ApiException('x', statusCode: 401))
            .transcribeTwoTier('a', recordingId: 'r'),
        throwsA(isA<ApiException>()),
      );
    });

    test('chữ rỗng → NO_SPEECH_DETECTED, không phải mất mạng', () async {
      await expectLater(
        _svc(() async => const TwoTierTranscript(text: '  '))
            .transcribeTwoTier('a', recordingId: 'r'),
        throwsA(isA<ApiException>()
            .having((e) => e.code, 'code', 'NO_SPEECH_DETECTED')),
      );
    });
  });

  test('hủy giữa chừng → trả về ngay, không treo chờ Backend', () async {
    final slow = Completer<TwoTierTranscript>();
    final svc = _svc(() => slow.future, timeout: const Duration(seconds: 30));
    final future = svc.transcribeTwoTier('a', recordingId: 'r');

    svc.cancel();
    await expectLater(future, throwsA(isA<SttCancelled>()))
        .timeout(const Duration(seconds: 1));

    // Backend trả muộn hoặc lỗi muộn cũng không làm crash.
    slow.completeError(const ApiException('late'));
  });

  test('lượt mới thay lượt cũ: kết quả lượt cũ bị bỏ', () async {
    final first = Completer<TwoTierTranscript>();
    var calls = 0;
    final svc = TwoTierSttService(
      timeout: const Duration(seconds: 5),
      backend: (path, {required recordingId, thoughtId}) {
        calls++;
        return calls == 1
            ? first.future
            : Future.value(const TwoTierTranscript(text: 'lượt 2'));
      },
    );
    final old = svc.transcribeTwoTier('a', recordingId: 'r1');
    // Gắn kỳ vọng NGAY để lỗi SttCancelled không thành lỗi chưa xử lý.
    final oldCancelled = expectLater(old, throwsA(isA<SttCancelled>()));
    svc.cancel();
    final latest = await svc.transcribeTwoTier('b', recordingId: 'r2');
    first.complete(const TwoTierTranscript(text: 'lượt 1'));

    expect(latest.text, 'lượt 2');
    await oldCancelled;
  });

  test('hàm backend ném lỗi đồng bộ vẫn được phân loại (không văng ra ngoài)',
      () async {
    final svc = TwoTierSttService(
      backend: (path, {required recordingId, thoughtId}) =>
          throw const ApiException('offline'),
    );
    await expectLater(
      svc.transcribeTwoTier('a', recordingId: 'r'),
      throwsA(isA<SttNeedsOnDeviceFallback>()),
    );
  });
}
