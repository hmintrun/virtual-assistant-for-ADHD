/// Reads a deadline only from a phrase that explicitly marks it as one.
///
/// This is a small on-device fallback, not a replacement for UF1 semantic
/// extraction. Ambiguous or incomplete phrases return null so the UI can ask
/// the user instead of turning availability times into a deadline.
DateTime? parseVietnameseDeadline(String input, {DateTime? reference}) {
  final text = input.trim().toLowerCase().replaceAll(RegExp(r'\s+'), ' ');
  if (text.isEmpty) return null;

  final now = reference ?? DateTime.now();
  final cuePattern = RegExp(
    r'deadline|hạn\s+chót|hạn\s+nộp|hạn|nộp\s+trước|phải\s+nộp|nộp|hoàn\s+thành\s+trước|xong\s+trước|gửi\s+trước',
  );
  final cues = cuePattern.allMatches(text).toList(growable: false);
  if (cues.isEmpty) return null;

  for (final cue in cues) {
    final leftBoundary = _lastBoundary(text, cue.start);
    final rightBoundary = _nextBoundary(text, cue.end);
    var phraseStart = leftBoundary;
    var phraseEnd = rightBoundary;

    // Keep the deadline phrase near its cue. In particular, do not let a
    // preceding availability clause supply its weekday or time.
    final before = text.substring(phraseStart, cue.start);
    final connectors =
        RegExp(r'\b(?:và|nhưng|còn)\b').allMatches(before).toList();
    if (connectors.isNotEmpty) phraseStart += connectors.last.end;
    final after = text.substring(cue.end, phraseEnd);
    final afterConnector = RegExp(r'\b(?:và|nhưng|còn)\b').firstMatch(after);
    if (afterConnector != null) phraseEnd = cue.end + afterConnector.start;

    // If the cue follows the date (for example, “Chủ nhật là deadline”),
    // include the text before it, but only within its current clause.
    if (text.substring(cue.end, phraseEnd).trim().isEmpty) {
      phraseStart = leftBoundary;
    }
    final phrase = text.substring(phraseStart, phraseEnd).trim();
    final parsed = _parseExplicitDeadlinePhrase(phrase, now);
    if (parsed != null) return parsed;
  }
  return null;
}

DateTime? _parseExplicitDeadlinePhrase(String phrase, DateTime now) {
  var year = now.year;
  var month = now.month;
  var day = now.day;
  var dateStart = -1;
  var dateEnd = -1;
  DateTime? relativeDate;

  final isoDates = RegExp(r'(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)')
      .allMatches(phrase)
      .toList(growable: false);
  final numericDates = RegExp(
    r'(?<!\d)(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?(?!\d)',
  ).allMatches(phrase).toList(growable: false);
  final vietnameseDates = RegExp(
    r'ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s+năm\s+(\d{4}))?',
  ).allMatches(phrase).toList(growable: false);

  final explicitDates = [...isoDates, ...numericDates, ...vietnameseDates]
    ..sort((a, b) => a.start.compareTo(b.start));
  if (explicitDates.length > 1) return null;

  if (explicitDates.isNotEmpty) {
    final match = explicitDates.single;
    dateStart = match.start;
    dateEnd = match.end;
    if (isoDates.contains(match)) {
      year = int.parse(match.group(1)!);
      month = int.parse(match.group(2)!);
      day = int.parse(match.group(3)!);
    } else {
      day = int.parse(match.group(1)!);
      month = int.parse(match.group(2)!);
      final rawYear = match.group(3);
      if (rawYear != null) {
        year = int.parse(rawYear.length == 2 ? '20$rawYear' : rawYear);
      } else if (month < now.month || (month == now.month && day < now.day)) {
        year++;
      }
    }
  } else {
    final relativeMatches = <DateTime>[];
    if (RegExp(r'\bngày\s+mai\b|\bmai\b').hasMatch(phrase)) {
      relativeMatches.add(DateTime(now.year, now.month, now.day + 1));
    }
    if (RegExp(r'\bngày\s+kia\b|\bmốt\b').hasMatch(phrase)) {
      relativeMatches.add(DateTime(now.year, now.month, now.day + 2));
    }
    if (RegExp(r'\bhôm\s+nay\b|\bchiều\s+nay\b|\btối\s+nay\b|\btrưa\s+nay\b')
        .hasMatch(phrase)) {
      relativeMatches.add(DateTime(now.year, now.month, now.day));
    }
    if (RegExp(r'\bcuối\s+tuần\b|\bchủ\s+nhật\b|\bcn\b').hasMatch(phrase)) {
      final daysAhead = (DateTime.sunday - now.weekday) % 7;
      relativeMatches.add(DateTime(now.year, now.month, now.day + daysAhead));
    }
    for (final match in RegExp(r'\bthứ\s*([2-7])\b').allMatches(phrase)) {
      final weekday = int.parse(match.group(1)!) - 1;
      final daysAhead = (weekday - now.weekday) % 7;
      relativeMatches.add(DateTime(now.year, now.month, now.day + daysAhead));
    }
    if (relativeMatches.length != 1) return null;
    relativeDate = relativeMatches.single;
    year = relativeDate.year;
    month = relativeDate.month;
    day = relativeDate.day;
  }

  final validDate = DateTime(year, month, day);
  if (validDate.year != year ||
      validDate.month != month ||
      validDate.day != day) {
    return null;
  }

  final timePattern = RegExp(
    r'(?<![\d/.-])(\d{1,2})(?:\s*:\s*(\d{2})|\s*h(?:\s*(\d{2}))?|\s+giờ)?\s*(sáng|trưa|chiều|tối|am|pm)?\b',
  );
  final timeMatches = timePattern.allMatches(phrase).where((match) {
    if (dateStart >= 0 && match.start < dateEnd && match.end > dateStart) {
      return false;
    }
    final token = match.group(0) ?? '';
    final period = match.group(4);
    return token.contains(':') ||
        RegExp(r'\d\s*h').hasMatch(token) ||
        token.contains('giờ') ||
        period != null;
  }).toList(growable: false);
  if (timeMatches.length != 1) return null;

  final time = timeMatches.single;
  var hour = int.parse(time.group(1)!);
  final minute = int.tryParse(time.group(2) ?? time.group(3) ?? '') ?? 0;
  final period = time.group(4);
  if (hour > 23 || minute > 59) return null;
  if ((period == 'chiều' || period == 'tối' || period == 'pm') && hour < 12) {
    hour += 12;
  } else if ((period == 'sáng' || period == 'am') && hour == 12) {
    hour = 0;
  } else if (period == 'trưa' && hour < 11) {
    hour += 12;
  } else if (period == null &&
      hour > 0 &&
      hour < 13 &&
      !(time.group(0)?.contains(':') ?? false)) {
    // “5h” alone does not tell us whether the user means morning or evening.
    return null;
  }

  var result = DateTime(year, month, day, hour, minute);
  if (relativeDate != null && !result.isAfter(now)) {
    // A weekday that is today but whose deadline time has passed means the
    // next occurrence, not a deadline in the past.
    result = DateTime(year, month, day + 7, hour, minute);
  }
  return result.isAfter(now) ? result : null;
}

int _lastBoundary(String text, int before) {
  final matches = RegExp(r'[,;.!?\n]').allMatches(text.substring(0, before));
  return matches.isEmpty ? 0 : matches.last.end;
}

int _nextBoundary(String text, int after) {
  final match = RegExp(r'[,;.!?\n]').firstMatch(text.substring(after));
  return match == null ? text.length : after + match.start;
}
