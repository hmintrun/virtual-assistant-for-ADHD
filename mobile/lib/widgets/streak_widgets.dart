import 'dart:async';
import 'dart:ui' show PathMetric;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/theme.dart';
import '../providers/streak_provider.dart';
import '../services/melbel_sound_service.dart';
import 'melbel_ui.dart';

/// Paints Melbel's rounded cat paw outline/fill matching the `38x43` geometry
/// used across the schedule calendar and streak ribbon views.
class MelbelPawPainter extends CustomPainter {
  const MelbelPawPainter({
    required this.fillColor,
    this.strokeColor,
    this.strokeWidth = 0,
    this.dashed = false,
  });

  final Color fillColor;
  final Color? strokeColor;
  final double strokeWidth;
  final bool dashed;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    final center = size.center(Offset.zero);
    canvas.translate(center.dx, center.dy - size.height * 0.13);
    canvas.scale(1.1);
    canvas.translate(-center.dx, -center.dy);
    canvas.scale(size.width / 38, size.height / 43);

    final pad = Path()
      ..moveTo(19, 15)
      ..cubicTo(12, 15, 5, 23, 5, 29)
      ..cubicTo(5, 35, 11, 38, 19, 38)
      ..cubicTo(27, 38, 33, 35, 33, 29)
      ..cubicTo(33, 23, 26, 15, 19, 15)
      ..close();

    final toes = [
      for (final c in const [
        Offset(5, 13),
        Offset(14, 7),
        Offset(24, 7),
        Offset(33, 13),
      ])
        Path()..addOval(Rect.fromCenter(center: c, width: 7, height: 9)),
    ];

    final fillPaint = Paint()..color = fillColor;
    canvas.drawPath(pad, fillPaint);
    for (final toe in toes) {
      canvas.drawPath(toe, fillPaint);
    }

    if (strokeColor != null && strokeWidth > 0) {
      final strokePaint = Paint()
        ..color = strokeColor!
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..strokeJoin = StrokeJoin.round
        ..strokeCap = StrokeCap.round;
      for (final path in [pad, ...toes]) {
        if (dashed) {
          _drawDashedPath(canvas, path, strokePaint);
        } else {
          canvas.drawPath(path, strokePaint);
        }
      }
    }
    canvas.restore();
  }

  static void _drawDashedPath(Canvas canvas, Path path, Paint paint) {
    const dashLength = 4.0;
    const gapLength = 3.0;
    for (final PathMetric metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final next = (distance + dashLength).clamp(0.0, metric.length);
        canvas.drawPath(metric.extractPath(distance, next), paint);
        distance += dashLength + gapLength;
      }
    }
  }

  @override
  bool shouldRepaint(MelbelPawPainter oldDelegate) =>
      fillColor != oldDelegate.fillColor ||
      strokeColor != oldDelegate.strokeColor ||
      strokeWidth != oldDelegate.strokeWidth ||
      dashed != oldDelegate.dashed;
}

/// Compact header pill showing current streak or 3-day warmup progress.
class MelbelStreakPill extends StatelessWidget {
  const MelbelStreakPill({
    required this.streak,
    required this.onTap,
    super.key,
  });

  final StreakProvider streak;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final days = streak.effectiveDisplayStreak;
    final unlocked = streak.isUnlocked;
    final repair = streak.repairPending;
    final todayDone = streak.todayDone;

    final Color bg;
    final Color border;
    final Color fg;
    final IconData icon;
    final String label;

    if (repair) {
      bg = palette.amberSoft;
      border = palette.amber.withValues(alpha: 0.65);
      fg = palette.amberInk;
      icon = Icons.healing_rounded;
      label = 'Nối $days';
    } else if (!unlocked) {
      bg = palette.mutedSurface;
      border = palette.hairline;
      fg = palette.onSurfaceVariant;
      icon = Icons.eco_outlined;
      label = '$days/${StreakProvider.unlockDays}';
    } else if (todayDone) {
      bg = palette.amberSoft;
      border = palette.amber.withValues(alpha: 0.55);
      fg = palette.onAmberSoft;
      icon = Icons.pets_rounded;
      label = '$days ngày';
    } else {
      bg = palette.sageSoft;
      border = palette.sage.withValues(alpha: 0.35);
      fg = palette.onSageSoft;
      icon = Icons.pets_rounded;
      label = '$days ngày';
    }

