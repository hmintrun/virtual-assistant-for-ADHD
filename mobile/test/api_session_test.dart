import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/core/api_client.dart';
import 'package:proplan_va/core/api_session.dart';

void main() {
  tearDown(() => ApiSession.current = null);

  test('401 refreshes the same owner and preserves the command body and key',
      () async {
    final session = ApiSession('owner-a', token: 'expired');
    ApiSession.current = session;
    var renewals = 0;
    session.renew = () async {
      renewals++;
      session.token = 'fresh';
      return true;
    };
    final requests = <RequestOptions>[];
    final dio = Dio();
    final client = ApiClient(dio: dio);
    dio.interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
      requests.add(request);
      final authenticated = request.headers['Authorization'] == 'Bearer fresh';
      final response = Response<Map<String, dynamic>>(
        requestOptions: request,
        statusCode: authenticated ? 200 : 401,
        data: {
          if (authenticated) 'data': {'saved': true},
        },
      );
      if (authenticated) {
        handler.resolve(response);
      } else {
        handler.reject(
            DioException.badResponse(
              statusCode: 401,
              requestOptions: request,
              response: response,
            ),
            true);
      }
    }));

    final body = {'title': 'Làm slide'};
    expect(await client.postData('/drafts', data: body, idempotencyKey: 'key'),
        {'saved': true});
    expect(renewals, 1);
    expect(requests, hasLength(2));
    expect(requests.last.data, body);
    expect(requests.last.headers['Idempotency-Key'], 'key');
  });

  test('Calendar permission 401 keeps the current app session', () async {
    final session = ApiSession('owner-a', token: 'valid');
    ApiSession.current = session;
    session.renew = () async => throw StateError('Unexpected session renewal');
    session.onUnauthorized = () => fail('App session was marked unauthorized');
    final dio = Dio();
    final client = ApiClient(dio: dio);
    dio.interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
      handler.reject(
        DioException.badResponse(
          statusCode: 401,
          requestOptions: request,
          response: Response(
            requestOptions: request,
            statusCode: 401,
            data: {
              'error': {'code': 'CALENDAR_AUTH_REQUIRED'}
            },
          ),
        ),
        true,
      );
    }));
    await expectLater(
      client.getData('/calendar/connections/id/calendars'),
      throwsA(isA<ApiException>()
          .having((error) => error.code, 'code', 'CALENDAR_AUTH_REQUIRED')),
    );
  });

  test('a client created for a signed-out owner cannot use the next token',
      () async {
    final first = ApiSession('owner-a', token: 'a');
    ApiSession.current = first;
    final dio = Dio();
    final oldClient = ApiClient(dio: dio);
    var sent = false;
    dio.interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
      sent = true;
      handler.resolve(Response(
        requestOptions: request,
        data: <String, dynamic>{'data': <String, dynamic>{}},
      ));
    }));

    first.active = false;
    ApiSession.current = ApiSession('owner-b', token: 'b');
    await expectLater(
        oldClient.getData('/drafts'), throwsA(isA<ApiException>()));
    expect(sent, isFalse);
  });
}
