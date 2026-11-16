import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:google_sign_in/google_sign_in.dart';

import '../core/api_client.dart';
import '../core/api_session.dart';

class AuthService extends ChangeNotifier {
  static const isDev = String.fromEnvironment('AUTH_MODE') == 'dev_user';
  static const _webClientId = String.fromEnvironment('GOOGLE_CLIENT_ID');
  static const _serverClientId =
      String.fromEnvironment('GOOGLE_SERVER_CLIENT_ID');
  static const _sessionKey = 'proplan.auth.session.v1';

  final _storage = const FlutterSecureStorage();
  final _publicApi = ApiClient(bindSession: false);
  final _google = GoogleSignIn.instance;
  Future<void>? _googleInitialization;
  StreamSubscription<GoogleSignInAuthenticationEvent>? _webEvents;
  Future<bool>? _renewing;
  String? _refreshToken;
  DateTime? _refreshExpiresAt;

  ApiSession? session;
  Map<String, dynamic>? user;
  bool loading = true;
  bool busy = false;
  bool needsSignIn = false;
  String? error;

  bool get configured => kIsWeb
      ? _webClientId.isNotEmpty
      : defaultTargetPlatform == TargetPlatform.android &&
          _serverClientId.isNotEmpty;

  Future<void> initializeGoogle() =>
      _googleInitialization ??= _initializeGoogle();

  Future<void> _initializeGoogle() async {
    await _google.initialize(
      clientId: kIsWeb ? _webClientId : null,
      serverClientId: kIsWeb ? null : _serverClientId,
    );
    if (kIsWeb) {
      _webEvents = _google.authenticationEvents.listen((event) {
        if (event is GoogleSignInAuthenticationEventSignIn && !busy) {
          unawaited(_signIn(() async => event.user));
        }
      }, onError: (Object error) => _showError(error));
    }
  }

