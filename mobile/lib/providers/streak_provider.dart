import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

enum StreakDayKind { done, freeze, repair }

@immutable
class StreakDayRecord {
  const StreakDayRecord({
    required this.kind,
    this.minutes = 0,
  });

  final StreakDayKind kind;
  final int minutes;

  Map<String, dynamic> toJson() => {
        'kind': kind.name,
        'minutes': minutes,
      };

  static StreakDayRecord? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final kindName = raw['kind'] as String?;
    final kind = StreakDayKind.values.cast<StreakDayKind?>().firstWhere(
          (value) => value?.name == kindName,
          orElse: () => null,
        );
    if (kind == null) return null;
    return StreakDayRecord(
      kind: kind,
      minutes: (raw['minutes'] as num?)?.toInt() ?? 0,
    );
  }
}

@immutable
class StreakTier {
  const StreakTier({
    required this.level,
    required this.minDays,
    required this.nextTarget,
    required this.name,
    required this.description,
    required this.whisper,
  });

  final int level;
  final int minDays;
  final int nextTarget;
  final String name;
  final String description;
  final String whisper;
}

@immutable
class StreakCelebrationEvent {
  const StreakCelebrationEvent({
    required this.streakDays,
    required this.minutesAdded,
    required this.justUnlocked,
    required this.restoredFromRepair,
    required this.earnedFreeze,
    required this.evolved,
    required this.tier,
  });

  final int streakDays;
  final int minutesAdded;
  final bool justUnlocked;
  final bool restoredFromRepair;
  final bool earnedFreeze;
  final bool evolved;
  final StreakTier tier;
}

/// Manages "Nhịp cùng Melbel" focus streaks with a 3-day unlock threshold
/// and 3-tier ADHD-safe psychological protection (Auto-Freeze, 24h Repair,
/// and non-judgmental Soft Reset keeping month & record stats).
class StreakProvider extends ChangeNotifier {
  StreakProvider({
    required String ownerId,
    DateTime Function()? now,
  })  : _ownerId = ownerId,
        _now = now ?? DateTime.now;

  static const int unlockDays = 3;
  static const int minFocusSeconds = 120;
  static const int maxFreeze = 2;
  static const int maxRepairsPerMonth = 2;

  static const List<StreakTier> tiers = [
    StreakTier(
      level: 1,
      minDays: 3,
      nextTarget: 7,
      name: 'Mở chuỗi · Vào guồng',
      description:
          'Mở khóa sau 3 ngày · Kích hoạt Khiên ngủ nướng & Nối lại nhịp 24h',
      whisper:
          'Chuỗi đã sáng rồi! Khiên ngủ nướng và Nối lại nhịp 24h đã sẵn sàng bảo vệ bạn.',
    ),
    StreakTier(
      level: 2,
      minDays: 7,
      nextTarget: 14,
      name: 'Trọn tuần',
      description: 'Vầng lavender + 2 lá mầm xanh · Thưởng +1 Khiên',
      whisper: 'Trọn vẹn cả tuần đồng hành cùng nhau rồi đấy, tự hào quá!',
    ),
    StreakTier(
      level: 3,
      minDays: 14,
      nextTarget: 30,
      name: 'Bền bỉ',
      description: 'Vầng trăng đôi dịu mắt + ánh sao nhỏ',
      whisper: 'Hai tuần đều đặn! Bạn không cần hoàn hảo, chỉ cần quay lại.',
    ),
    StreakTier(
      level: 4,
      minDays: 30,
      nextTarget: 100,
      name: 'Bạn tri kỷ',
      description: 'Hào quang tri kỷ + nơ sao nhỏ (tự do bật/tắt)',
      whisper: '30+ ngày bên nhau — Melbel luôn ở đây dù ngày bận hay rảnh!',
    ),
    StreakTier(
      level: 5,
      minDays: 100,
      nextTarget: 100,
      name: 'Đồng hành bền vững',
      description: 'Vầng sao đêm rực rỡ cùng Melbel',
      whisper: '100+ ngày đồng hành — một thói quen thật dịu dàng và bền bỉ!',
    ),
  ];

  static StreakProvider? maybeOf(
    BuildContext context, {
    bool listen = true,
  }) {
    try {
      return Provider.of<StreakProvider>(context, listen: listen);
    } on ProviderNotFoundException {
      return null;
    }
  }

  final String _ownerId;
  final DateTime Function() _now;