    final semantic = repair
        ? 'Chuỗi $days ngày đang chờ nối lại nhịp'
        : !unlocked
            ? 'Khởi động mở chuỗi: $days trên ${StreakProvider.unlockDays} ngày'
            : 'Chuỗi tập trung $days ngày liên tiếp';

    return Semantics(
      button: true,
      label: semantic,
      child: Tooltip(
        message: 'Nhịp cùng Melbel',
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(999),
            onTap: () {
              HapticFeedback.selectionClick();
              onTap();
            },
            child: ConstrainedBox(
              constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
              child: Center(
                widthFactor: 1,
                child: AnimatedContainer(
                  duration: MediaQuery.disableAnimationsOf(context)
                      ? Duration.zero
                      : const Duration(milliseconds: 220),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 6,
                  ),
                  decoration: BoxDecoration(
                    color: bg,
                    borderRadius: BorderRadius.circular(999),
                    border: Border.all(color: border, width: 1),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(icon, size: 15, color: fg),
                      const SizedBox(width: 5),
                      Text(
                        label,
                        maxLines: 1,
                        softWrap: false,
                        style: theme.textTheme.labelMedium?.copyWith(
                          color: fg,
                          fontWeight: FontWeight.w700,
                          fontFeatures: const [FontFeature.tabularFigures()],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Opens the "Nhịp cùng Melbel" modal bottom sheet showing current streak,
/// 3-day unlock progress, 7-day strip, shields, 24h repair status, and
/// Melbel's 5 evolution tiers.
Future<void> showMelbelStreakSheet(
  BuildContext context,
  StreakProvider streak,
) =>
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetContext) => _MelbelStreakSheet(streak: streak),
    );

class _MelbelStreakSheet extends StatelessWidget {
  const _MelbelStreakSheet({required this.streak});

  final StreakProvider streak;

  @override
  Widget build(BuildContext context) {
    final maxH = MediaQuery.sizeOf(context).height * 0.84;
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxH),
        child: AnimatedBuilder(
          animation: streak,
          builder: (context, _) {
            final theme = Theme.of(context);
            final palette = context.melbel;
            final days = streak.effectiveDisplayStreak;
            final unlocked = streak.isUnlocked;
            final tier = streak.currentTier;
            final now = DateTime.now();

            final double progressRatio;
            final String progressLabel;
            final String progressRatioText;
            if (!unlocked) {
              progressRatio = days / StreakProvider.unlockDays;
              progressLabel =
                  'Tiến tới mở khóa Chuỗi (${StreakProvider.unlockDays} ngày)';
              progressRatioText = '$days / ${StreakProvider.unlockDays} ngày';
            } else if (days >= 100) {
              progressRatio = 1.0;
              progressLabel = 'Đã đạt mốc Đồng hành bền vững';
              progressRatioText = '$days ngày';
            } else {
              final span = (tier.nextTarget - tier.minDays).clamp(1, 999);
              progressRatio =
                  ((days - tier.minDays) / span).clamp(0.08, 1.0).toDouble();
              progressLabel = 'Tiến tới mốc ${tier.nextTarget} ngày';
              progressRatioText = '$days / ${tier.nextTarget} ngày';
            }

            final expression = streak.repairPending
                ? CatExpression.sleepy
                : streak.todayDone
                    ? CatExpression.happy
                    : CatExpression.neutral;

            return Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 4, 12, 8),
                  child: Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'NHỊP CÙNG MELBEL',
                              style: theme.textTheme.labelSmall?.copyWith(
                                color: palette.accentInk,
                                fontWeight: FontWeight.w700,
                                letterSpacing: 0.6,
                              ),
                            ),
                            Text(
                              'Chuỗi tập trung',
                              style: theme.textTheme.titleLarge?.copyWith(
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ],
                        ),
                      ),
                      IconButton(
                        tooltip: 'Đóng',
                        constraints:
                            const BoxConstraints(minWidth: 48, minHeight: 48),
                        onPressed: () => Navigator.of(context).pop(),
                        icon: const Icon(Icons.close_rounded),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
                    children: [
                MelbelCard(
                  borderRadius: 24,
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          _MelbelEvolutionEmblem(
                            tier: tier,
                            streakDays: days,
                            crownEnabled: streak.crownEnabled,
                            expression: expression,
                          ),
                          const SizedBox(width: 14),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Wrap(
                                  crossAxisAlignment: WrapCrossAlignment.end,
                                  spacing: 6,
                                  children: [
                                    Text(
                                      '$days',
                                      style: theme.textTheme.headlineLarge
                                          ?.copyWith(
                                        fontWeight: FontWeight.w800,
                                        color: palette.onSurface,
                                        fontFeatures: const [
                                          FontFeature.tabularFigures(),
                                        ],
                                      ),
                                    ),
                                    Padding(
                                      padding: const EdgeInsets.only(bottom: 4),
                                      child: Text(
                                        unlocked
                                            ? 'ngày liên tiếp'
                                            : '/ ${StreakProvider.unlockDays} ngày mở chuỗi',
                                        style:
                                            theme.textTheme.bodySmall?.copyWith(
                                          color: palette.onSurfaceVariant,
                                          fontWeight: FontWeight.w600,
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 6),
                                MelbelStatusPill(
                                  label: unlocked
                                      ? '${tier.name} · Mốc ${tier.nextTarget}'
                                      : 'Khởi động · Cần 3 ngày liên tiếp',
                                  icon: unlocked
                                      ? Icons.auto_awesome_rounded
                                      : Icons.lock_clock_outlined,
                                  tone: unlocked
                                      ? MelbelCardTone.sage
                                      : MelbelCardTone.amber,
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: palette.background,
                          borderRadius: BorderRadius.circular(16),
                          border: Border.all(color: palette.hairline),
                        ),
                        child: Text(
                          _statusMessage(streak, tier),
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: palette.onSurface,
                            height: 1.4,
                          ),
                        ),
                      ),
                      if (streak.usedFreezeNotice ||
                          streak.repairPending ||
                          streak.warmupMissedNotice ||
                          streak.softResetNotice) ...[
                        const SizedBox(height: 10),
                        _StreakNoticeBanner(streak: streak),
                      ],
                      const SizedBox(height: 14),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Flexible(
                            child: Text(
                              progressLabel,
                              style: theme.textTheme.labelMedium?.copyWith(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                          Text(
                            progressRatioText,
                            style: theme.textTheme.labelMedium?.copyWith(
                              color: palette.accentInk,
                              fontWeight: FontWeight.w700,
                              fontFeatures: const [
                                FontFeature.tabularFigures(),
                              ],
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 6),
                      MelbelAnimatedProgressBar(
                        value: progressRatio,
                        height: 8,
                        semanticsLabel: progressLabel,
                      ),
                      const SizedBox(height: 14),
                      _WeekStreakStrip(streak: streak, today: now),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: _MetricTile(
                        icon: Icons.shield_outlined,
                        value:
                            '${streak.freezeCount}/${StreakProvider.maxFreeze}',
                        label: 'Khiên ngủ nướng',
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: _MetricTile(
                        icon: Icons.calendar_today_outlined,
                        value: '${streak.activeDaysInMonth(now)}/30',
                        label: 'Ngày tháng này',
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: _MetricTile(
                        icon: Icons.workspace_premium_outlined,
                        value: '${streak.longestStreak}',
                        label: 'Kỷ lục chuỗi',
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                MelbelCard(
                  tone: MelbelCardTone.amber,
                  borderRadius: 20,
                  padding: const EdgeInsets.all(14),
                  child: Text(
                    'Quy tắc mở chuỗi: Hoàn thành ít nhất ${StreakProvider.minFocusSeconds ~/ 60} phút tập trung mỗi ngày trong ${StreakProvider.unlockDays} ngày liên tiếp để mở khóa Chuỗi và kích hoạt Khiên ngủ nướng (${streak.freezeCount}/${StreakProvider.maxFreeze}) + Nối lại nhịp 24h (còn ${streak.repairsLeftThisMonth} lượt/tháng).',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: palette.onAmberSoft,
                      height: 1.45,
                    ),
                  ),
                ),
                if (days >= 30) ...[
                  const SizedBox(height: 10),
                  MelbelCard(
                    borderRadius: 20,
                    padding: EdgeInsets.zero,
                    child: SwitchListTile(
                      value: streak.crownEnabled,
                      onChanged: (value) =>
                          unawaited(streak.setCrownEnabled(value)),
                      title: const Text('Nơ sao Bạn tri kỷ (30+ ngày)'),
                      subtitle: const Text(
                        'Hiển thị phụ kiện nhỏ trên Melbel trong thẻ chuỗi',
                      ),
                    ),
                  ),
                ],
                const SizedBox(height: 14),
                Text(
                  'Các chặng tiến hoá cùng Melbel',
                  style: theme.textTheme.titleSmall?.copyWith(
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 8),
                for (final t in StreakProvider.tiers)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: MelbelCard(
                      tone: tier.level == t.level
                          ? MelbelCardTone.sage
                          : MelbelCardTone.neutral,
                      borderRadius: 18,
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 12,
                      ),
                      child: Row(
                        children: [
                          Icon(
                            days >= t.minDays
                                ? Icons.check_circle_rounded
                                : Icons.lock_outline_rounded,
                            size: 20,
                            color: days >= t.minDays
                                ? palette.accentInk
                                : palette.onSurfaceVariant,
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  'Lv.${t.level} · ${t.name}',
                                  style: theme.textTheme.labelLarge?.copyWith(
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  t.description,
                                  style: theme.textTheme.bodySmall?.copyWith(
                                    color: palette.onSurfaceVariant,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            days >= t.minDays ? 'Đã mở' : '${t.minDays} ngày',
                            style: theme.textTheme.labelMedium?.copyWith(
                              color: palette.accentInk,
                              fontWeight: FontWeight.w700,
                              fontFeatures: const [
                                FontFeature.tabularFigures(),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ],
      );
          },
        ),
      ),
    );
  }

  static String _statusMessage(StreakProvider streak, StreakTier tier) {
    if (streak.usedFreezeNotice) {
      return 'Hôm qua mình nghỉ ngơi một chút, Khiên ngủ nướng đã giữ trọn chuỗi giúp bạn rồi nhé.';
    }
    if (streak.repairPending) {
      return 'Chuỗi ${streak.repairPreviousStreak} ngày đang chờ bạn thắp lại! Chỉ cần 2 phút khởi động hôm nay thôi.';
    }
    if (streak.warmupMissedNotice) {
      return 'Chuỗi chưa mở (dưới 3 ngày) nên mình khởi động lại 0/3 thật nhẹ, Khiên ngủ nướng vẫn còn nguyên!';
    }
    if (streak.softResetNotice) {
      return 'Chào mừng bạn quay lại! Thành quả tháng này và kỷ lục ${streak.longestStreak} ngày vẫn ở nguyên đây.';
    }
    if (streak.todayDone) {
      final mins = streak.dayMinutes(DateTime.now());
      return 'Hôm nay bạn đã tập trung $mins phút. ${tier.whisper}';
    }
    return tier.whisper;
  }
}

class _MelbelEvolutionEmblem extends StatelessWidget {
  const _MelbelEvolutionEmblem({
    required this.tier,
    required this.streakDays,
    required this.crownEnabled,
    required this.expression,
  });

  final StreakTier tier;
  final int streakDays;
  final bool crownEnabled;
  final CatExpression expression;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final unlocked = streakDays >= StreakProvider.unlockDays;
    final outerBg = !unlocked
        ? palette.mutedSurface
        : tier.level >= 2
            ? palette.sageSoft
            : palette.amberSoft;
    final borderColor = !unlocked
        ? palette.hairline
        : tier.level >= 3
            ? palette.amber.withValues(alpha: 0.75)
            : palette.sage.withValues(alpha: 0.45);

    return SizedBox(
      width: 92,
      height: 92,
      child: Stack(
        alignment: Alignment.center,
        clipBehavior: Clip.none,
        children: [
          AnimatedContainer(
            duration: MediaQuery.disableAnimationsOf(context)
                ? Duration.zero
                : const Duration(milliseconds: 240),
            width: 86,
            height: 86,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: outerBg,
              border: Border.all(
                color: borderColor,
                width: tier.level >= 4 ? 2.4 : 1.5,
              ),
            ),
          ),
          MelbelMascot(
            expression: expression,
            width: 78,
            height: 68,
          ),
          if (tier.level >= 2)
            Positioned(
              left: 0,
              bottom: 6,
              child: IgnorePointer(
                child: Icon(
                  Icons.eco_rounded,
                  size: 18,
                  color: palette.accentInk,
                ),
              ),
            ),
          if (streakDays >= 30 && crownEnabled)
            Positioned(
              top: -4,
              child: IgnorePointer(
                child: Container(
                  padding: const EdgeInsets.all(3),
                  decoration: BoxDecoration(
                    color: palette.amberSoft,
                    shape: BoxShape.circle,
                    border: Border.all(color: palette.amber, width: 1),
                  ),
                  child: Icon(
                    Icons.star_rounded,
                    size: 14,
                    color: palette.amberInk,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _StreakNoticeBanner extends StatelessWidget {
  const _StreakNoticeBanner({required this.streak});

  final StreakProvider streak;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final MelbelCardTone tone;
    final String text;
    if (streak.usedFreezeNotice) {
      tone = MelbelCardTone.sage;
      text =
          'Khiên ngủ nướng đã tự động bật hôm qua (còn ${streak.freezeCount}/${StreakProvider.maxFreeze}).';
    } else if (streak.repairPending) {
      tone = MelbelCardTone.amber;
      text =
          'Chờ nối lại nhịp 24h: làm ≥ 2 phút hôm nay để nối chuỗi ${streak.repairPreviousStreak} ngày!';
    } else if (streak.warmupMissedNotice) {
      tone = MelbelCardTone.muted;
      text =
          'Chưa đủ 3 ngày mở chuỗi nên Khiên ngủ nướng (${streak.freezeCount}/${StreakProvider.maxFreeze}) vẫn còn nguyên.';
    } else {
      tone = MelbelCardTone.muted;
      text = 'Bắt đầu nhịp mới thật nhẹ nhàng — thành quả cũ vẫn được giữ.';
    }

    return MelbelCard(
      tone: tone,
      borderRadius: 16,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      child: Text(
        text,
        style: theme.textTheme.labelMedium?.copyWith(
          color: tone == MelbelCardTone.amber
              ? palette.onAmberSoft
              : palette.onSurface,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

class _WeekStreakStrip extends StatelessWidget {
  const _WeekStreakStrip({required this.streak, required this.today});

  final StreakProvider streak;
  final DateTime today;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final mondayOffset = today.weekday - 1;
    final weekStart = DateTime(
      today.year,
      today.month,
      today.day - mondayOffset,
    );
    const labels = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];

    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        for (var i = 0; i < 7; i++)
          () {
            final day = DateTime(
              weekStart.year,
              weekStart.month,
              weekStart.day + i,
            );
            final status = streak.dayStatus(day);
            final isToday = DateUtils.isSameDay(day, today);
            final fillColor = switch (status) {
              StreakDayKind.done => palette.sage,
              StreakDayKind.freeze => palette.surface,
              StreakDayKind.repair => palette.amberSoft,
              null => isToday ? palette.amberSoft : palette.mutedSurface,
            };
            final strokeColor = switch (status) {
              StreakDayKind.freeze => palette.sage,
              StreakDayKind.repair => palette.amber,
              StreakDayKind.done => null,
              null => isToday ? palette.amber : null,
            };

            return Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(
                  width: 30,
                  height: 32,
                  child: CustomPaint(
                    painter: MelbelPawPainter(
                      fillColor: fillColor,
                      strokeColor: strokeColor,
                      strokeWidth: strokeColor != null ? 2.2 : 0,
                      dashed: status == StreakDayKind.repair,
                    ),
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  labels[i],
                  style: theme.textTheme.labelSmall?.copyWith(
                    fontWeight: isToday ? FontWeight.w700 : FontWeight.w500,
                    color: isToday
                        ? palette.amberInk
                        : palette.onSurfaceVariant,
                  ),
                ),
              ],
            );
          }(),
      ],
    );
  }
}

class _MetricTile extends StatelessWidget {
  const _MetricTile({
    required this.icon,
    required this.value,
    required this.label,
  });

  final IconData icon;
  final String value;
  final String label;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    return MelbelCard(
      borderRadius: 18,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 10),
      child: Column(
        children: [
          Icon(icon, size: 17, color: palette.accentInk),
          const SizedBox(height: 4),
          Text(
            value,
            maxLines: 1,
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w800,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.labelSmall?.copyWith(
              color: palette.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }
}

/// Calm celebration sheet shown once per day upon completing a focus session
/// (or 2-minute kickoff) that advances warmup or active streak progress.
Future<void> showMelbelStreakCelebration(
  BuildContext context,
  StreakCelebrationEvent event,
) {
  unawaited(MelbelSoundService.instance.playMeow(volume: 0.6));
  final unlocked = event.streakDays >= StreakProvider.unlockDays;
  final eyebrow = event.justUnlocked
      ? 'CHÍNH THỨC MỞ KHÓA CHUỖI'
      : event.restoredFromRepair
          ? 'ĐÃ NỐI LẠI NHỊP THÀNH CÔNG'
          : !unlocked
              ? 'KHỞI ĐỘNG NGÀY ${event.streakDays}/${StreakProvider.unlockDays}'
              : event.evolved
                  ? 'LÊN MỐC MỚI · ${event.tier.name.toUpperCase()}'
                  : 'ĐÃ THẮP SÁNG NHỊP HÔM NAY';
  final headline = event.justUnlocked
      ? 'Mở khóa Chuỗi tập trung!'
      : event.restoredFromRepair
          ? 'Chuỗi cũ đã được nối lại!'
          : !unlocked
              ? 'Còn ${StreakProvider.unlockDays - event.streakDays} ngày nữa để mở chuỗi!'
              : event.evolved
                  ? 'Chạm mốc ${event.tier.name}!'
                  : 'Nhịp hôm nay đã sáng rồi!';
  final bigCount = unlocked
      ? '${event.streakDays} ngày'
      : '${event.streakDays}/${StreakProvider.unlockDays} ngày';
  final double barValue = !unlocked
      ? event.streakDays / StreakProvider.unlockDays
      : event.streakDays >= 100
          ? 1.0
          : ((event.streakDays - event.tier.minDays) /
                  (event.tier.nextTarget - event.tier.minDays).clamp(1, 999))
              .clamp(0.1, 1.0)
              .toDouble();

  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (sheetContext) {
      final theme = Theme.of(sheetContext);
      final palette = sheetContext.melbel;
      return SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
          child: MelbelEntrance(
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const MelbelMascot(
                    expression: CatExpression.happy,
                    width: 108,
                    height: 94,
                  ),
                  const SizedBox(height: 10),
                  Text(
                    eyebrow,
                    textAlign: TextAlign.center,
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: palette.accentInk,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.6,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    bigCount,
                    style: theme.textTheme.displaySmall?.copyWith(
                      color: palette.accentInk,
                      fontWeight: FontWeight.w800,
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    headline,
                    textAlign: TextAlign.center,
                    style: theme.textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    event.tier.whisper,
                    textAlign: TextAlign.center,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: palette.onSurfaceVariant,
                    ),
                  ),
                  if (event.earnedFreeze) ...[
                    const SizedBox(height: 12),
                    const MelbelStatusPill(
                      label: 'Tặng thêm +1 Khiên ngủ nướng',
                      icon: Icons.shield_outlined,
                      tone: MelbelCardTone.amber,
                    ),
                  ],
                  const SizedBox(height: 16),
                  MelbelAnimatedProgressBar(
                    value: barValue,
                    height: 8,
                    semanticsLabel: 'Tiến độ chuỗi tập trung',
                  ),
                  const SizedBox(height: 18),
                  PrimaryCta(
                    label: 'Tiếp tục cùng Melbel',
                    onPressed: () => Navigator.of(sheetContext).pop(),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    },
  );
}