  Future<void> restore() async {
    try {
      if (isDev) {
        _bind(ApiSession('dev_user'), null);
        return;
      }
      final raw = await _storage.read(key: _sessionKey);
      if (raw == null) return;
      final saved = jsonDecode(raw) as Map<String, dynamic>;
      final profile = saved['user'] as Map<String, dynamic>;
      final owner = profile['user_id'] as String;
      final bound = ApiSession(owner, token: saved['token'] as String);
      _refreshToken = saved['refresh_token'] as String?;
      _refreshExpiresAt =
          DateTime.tryParse(saved['refresh_expires_at'] as String? ?? '');
      _bind(bound, profile);
      try {
        final verified = await ApiClient().getData('/auth/me');
        if (verified['user_id'] != owner) {
          await signOut();
          return;
        }
        user = verified;
      } on ApiException {
        // Keep the cache bound to its last authenticated account.
        needsSignIn = true;
      }
    } catch (_) {
      if (session == null) error = 'Chưa khôi phục được phiên đăng nhập.';
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  void _bind(ApiSession bound, Map<String, dynamic>? profile) {
    session = bound;
    ApiSession.current = bound;
    user = profile;
    if (!isDev) {
      bound.renew = () => _renewing ??= _renew(bound).whenComplete(() {
            _renewing = null;
          });
      bound.onUnauthorized = () {
        if (!needsSignIn) {
          needsSignIn = true;
          notifyListeners();
        }
      };
    }
  }

  Future<bool> _renew(ApiSession bound) async {
    if (busy || !bound.active || session != bound) return false;
    final refreshToken = _refreshToken;
    final expiresAt = _refreshExpiresAt;
    if (refreshToken != null &&
        (expiresAt == null || expiresAt.isAfter(DateTime.now()))) {
      try {
        final data = await _publicApi.postData(
          '/auth/refresh',
          data: {'refresh_token': refreshToken},
          idempotent: false,
        );
        if (session != bound || !bound.active || user == null) return false;
        final profile = data['user'] as Map<String, dynamic>? ?? user!;
        if (profile['user_id'] != bound.ownerId) return false;
        await _saveTokens(data, profile, keepRefreshToken: true);
        bound.token = data['access_token'] as String;
        user = profile;
        needsSignIn = false;
        error = null;
        notifyListeners();
        return true;
      } on ApiException catch (failure) {
        if (failure.statusCode != 401 && failure.statusCode != 404)
          return false;
      } catch (_) {
        return false;
      }
    }
    if (!configured || kIsWeb || busy || !bound.active) return false;
    try {
      await initializeGoogle();
      final account = await _google.attemptLightweightAuthentication();
      if (account == null || !bound.active) return false;
      await _exchange(account, bound);
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> signIn() => _signIn(() async {
        await initializeGoogle();
        return _google.authenticate();
      });

  Future<void> _signIn(
      Future<GoogleSignInAccount> Function() getAccount) async {
    if (busy || loading) return;
    if (_renewing != null) await _renewing;
    if (busy || loading) return;
    final previous = session;
    busy = true;
    error = null;
    notifyListeners();
    try {
      await _exchange(await getAccount(), previous);
    } catch (failure) {
      _showError(failure);
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> _exchange(
      GoogleSignInAccount account, ApiSession? previous) async {
    final idToken = account.authentication.idToken;
    if (idToken == null) {
      throw const ApiException(
          'Google chưa trả thông tin xác thực. Hãy thử lại.');
    }
    final data = await _publicApi.postData(
      '/auth/google',
      data: {'id_token': idToken},
      idempotent: false,
    );
    final profile = data['user'] as Map<String, dynamic>;
    final owner = profile['user_id'] as String;
    final token = data['access_token'] as String;
    if (session != previous) return;
    if (previous != null && previous.ownerId != owner) {
      throw const ApiException(
          'Hãy dùng lại tài khoản hiện tại hoặc đăng xuất trước khi đổi tài khoản.');
    }
    await _saveTokens(data, profile);
    if (previous == null) {
      _bind(ApiSession(owner, token: token), profile);
    } else {
      previous.token = token;
      user = profile;
    }
    needsSignIn = false;
    error = null;
    notifyListeners();
  }

  Future<void> _saveTokens(
    Map<String, dynamic> data,
    Map<String, dynamic> profile, {
    bool keepRefreshToken = false,
  }) async {
    final refresh = data['refresh_token'] as String? ??
        (keepRefreshToken ? _refreshToken : null);
    final expiresIn = data['refresh_expires_in'] as int?;
    final expiresAt = expiresIn != null
        ? DateTime.now().toUtc().add(Duration(seconds: expiresIn))
        : (keepRefreshToken ? _refreshExpiresAt : null);
    await _storage.write(
      key: _sessionKey,
      value: jsonEncode({
        'user': profile,
        'token': data['access_token'] as String,
        'refresh_token': refresh,
        'refresh_expires_at': expiresAt?.toIso8601String(),
      }),
    );
    _refreshToken = refresh;
    _refreshExpiresAt = expiresAt;
  }

  void _showError(Object failure) {
    if (failure is GoogleSignInException &&
        failure.code == GoogleSignInExceptionCode.canceled) return;
    error = failure is ApiException
        ? failure.message
        : 'Chưa đăng nhập được với Google. Hãy kiểm tra cấu hình và thử lại.';
    notifyListeners();
  }

  Future<void> signOut() async {
    if (busy) return;
    busy = true;
    notifyListeners();
    try {
      await _renewing;
      await _storage.delete(key: _sessionKey);
      _refreshToken = null;
      _refreshExpiresAt = null;
      session?.active = false;
      session = null;
      ApiSession.current = null;
      user = null;
      error = null;
      needsSignIn = false;
      if (_googleInitialization != null) {
        try {
          await _googleInitialization;
          await _google.signOut();
        } catch (_) {
          error = 'Đã thoát ứng dụng. Google chưa xác nhận đăng xuất.';
        }
      }
    } catch (failure) {
      _showError(failure);
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    unawaited(_webEvents?.cancel());
    super.dispose();
  }
}