  int _currentStreak = 0;
  int _longestStreak = 0;
  int _freezeCount = 1;
  int _repairsUsedThisMonth = 0;
  String? _repairMonthKey;
  String? _lastActiveDateKey;
  bool _repairPending = false;
  int _repairPreviousStreak = 0;
  String? _repairPendingDateKey;
  bool _usedFreezeNotice = false;
  bool _warmupMissedNotice = false;
  bool _softResetNotice = false;
  bool _crownEnabled = true;
  bool _introDismissed = false;
  bool _restored = false;
  StreakCelebrationEvent? _pendingCelebration;
  final Map<String, StreakDayRecord> _dayRecords = {};

  String get _storageKey => 'proplan.streak.v1.$_ownerId';

  bool get isRestored => _restored;
  int get currentStreak => _currentStreak;
  int get effectiveDisplayStreak =>
      _repairPending ? _repairPreviousStreak : _currentStreak;
  bool get isUnlocked => effectiveDisplayStreak >= unlockDays;
  int get longestStreak => _longestStreak;
  int get freezeCount => _freezeCount;
  int get repairsUsedThisMonth => _repairsUsedThisMonth;
  int get repairsLeftThisMonth =>
      math.max(0, maxRepairsPerMonth - _repairsUsedThisMonth);
  bool get repairPending => _repairPending;
  int get repairPreviousStreak => _repairPreviousStreak;
  bool get usedFreezeNotice => _usedFreezeNotice;
  bool get warmupMissedNotice => _warmupMissedNotice;
  bool get softResetNotice => _softResetNotice;
  bool get crownEnabled => _crownEnabled;
  bool get introDismissed => _introDismissed;
  StreakCelebrationEvent? get pendingCelebration => _pendingCelebration;

  bool get todayDone => dayStatus(_now()) == StreakDayKind.done;

  StreakTier get currentTier => tierFor(effectiveDisplayStreak);

  static StreakTier tierFor(int days) {
    if (days < unlockDays) {
      final safeDays = days.clamp(0, unlockDays - 1);
      final whisper = safeDays == 0
          ? 'Bắt đầu ngày đầu tiên để khởi động chuỗi 3 ngày cùng Melbel nhé!'
          : safeDays == 1
              ? 'Tốt lắm! Còn 2 ngày liên tiếp nữa để chính thức mở khóa Chuỗi.'
              : 'Còn 1 ngày nữa thôi là bạn chính thức mở khóa Chuỗi tập trung cùng Melbel!';
      return StreakTier(
        level: 0,
        minDays: 0,
        nextTarget: unlockDays,
        name: 'Khởi động ($safeDays/$unlockDays)',
        description:
            'Cần $unlockDays ngày tập trung liên tiếp để mở khóa chuỗi',
        whisper: whisper,
      );
    }
    var matched = tiers.first;
    for (final tier in tiers) {
      if (days >= tier.minDays) matched = tier;
    }
    return matched;
  }

  static String dateKey(DateTime day) =>
      '${day.year.toString().padLeft(4, '0')}-'
      '${day.month.toString().padLeft(2, '0')}-'
      '${day.day.toString().padLeft(2, '0')}';

  static String monthKey(DateTime day) =>
      '${day.year.toString().padLeft(4, '0')}-'
      '${day.month.toString().padLeft(2, '0')}';

  static DateTime? _parseDateKey(String? raw) {
    if (raw == null || raw.length != 10) return null;
    final parts = raw.split('-');
    if (parts.length != 3) return null;
    final y = int.tryParse(parts[0]);
    final m = int.tryParse(parts[1]);
    final d = int.tryParse(parts[2]);
    if (y == null || m == null || d == null) return null;
    return DateTime(y, m, d);
  }

  static int _daysBetween(DateTime from, DateTime to) {
    final a = DateTime.utc(from.year, from.month, from.day);
    final b = DateTime.utc(to.year, to.month, to.day);
    return b.difference(a).inDays;
  }

  StreakDayKind? dayStatus(DateTime day) => _dayRecords[dateKey(day)]?.kind;

  int dayMinutes(DateTime day) => _dayRecords[dateKey(day)]?.minutes ?? 0;

  int activeDaysInMonth([DateTime? month]) {
    final target = month ?? _now();
    final prefix = monthKey(target);
    var count = 0;
    for (final entry in _dayRecords.entries) {
      if (entry.key.startsWith(prefix) &&
          entry.value.kind == StreakDayKind.done) {
        count++;
      }
    }
    return count;
  }

