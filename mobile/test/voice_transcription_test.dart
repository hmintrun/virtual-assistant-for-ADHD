import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/services/voice_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Voice API sends the right purpose and stable retry key', () async {
    SharedPreferences.setMockInitialValues({'focus_device_id': 'device-1'});
    final directory = await Directory.systemTemp.createTemp();
    addTearDown(() => directory.delete(recursive: true));
    final file = await File('${directory.path}/note.m4a').writeAsBytes([1]);
    final requests = <RequestOptions>[];
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(
        onRequest: (request, handler) {
          requests.add(request);
          handler.resolve(Response(
            requestOptions: request,
            statusCode: 200,
            data: {
              'data': {'text': ' Ghi chú thử '}
            },
          ));
        },
      ));
    final service = VoiceTranscriptionService(api: ApiClient(dio: dio));
    expect(await service.transcribeM4a(file.path, recordingId: 'entry'),
        'Ghi chú thử');
    for (var i = 0; i < 2; i++) {
      await service.transcribeM4a(file.path,
          recordingId: 'note', thoughtId: 'server-note');
    }
    Map<String, String> fields(RequestOptions request) =>
        Map.fromEntries((request.data as FormData).fields);
    expect(fields(requests.first),
        {'purpose': 'uf1_input', 'device_id': 'device-1'});
    expect(fields(requests.last), {
      'purpose': 'thought_to_draft',
      'device_id': 'device-1',
      'thought_id': 'server-note',
    });
    expect(requests.last.path, '/voice/transcriptions');
    expect((requests.last.data as FormData).files.single.key, 'file');
    expect(requests[1].headers['Idempotency-Key'],
        requests[2].headers['Idempotency-Key']);
  });
}
