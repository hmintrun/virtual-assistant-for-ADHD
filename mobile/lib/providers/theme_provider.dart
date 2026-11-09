import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../core/theme.dart';

class ThemeProvider extends ChangeNotifier {
  static const _storageKey = 'melbel.appearance.v1';
  static const _storage = FlutterSecureStorage();
  static const options = {'light', 'dark', 'system'};

  ThemeProvider() {
    _localTimeTimer = Timer.periodic(
      const Duration(minutes: 1),
      (_) => _refreshLocalTimeMode(),
    );
  }

  String _appearance = 'light';
  Timer? _localTimeTimer;

  String get appearance => _appearance;

  bool get isDark {
    if (_appearance == 'system') {
      final hour = DateTime.now().hour;
      return hour < 6 || hour >= 18;
    }
    return _appearance == 'dark';
  }

  Future<void> restore() async {
    try {
      final saved = await _storage.read(key: _storageKey);
      if (saved != null && options.contains(saved)) {
        _appearance = saved;
      }
    } catch (_) {
      // Keep the default light theme if secure storage is unavailable.
    }
    MelbelColors.isDark = isDark;
    notifyListeners();
  }

  Future<void> setAppearance(String value) async {
    if (!options.contains(value)) return;
    _appearance = value;
    MelbelColors.isDark = isDark;
    notifyListeners();
    try {
      await _storage.write(key: _storageKey, value: value);
    } catch (_) {
      // Theme remains active for this run even if persistence is unavailable.
    }
  }

  void _refreshLocalTimeMode() {
    if (_appearance != 'system' || MelbelColors.isDark == isDark) return;
    MelbelColors.isDark = isDark;
    notifyListeners();
  }

  @override
  void dispose() {
    _localTimeTimer?.cancel();
    super.dispose();
  }
}