  int contiguousRunLength(DateTime day) {
    final normalized = DateUtils.dateOnly(day);
    if (!_isRibbonKind(dayStatus(normalized))) return 0;

    var start = normalized;
    while (_isRibbonKind(
        dayStatus(DateTime(start.year, start.month, start.day - 1)))) {
      start = DateTime(start.year, start.month, start.day - 1);
    }

    var end = normalized;
    while (_isRibbonKind(
        dayStatus(DateTime(end.year, end.month, end.day + 1)))) {
      end = DateTime(end.year, end.month, end.day + 1);
    }

    return _daysBetween(start, end) + 1;
  }

  bool isInUnlockedRibbon(DateTime day) =>
      contiguousRunLength(day) >= unlockDays;

  bool hasRibbonPrev(DateTime day) {
    if (!isInUnlockedRibbon(day)) return false;
    final prev = DateTime(day.year, day.month, day.day - 1);
    return _isRibbonKind(dayStatus(prev));
  }

  bool hasRibbonNext(DateTime day) {
    if (!isInUnlockedRibbon(day)) return false;
    final next = DateTime(day.year, day.month, day.day + 1);
    return _isRibbonKind(dayStatus(next));
  }

  static bool _isRibbonKind(StreakDayKind? kind) =>
      kind == StreakDayKind.done || kind == StreakDayKind.freeze;

