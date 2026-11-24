import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/services/google_calendar_api.dart';

void main() {
  test('Calendar reads a bounded day and confirms a write calendar preview',
      () async {
    final requests = <RequestOptions>[];
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(
        onRequest: (request, handler) {
          requests.add(request);
          final Object data = switch (request.path) {
            '/calendar/connections/account/events' => [
                {'title': 'Họp nhóm'},
              ],
            '/calendar/connections/account/configuration-previews' => {
                'preview_id': 'preview',
                'preview_revision': 7,
              },
            '/calendar/configuration-previews/preview/confirm' => {
                'write_calendar_id': 'target'
              },
            _ => throw StateError('Unexpected request ${request.path}'),
          };
          handler
              .resolve(Response(requestOptions: request, data: {'data': data}));
        },
      ));
    final calendar = GoogleCalendarApi(ApiClient(dio: dio, bindSession: false));

    final events = await calendar.events('account', 'my-calendar@example.com',
        DateTime.utc(2026, 11, 14), DateTime.utc(2026, 11, 15));
    expect(events.single['title'], 'Họp nhóm');
    expect(requests.single.queryParameters, {
      'google_calendar_id': 'my-calendar@example.com',
      'from': '2026-11-14T00:00:00.000Z',
      'to': '2026-11-15T00:00:00.000Z',
      'limit': 250,
    });

    final preview = await calendar.previewWriteCalendar(
        'account', ['existing'], 'target',
        createCalendar: false);
    expect(requests.length, 2);
    expect(requests.last.data, {
      'read_calendar_ids': ['existing'],
      'write_calendar_id': 'target',
      'existing_event_action': 'keep',
    });
    await calendar.confirmPreview(preview);
    expect(
        requests.last.path, '/calendar/configuration-previews/preview/confirm');
    expect(requests.last.data, {'expected_preview_revision': 7});
  });
}