  Future<void> restore() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_storageKey);
      if (raw != null && raw.isNotEmpty) {
        final decoded = jsonDecode(raw);
        if (decoded is Map<String, dynamic>) {
          _loadFromMap(decoded);
        }
      }
    } catch (_) {
      // Corrupt or unavailable SharedPreferences must not crash app launch.
    }

    final changed = _reconcileForDate(DateUtils.dateOnly(_now()));
    _restored = true;
    if (changed) {
      await _persist();
    }
    notifyListeners();
  }

  void _loadFromMap(Map<String, dynamic> json) {
    _currentStreak = (json['current_streak'] as num?)?.toInt() ?? 0;
    _longestStreak = (json['longest_streak'] as num?)?.toInt() ?? 0;
    _freezeCount =
        ((json['freeze_count'] as num?)?.toInt() ?? 1).clamp(0, maxFreeze);
    _repairsUsedThisMonth =
        (json['repairs_used_this_month'] as num?)?.toInt() ?? 0;
    _repairMonthKey = json['repair_month_key'] as String?;
    _lastActiveDateKey = json['last_active_date_key'] as String?;
    _repairPending = json['repair_pending'] as bool? ?? false;
    _repairPreviousStreak =
        (json['repair_previous_streak'] as num?)?.toInt() ?? 0;
    _repairPendingDateKey = json['repair_pending_date_key'] as String?;
    _usedFreezeNotice = json['used_freeze_notice'] as bool? ?? false;
    _warmupMissedNotice = json['warmup_missed_notice'] as bool? ?? false;
    _softResetNotice = json['soft_reset_notice'] as bool? ?? false;
    _crownEnabled = json['crown_enabled'] as bool? ?? true;
    _introDismissed = json['intro_dismissed'] as bool? ?? false;

    _dayRecords.clear();
    final days = json['day_records'];
    if (days is Map) {
      for (final entry in days.entries) {
        if (entry.key is! String) continue;
        final parsed = StreakDayRecord.fromJson(entry.value);
        if (parsed != null) {
          _dayRecords[entry.key as String] = parsed;
        }
      }
    }
  }

  bool _reconcileForDate(DateTime today) {
    var changed = false;
    final todayKey = dateKey(today);
    final mKey = monthKey(today);
    if (_repairMonthKey != mKey) {
      _repairMonthKey = mKey;
      _repairsUsedThisMonth = 0;
      changed = true;
    }

    if (_repairPending) {
      if (_repairPendingDateKey == todayKey) {
        return changed;
      }
      // 24h repair window has expired without a focus session on the recovery day.
      _currentStreak = 0;
      _repairPending = false;
      _repairPendingDateKey = null;
      _usedFreezeNotice = false;
      _warmupMissedNotice = false;
      _softResetNotice = true;
      return true;
    }

    final last = _parseDateKey(_lastActiveDateKey);
    if (last == null) return changed;

    final gap = _daysBetween(last, today);
    if (gap <= 1) return changed;

    if (gap == 2) {
      final yesterday = DateTime(today.year, today.month, today.day - 1);
      final yesterdayKey = dateKey(yesterday);
      if (_currentStreak < unlockDays) {
        // Warmup (<3 days) missed: reset cleanly without spending a shield.
        _currentStreak = 0;
        _lastActiveDateKey = null;
        _usedFreezeNotice = false;
        _softResetNotice = false;
        _warmupMissedNotice = true;
        return true;
      }
      if (_freezeCount > 0) {
        _freezeCount--;
        _dayRecords[yesterdayKey] =
            const StreakDayRecord(kind: StreakDayKind.freeze, minutes: 0);
        _lastActiveDateKey = yesterdayKey;
        _usedFreezeNotice = true;
        _warmupMissedNotice = false;
        _softResetNotice = false;
        return true;
      }
      if (repairsLeftThisMonth > 0) {
        _repairPending = true;
        _repairPreviousStreak = _currentStreak;
        _repairPendingDateKey = todayKey;
        _dayRecords[yesterdayKey] =
            const StreakDayRecord(kind: StreakDayKind.repair, minutes: 0);
        _usedFreezeNotice = false;
        _warmupMissedNotice = false;
        _softResetNotice = false;
        return true;
      }
      _currentStreak = 0;
      _lastActiveDateKey = null;
      _usedFreezeNotice = false;
      _warmupMissedNotice = false;
      _softResetNotice = true;
      return true;
    }

    // Missed 2+ full days (gap >= 3): soft reset while preserving month total & record.
    if (_currentStreak > 0 || _repairPending) {
      _currentStreak = 0;
      _repairPending = false;
      _repairPendingDateKey = null;
      _lastActiveDateKey = null;
      _usedFreezeNotice = false;
      _warmupMissedNotice = false;
      _softResetNotice = true;
      return true;
    }
    return changed;
  }

  /// Evaluates state for [at] (defaults to now) and persists if reconciled.
  Future<void> reconcileNow([DateTime? at]) async {
    final target = DateUtils.dateOnly(at ?? _now());
    if (_reconcileForDate(target)) {
      await _persist();
      notifyListeners();
    }
  }

  Future<StreakCelebrationEvent?> recordFocusActivity({
    required int seconds,
    DateTime? at,
  }) async {
    if (seconds < minFocusSeconds) return null;

    final day = DateUtils.dateOnly(at ?? _now());
    _reconcileForDate(day);

    final key = dateKey(day);
    final minutes = math.max(1, (seconds / 60).round());
    final existing = _dayRecords[key];
    if (existing?.kind == StreakDayKind.done) {
      _dayRecords[key] = StreakDayRecord(
        kind: StreakDayKind.done,
        minutes: existing!.minutes + minutes,
      );
      _lastActiveDateKey ??= key;
      await _persist();
      notifyListeners();
      return null;
    }

    final oldDays = effectiveDisplayStreak;
    final oldTier = tierFor(oldDays);
    var restoredFromRepair = false;

    if (_repairPending && _repairPendingDateKey == key) {
      final yesterday = DateTime(day.year, day.month, day.day - 1);
      final yKey = dateKey(yesterday);
      _dayRecords[yKey] = StreakDayRecord(
        kind: StreakDayKind.freeze,
        minutes: _dayRecords[yKey]?.minutes ?? 0,
      );
      _currentStreak = _repairPreviousStreak + 1;
      _repairPending = false;
      _repairPendingDateKey = null;
      _repairsUsedThisMonth++;
      restoredFromRepair = true;
    } else {
      final last = _parseDateKey(_lastActiveDateKey);
      if (last != null && _daysBetween(last, day) == 1) {
        _currentStreak += 1;
      } else if (_lastActiveDateKey == null || _currentStreak == 0) {
        _currentStreak = 1;
      } else {
        _currentStreak += 1;
      }
    }

    _dayRecords[key] = StreakDayRecord(
      kind: StreakDayKind.done,
      minutes: minutes,
    );
    _lastActiveDateKey = key;
    _usedFreezeNotice = false;
    _warmupMissedNotice = false;
    _softResetNotice = false;

    final unlockedNow = _currentStreak >= unlockDays;
    final justUnlocked = oldDays < unlockDays && unlockedNow;

    if (unlockedNow) {
      _longestStreak = math.max(_longestStreak, _currentStreak);
    }

    var earnedFreeze = false;
    if (unlockedNow &&
        !justUnlocked &&
        _freezeCount < maxFreeze &&
        (_currentStreak == 7 || _currentStreak % 5 == 0)) {
      _freezeCount++;
      earnedFreeze = true;
    }

    final newTier = tierFor(_currentStreak);
    final event = StreakCelebrationEvent(
      streakDays: _currentStreak,
      minutesAdded: minutes,
      justUnlocked: justUnlocked,
      restoredFromRepair: restoredFromRepair,
      earnedFreeze: earnedFreeze,
      evolved: unlockedNow && newTier.level > oldTier.level,
      tier: newTier,
    );
    _pendingCelebration = event;
    await _persist();
    notifyListeners();
    return event;
  }

  /// Backfills historical completed sessions fetched from `/schedule` so the
  /// calendar ribbon and month stats stay aligned with server history.
  Future<void> ingestCompletedSessions(
      List<Map<String, dynamic>> sessions) async {
    var changed = false;
    for (final row in sessions) {
      if (row['status'] != 'completed') continue;
      final rawTs =
          (row['ended_at'] ?? row['scheduled_start']) as String?;
      if (rawTs == null) continue;
      final parsed = DateTime.tryParse(rawTs)?.toLocal();
      if (parsed == null) continue;
      final key = dateKey(parsed);
      if (_dayRecords.containsKey(key)) continue;

      final mins =
          (((row['work_seconds'] as num?)?.toInt() ?? 1500) / 60).ceil().clamp(
                1,
                600,
              );
      _dayRecords[key] = StreakDayRecord(
        kind: StreakDayKind.done,
        minutes: mins,
      );
      changed = true;
    }

    if (!changed) return;

    // Re-evaluate trailing active streak anchored at today or yesterday.
    final today = DateUtils.dateOnly(_now());
    final yesterday = DateTime(today.year, today.month, today.day - 1);
    final anchor = dayStatus(today) != null
        ? today
        : (dayStatus(yesterday) != null ? yesterday : null);

    if (anchor != null && !_repairPending) {
      final runLen = contiguousRunLength(anchor);
      if (runLen > _currentStreak) {
        _currentStreak = runLen;
        _lastActiveDateKey = dateKey(anchor);
      }
    }

    // Recompute longest historical streak across all known done/freeze days.
    var bestHistorical = 0;
    for (final key in _dayRecords.keys) {
      final d = _parseDateKey(key);
      if (d == null) continue;
      final len = contiguousRunLength(d);
      if (len >= unlockDays && len > bestHistorical) {
        bestHistorical = len;
      }
    }
    if (bestHistorical > _longestStreak) {
      _longestStreak = bestHistorical;
    }

    await _persist();
    notifyListeners();
  }

  StreakCelebrationEvent? consumePendingCelebration() {
    final event = _pendingCelebration;
    _pendingCelebration = null;
    return event;
  }

  Future<void> setCrownEnabled(bool enabled) async {
    if (_crownEnabled == enabled) return;
    _crownEnabled = enabled;
    await _persist();
    notifyListeners();
  }

  Future<void> dismissIntro() async {
    if (_introDismissed) return;
    _introDismissed = true;
    await _persist();
    notifyListeners();
  }

  Future<void> _persist() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final payload = {
        'current_streak': _currentStreak,
        'longest_streak': _longestStreak,
        'freeze_count': _freezeCount,
        'repairs_used_this_month': _repairsUsedThisMonth,
        'repair_month_key': _repairMonthKey,
        'last_active_date_key': _lastActiveDateKey,
        'repair_pending': _repairPending,
        'repair_previous_streak': _repairPreviousStreak,
        'repair_pending_date_key': _repairPendingDateKey,
        'used_freeze_notice': _usedFreezeNotice,
        'warmup_missed_notice': _warmupMissedNotice,
        'soft_reset_notice': _softResetNotice,
        'crown_enabled': _crownEnabled,
        'intro_dismissed': _introDismissed,
        'day_records': {
          for (final entry in _dayRecords.entries)
            entry.key: entry.value.toJson(),
        },
      };
      await prefs.setString(_storageKey, jsonEncode(payload));
    } catch (_) {
      // Ignore storage persistence errors on restricted environments.
    }
  }
}
