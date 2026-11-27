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
      backgroundColor: context.melbel.background,
      builder: (sheetContext) => _MelbelStreakSheet(streak: streak),
    );

class _MelbelStreakSheet extends StatefulWidget {
  const _MelbelStreakSheet({required this.streak});

  final StreakProvider streak;

  @override
  State<_MelbelStreakSheet> createState() => _MelbelStreakSheetState();
}

class _MelbelStreakSheetState extends State<_MelbelStreakSheet> {
  bool _showTiers = false;

  @override
  Widget build(BuildContext context) {
    final maxH = MediaQuery.sizeOf(context).height * 0.88;
    final theme = Theme.of(context);
    final reducedMotion = MediaQuery.disableAnimationsOf(context);

    return SafeArea(
      top: false,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxH),
        child: AnimatedBuilder(
          animation: widget.streak,
          builder: (context, _) {
            final streak = widget.streak;
            final palette = context.melbel;
            final isDark = Theme.of(context).brightness == Brightness.dark;
            final days = streak.effectiveDisplayStreak;
            final unlocked = streak.isUnlocked;
            final tier = streak.currentTier;
            final now = DateTime.now();

            final double progressRatio;
            final String progressLabel;
            final String progressRatioText;
            if (!unlocked) {
              progressRatio = days / StreakProvider.unlockDays;
              progressLabel = 'Mở chuỗi';
              progressRatioText = '$days / ${StreakProvider.unlockDays}';
            } else if (days >= 100) {
              progressRatio = 1.0;
              progressLabel = 'Bền vững';
              progressRatioText = '$days ngày';
            } else {
              final span = (tier.nextTarget - tier.minDays).clamp(1, 999);
              progressRatio = ((days - tier.minDays) / span)
                  .clamp(0.08, 1.0)
                  .toDouble();
              progressLabel = 'Mốc ${tier.nextTarget} ngày';
              progressRatioText = '$days / ${tier.nextTarget}';
            }

            final expression = streak.repairPending
                ? CatExpression.sleepy
                : streak.todayDone
                    ? CatExpression.happy
                    : CatExpression.neutral;

            final hasAlertNotice = streak.usedFreezeNotice ||
                streak.repairPending ||
                streak.warmupMissedNotice ||
                streak.softResetNotice;

            Widget buildHeaderButton({
              required String tooltip,
              required IconData icon,
              required VoidCallback onPressed,
            }) =>
                IconButton(
                  tooltip: tooltip,
                  constraints: const BoxConstraints(
                    minWidth: 48,
                    minHeight: 48,
                  ),
                  padding: EdgeInsets.zero,
                  onPressed: onPressed,
                  icon: Container(
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: palette.mutedSurface,
                      borderRadius: BorderRadius.circular(14),
                    ),
                    alignment: Alignment.center,
                    child: Icon(icon, size: 20, color: palette.onSurface),
                  ),
                );

            return Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 0, 12, 8),
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
                            Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Flexible(
                                  child: Text(
                                    'Chuỗi tập trung',
                                    style: theme.textTheme.titleLarge?.copyWith(
                                      fontWeight: FontWeight.w800,
                                      color: palette.onSurface,
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 6),
                                Transform.rotate(
                                  angle: 0.22,
                                  child: MelbelDoodleRays(
                                    size: 16,
                                    color: palette.accentInk
                                        .withValues(alpha: 0.72),
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                      buildHeaderButton(
                        tooltip: 'Cách hoạt động',
                        icon: Icons.help_outline_rounded,
                        onPressed: () => showMelbelStreakGuide(
                          context,
                          streak: streak,
                        ),
                      ),
                      const SizedBox(width: 4),
                      buildHeaderButton(
                        tooltip: 'Đóng',
                        icon: Icons.close_rounded,
                        onPressed: () => Navigator.of(context).pop(),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(18, 4, 18, 24),
                    children: [
                      MelbelCard(
                        tone: MelbelCardTone.neutral,
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
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Row(
                                        children: [
                                          Expanded(
                                            child: Wrap(
                                              crossAxisAlignment:
                                                  WrapCrossAlignment.end,
                                              spacing: 6,
                                              children: [
                                                Text(
                                                  '$days',
                                                  style: theme
                                                      .textTheme.displaySmall
                                                      ?.copyWith(
                                                    fontWeight: FontWeight.w800,
                                                    height: 1.0,
                                                    color: palette.onSurface,
                                                    fontFeatures: const [
                                                      FontFeature
                                                          .tabularFigures(),
                                                    ],
                                                  ),
                                                ),
                                                Padding(
                                                  padding:
                                                      const EdgeInsets.only(
                                                    bottom: 4,
                                                  ),
                                                  child: Text(
                                                    unlocked
                                                        ? 'ngày'
                                                        : '/ ${StreakProvider.unlockDays} ngày',
                                                    style: theme
                                                        .textTheme.bodyMedium
                                                        ?.copyWith(
                                                      color: palette
                                                          .onSurfaceVariant,
                                                      fontWeight:
                                                          FontWeight.w700,
                                                    ),
                                                  ),
                                                ),
                                              ],
                                            ),
                                          ),
                                          Opacity(
                                            opacity: isDark ? 0.22 : 0.16,
                                            child: SizedBox(
                                              width: 24,
                                              height: 26,
                                              child: CustomPaint(
                                                painter: MelbelPawPainter(
                                                  fillColor: palette.accentInk,
                                                ),
                                              ),
                                            ),
                                          ),
                                        ],
                                      ),
                                      const SizedBox(height: 6),
                                      MelbelStatusPill(
                                        label: unlocked
                                            ? tier.name
                                            : 'Khởi động · 3 ngày',
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
                            if (hasAlertNotice)
                              _StreakNoticeBanner(streak: streak)
                            else
                              _ShortCuePill(message: _shortCue(streak, now)),
                            const SizedBox(height: 14),
                            Row(
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                                Flexible(
                                  child: Text(
                                    progressLabel,
                                    style:
                                        theme.textTheme.labelMedium?.copyWith(
                                      fontWeight: FontWeight.w700,
                                      color: palette.onSurface,
                                    ),
                                  ),
                                ),
                                Text(
                                  progressRatioText,
                                  style: theme.textTheme.labelMedium?.copyWith(
                                    color: palette.accentInk,
                                    fontWeight: FontWeight.w800,
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
                              height: 10,
                              semanticsLabel: progressLabel,
                            ),
                            const SizedBox(height: 14),
                            _WeekStreakStrip(
                              streak: streak,
                              today: now,
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 10),
                      IntrinsicHeight(
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Expanded(
                              child: _MetricTile(
                                icon: Icons.shield_outlined,
                                value:
                                    '${streak.freezeCount}/${StreakProvider.maxFreeze}',
                                label: 'Khiên',
                              ),
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: _MetricTile(
                                icon: Icons.calendar_today_outlined,
                                value: '${streak.activeDaysInMonth(now)}/30',
                                label: 'Tháng này',
                              ),
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: _MetricTile(
                                icon: Icons.workspace_premium_outlined,
                                value: '${streak.longestStreak}',
                                label: 'Kỷ lục',
                              ),
                            ),
                          ],
                        ),
                      ),
                      if (!unlocked) ...[
                        const SizedBox(height: 10),
                        MelbelCard(
                          tone: MelbelCardTone.neutral,
                          borderRadius: 20,
                          padding: EdgeInsets.zero,
                          child: SwitchListTile(
                            secondary: Icon(
                              streak.introDismissed
                                  ? Icons.push_pin_outlined
                                  : Icons.push_pin_rounded,
                              color: palette.accentInk,
                            ),
                            value: !streak.introDismissed,
                            onChanged: (show) => unawaited(
                              streak.setIntroDismissed(!show),
                            ),
                            title: Text(
                              'Hiện thẻ ở Trang chủ',
                              style: theme.textTheme.labelLarge?.copyWith(
                                fontWeight: FontWeight.w700,
                                color: palette.onSurface,
                              ),
                            ),
                          ),
                        ),
                      ],
                      const SizedBox(height: 10),
                      Semantics(
                        label:
                            'Quy tắc chuỗi: tối thiểu 2 phút mỗi ngày, ${StreakProvider.unlockDays} ngày mở chuỗi, ${streak.freezeCount}/${StreakProvider.maxFreeze} khiên ngủ nướng, còn ${streak.repairsLeftThisMonth} lượt nối 24 giờ',
                        child: ExcludeSemantics(
                          child: MelbelCard(
                            tone: MelbelCardTone.amber,
                            elevated: false,
                            borderRadius: 20,
                            padding: const EdgeInsets.symmetric(
                              horizontal: 12,
                              vertical: 10,
                            ),
                            child: Row(
                              children: [
                                Container(
                                  width: 30,
                                  height: 30,
                                  decoration: BoxDecoration(
                                    color:
                                        palette.amber.withValues(alpha: 0.22),
                                    shape: BoxShape.circle,
                                  ),
                                  child: Icon(
                                    Icons.bolt_rounded,
                                    size: 18,
                                    color: palette.amberInk,
                                  ),
                                ),
                                const SizedBox(width: 10),
                                Expanded(
                                  child: Wrap(
                                    spacing: 8,
                                    runSpacing: 6,
                                    children: [
                                      const _RuleMiniChip(
                                        icon: Icons.timer_outlined,
                                        label: '≥ 2p/ngày',
                                      ),
                                      _RuleMiniChip(
                                        icon: Icons.shield_outlined,
                                        label:
                                            '${streak.freezeCount}/${StreakProvider.maxFreeze}',
                                      ),
                                      _RuleMiniChip(
                                        icon: Icons.replay_rounded,
                                        label:
                                            '${streak.repairsLeftThisMonth} lượt',
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                      if (days >= 30) ...[
                        const SizedBox(height: 10),
                        MelbelCard(
                          tone: MelbelCardTone.neutral,
                          borderRadius: 20,
                          padding: EdgeInsets.zero,
                          child: SwitchListTile(
                            value: streak.crownEnabled,
                            onChanged: (value) => unawaited(
                              streak.setCrownEnabled(value),
                            ),
                            title: Text(
                              'Nơ sao Bạn tri kỷ (30+ ngày)',
                              style: theme.textTheme.labelLarge?.copyWith(
                                fontWeight: FontWeight.w700,
                                color: palette.onSurface,
                              ),
                            ),
                          ),
                        ),
                      ],
                      const SizedBox(height: 10),
                      MelbelCard(
                        tone: MelbelCardTone.neutral,
                        borderRadius: 20,
                        padding: EdgeInsets.zero,
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            InkWell(
                              borderRadius: BorderRadius.circular(20),
                              onTap: () {
                                HapticFeedback.selectionClick();
                                setState(() => _showTiers = !_showTiers);
                              },
                              child: ConstrainedBox(
                                constraints: const BoxConstraints(minHeight: 52),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 14,
                                    vertical: 10,
                                  ),
                                  child: Row(
                                    children: [
                                      Container(
                                        width: 30,
                                        height: 30,
                                        decoration: BoxDecoration(
                                          color: palette.successSoft,
                                          shape: BoxShape.circle,
                                        ),
                                        child: Icon(
                                          Icons.eco_rounded,
                                          size: 17,
                                          color: palette.successInk,
                                        ),
                                      ),
                                      const SizedBox(width: 10),
                                      Expanded(
                                        child: Text(
                                          'Các chặng tiến hoá cùng Melbel',
                                          style: theme.textTheme.titleSmall
                                              ?.copyWith(
                                            fontWeight: FontWeight.w700,
                                            color: palette.onSurface,
                                          ),
                                        ),
                                      ),
                                      AnimatedRotation(
                                        turns: _showTiers ? 0.25 : 0.0,
                                        duration: reducedMotion
                                            ? Duration.zero
                                            : const Duration(milliseconds: 200),
                                        child: Icon(
                                          Icons.chevron_right_rounded,
                                          color: palette.onSurfaceVariant,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                            AnimatedSize(
                              duration: reducedMotion
                                  ? Duration.zero
                                  : const Duration(milliseconds: 220),
                              curve: Curves.easeOutCubic,
                              alignment: Alignment.topCenter,
                              child: !_showTiers
                                  ? const SizedBox(
                                      width: double.infinity,
                                      height: 0,
                                    )
                                  : Padding(
                                      padding: const EdgeInsets.fromLTRB(
                                        12,
                                        0,
                                        12,
                                        12,
                                      ),
                                      child: Column(
                                        children: [
                                          for (final t in StreakProvider.tiers)
                                            Padding(
                                              padding: const EdgeInsets.only(
                                                top: 6,
                                              ),
                                              child: Container(
                                                padding:
                                                    const EdgeInsets.symmetric(
                                                  horizontal: 12,
                                                  vertical: 9,
                                                ),
                                                decoration: BoxDecoration(
                                                  color: tier.level == t.level
                                                      ? palette.sageSoft
                                                      : palette.mutedSurface,
                                                  borderRadius:
                                                      BorderRadius.circular(14),
                                                ),
                                                child: Row(
                                                  children: [
                                                    Icon(
                                                      days >= t.minDays
                                                          ? Icons
                                                              .check_circle_rounded
                                                          : Icons
                                                              .lock_outline_rounded,
                                                      size: 17,
                                                      color: days >= t.minDays
                                                          ? palette.accentInk
                                                          : palette
                                                              .onSurfaceVariant,
                                                    ),
                                                    const SizedBox(width: 8),
                                                    Expanded(
                                                      child: Text(
                                                        'Lv.${t.level} · ${t.name}',
                                                        style: theme.textTheme
                                                            .labelMedium
                                                            ?.copyWith(
                                                          fontWeight:
                                                              FontWeight.w700,
                                                          color:
                                                              palette.onSurface,
                                                        ),
                                                      ),
                                                    ),
                                                    const SizedBox(width: 8),
                                                    Text(
                                                      days >= t.minDays
                                                          ? 'Đã mở'
                                                          : '${t.minDays} ngày',
                                                      style: theme
                                                          .textTheme.labelSmall
                                                          ?.copyWith(
                                                        color:
                                                            palette.accentInk,
                                                        fontWeight:
                                                            FontWeight.w700,
                                                        fontFeatures: const [
                                                          FontFeature
                                                              .tabularFigures(),
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
                            ),
                          ],
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

  static String _shortCue(StreakProvider streak, DateTime now) {
    final days = streak.effectiveDisplayStreak;
    if (streak.todayDone) {
      return 'Hôm nay đã tập trung ${streak.dayMinutes(now)} phút!';
    }
    if (!streak.isUnlocked) {
      if (days == 0) {
        return 'Chạm ≥ 2 phút hôm nay cùng Melbel!';
      }
      return 'Còn ${StreakProvider.unlockDays - days} ngày nữa mở chuỗi';
    }
    return 'Giữ nhịp ≥ 2 phút hôm nay nhé!';
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
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final unlocked = streakDays >= StreakProvider.unlockDays;
    final outerBg = isDark
        ? MelbelMascot.darkBackdropColor
        : !unlocked
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
            showBackdrop: false,
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
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            text,
            style: theme.textTheme.labelMedium?.copyWith(
              color: tone == MelbelCardTone.amber
                  ? palette.onAmberSoft
                  : palette.onSurface,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (streak.canRepairNow) ...[
            const SizedBox(height: 10),
            PrimaryCta(
              label: streak.isRepairing
                  ? 'Đang nối lại nhịp…'
                  : 'Nối lại nhịp 24h ngay (còn ${streak.repairsLeftThisMonth} lượt)',
              icon: Icons.healing_rounded,
              height: 48,
              onPressed: streak.isRepairing
                  ? null
                  : () async {
                      final messenger = ScaffoldMessenger.maybeOf(context);
                      final err = await streak.repairWithBackend();
                      if (!context.mounted) return;
                      messenger?.showSnackBar(
                        SnackBar(
                          content: Text(
                            err ??
                                'Đã nối lại chuỗi ${streak.effectiveDisplayStreak} ngày!',
                          ),
                          behavior: SnackBarBehavior.floating,
                        ),
                      );
                    },
            ),
          ],
        ],
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

            return Expanded(
              child: FittedBox(
                fit: BoxFit.scaleDown,
                alignment: Alignment.center,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    SizedBox(
                      width: 32,
                      height: 34,
                      child: Stack(
                        clipBehavior: Clip.none,
                        alignment: Alignment.bottomCenter,
                        children: [
                          if (isToday)
                            Positioned(
                              top: -4,
                              child: IgnorePointer(
                                child: CustomPaint(
                                  size: const Size(24, 9),
                                  painter:
                                      _MiniPawRaysPainter(color: palette.amber),
                                ),
                              ),
                            ),
                          SizedBox(
                            width: 28,
                            height: 30,
                            child: CustomPaint(
                              painter: MelbelPawPainter(
                                fillColor: fillColor,
                                strokeColor: strokeColor,
                                strokeWidth: strokeColor != null ? 2.2 : 0,
                                dashed: status == StreakDayKind.repair,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 3),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 5,
                        vertical: 1,
                      ),
                      decoration: isToday
                          ? BoxDecoration(
                              color: palette.amberSoft,
                              borderRadius: BorderRadius.circular(999),
                            )
                          : null,
                      child: Text(
                        labels[i],
                        style: theme.textTheme.labelSmall?.copyWith(
                          fontWeight:
                              isToday ? FontWeight.w800 : FontWeight.w500,
                          color: isToday
                              ? palette.amberInk
                              : palette.onSurfaceVariant,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
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
      tone: MelbelCardTone.neutral,
      borderRadius: 20,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, size: 18, color: palette.accentInk),
          const SizedBox(height: 4),
          Text(
            value,
            maxLines: 1,
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w800,
              color: palette.onSurface,
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
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _ShortCuePill extends StatelessWidget {
  const _ShortCuePill({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    return MelbelCard(
      tone: MelbelCardTone.sage,
      elevated: false,
      borderRadius: 16,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      child: Row(
        children: [
          Container(
            width: 28,
            height: 28,
            decoration: BoxDecoration(
              color: palette.surface,
              borderRadius: BorderRadius.circular(9),
            ),
            child: Icon(
              Icons.calendar_today_rounded,
              size: 15,
              color: palette.accentInk,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodySmall?.copyWith(
                color: palette.onSageSoft,
                fontWeight: FontWeight.w600,
                height: 1.35,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _RuleMiniChip extends StatelessWidget {
  const _RuleMiniChip({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: palette.surface,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: palette.hairline),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 13, color: palette.amberInk),
          const SizedBox(width: 4),
          Text(
            label,
            style: theme.textTheme.labelSmall?.copyWith(
              color: palette.onAmberSoft,
              fontWeight: FontWeight.w700,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ],
      ),
    );
  }
}

class _MiniPawRaysPainter extends CustomPainter {
  const _MiniPawRaysPainter({required this.color});

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.6
      ..strokeCap = StrokeCap.round;
    final cx = size.width / 2;
    canvas.drawLine(Offset(cx, 1), Offset(cx, 6), paint);
    canvas.drawLine(Offset(cx - 8, 3.5), Offset(cx - 4.5, 7), paint);
    canvas.drawLine(Offset(cx + 8, 3.5), Offset(cx + 4.5, 7), paint);
  }

  @override
  bool shouldRepaint(_MiniPawRaysPainter oldDelegate) =>
      oldDelegate.color != color;
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

/// Dismissible first-time feature introduction card for [HomeScreen].
class MelbelStreakIntroCard extends StatelessWidget {
  const MelbelStreakIntroCard({
    required this.streak,
    required this.onLearnMore,
    required this.onDismiss,
    super.key,
  });

  final StreakProvider streak;
  final VoidCallback onLearnMore;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    final days = streak.effectiveDisplayStreak;
    return MelbelCard(
      tone: MelbelCardTone.sage,
      borderRadius: 24,
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Flexible(
                child: MelbelStatusPill(
                  label: 'Mới · Nhịp cùng Melbel',
                  icon: Icons.auto_awesome_rounded,
                  tone: MelbelCardTone.amber,
                ),
              ),
              const SizedBox(width: 8),
              MelbelIconObjectButton(
                icon: Icons.visibility_off_outlined,
                tooltip: 'Ẩn thẻ Streak',
                size: 44,
                tone: MelbelCardTone.neutral,
                onPressed: onDismiss,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'Giữ 3 ngày liên tiếp để mở Streak',
            style: theme.textTheme.titleMedium?.copyWith(
              fontWeight: FontWeight.w700,
              color: palette.onSageSoft,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            '≥ 2 phút/ngày · có Khiên & nối nhịp 24h.',
            style: theme.textTheme.bodySmall?.copyWith(
              color: palette.onSurfaceVariant,
              height: 1.4,
            ),
          ),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: Wrap(
              spacing: 12,
              runSpacing: 8,
              alignment: WrapAlignment.spaceBetween,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    SizedBox(
                      width: 24,
                      height: 26,
                      child: CustomPaint(
                        painter: MelbelPawPainter(
                          fillColor: palette.amberSoft,
                          strokeColor: palette.amber,
                          strokeWidth: 2.2,
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      '$days/${StreakProvider.unlockDays} ngày',
                      style: theme.textTheme.labelLarge?.copyWith(
                        fontWeight: FontWeight.w700,
                        color: palette.accentInk,
                        fontFeatures: const [FontFeature.tabularFigures()],
                      ),
                    ),
                  ],
                ),
                MelbelIconObjectButton(
                  icon: Icons.auto_stories_rounded,
                  tooltip: 'Xem giới thiệu',
                  size: 48,
                  tone: MelbelCardTone.neutral,
                  onPressed: onLearnMore,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Illustrated bottom sheet introducing the 3-day unlock rule, ADHD-safe
/// protection layers, and Melbel's evolution milestones.
Future<void> showMelbelStreakGuide(
  BuildContext context, {
  StreakProvider? streak,
  bool showOpenStreakCta = false,
}) =>
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetContext) {
        final theme = Theme.of(sheetContext);
        final palette = sheetContext.melbel;
        final maxH = MediaQuery.sizeOf(sheetContext).height * 0.84;
        return SafeArea(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxHeight: maxH),
            child: Column(
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
                              'GIỚI THIỆU TÍNH NĂNG',
                              style: theme.textTheme.labelSmall?.copyWith(
                                color: palette.accentInk,
                                fontWeight: FontWeight.w700,
                                letterSpacing: 0.6,
                              ),
                            ),
                            Text(
                              'Nhịp cùng Melbel (Streak)',
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
                        onPressed: () => Navigator.of(sheetContext).pop(),
                        icon: const Icon(Icons.close_rounded),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
                    children: [
                      MelbelCard(
                        tone: MelbelCardTone.sage,
                        borderRadius: 22,
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          children: [
                            const MelbelMascot(
                              expression: CatExpression.happy,
                              width: 88,
                              height: 78,
                            ),
                            const SizedBox(height: 12),
                            Wrap(
                              spacing: 10,
                              runSpacing: 8,
                              alignment: WrapAlignment.center,
                              children: [
                                _GuidePawLegendChip(
                                  label: 'Giữ nhịp',
                                  fill: palette.sage,
                                ),
                                _GuidePawLegendChip(
                                  label: 'Khiên ngủ nướng',
                                  fill: palette.surface,
                                  stroke: palette.sage,
                                ),
                                _GuidePawLegendChip(
                                  label: 'Chờ nối 24h',
                                  fill: palette.amberSoft,
                                  stroke: palette.amber,
                                  dashed: true,
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 12),
                      const _GuideRuleCard(
                        step: '1',
                        title: '3 ngày liên tiếp mở chuỗi (≥ 2 phút/ngày)',
                        description:
                            'Làm ≥ 2 phút/ngày trong 3 ngày liên tiếp để bật chuỗi.',
                      ),
                      const _GuideRuleCard(
                        step: '2',
                        title: 'Tầng 1 · Khiên ngủ nướng tự động',
                        description:
                            'Lỡ 1 ngày sẽ tự bật Khiên giữ chuỗi (tối đa 2 khiên).',
                      ),
                      const _GuideRuleCard(
                        step: '3',
                        title: 'Tầng 2 · Nối lại nhịp trong 24h',
                        description:
                            'Hết khiên vẫn có 24h làm ≥ 2 phút để nối tiếp (2 lượt/tháng).',
                      ),
                      const _GuideRuleCard(
                        step: '4',
                        title: 'Tầng 3 · Tiến hoá Melbel & Giữ thành quả',
                        description:
                            '5 mốc tiến hoá (3–100 ngày). Nghỉ dài vẫn giữ kỷ lục.',
                      ),
                      const SizedBox(height: 8),
                      PrimaryCta(
                        label: 'Đã hiểu · Bắt đầu giữ nhịp',
                        onPressed: () {
                          if (streak != null) {
                            unawaited(streak.dismissIntro());
                          }
                          Navigator.of(sheetContext).pop();
                        },
                      ),
                      if (showOpenStreakCta && streak != null) ...[
                        const SizedBox(height: 10),
                        SecondaryCta(
                          label: 'Xem chuỗi của tôi',
                          onPressed: () {
                            Navigator.of(sheetContext).pop();
                            showMelbelStreakSheet(context, streak);
                          },
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );

class _GuidePawLegendChip extends StatelessWidget {
  const _GuidePawLegendChip({
    required this.label,
    required this.fill,
    this.stroke,
    this.dashed = false,
  });

  final String label;
  final Color fill;
  final Color? stroke;
  final bool dashed;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: palette.surface,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: palette.hairline),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 14,
            height: 16,
            child: CustomPaint(
              painter: MelbelPawPainter(
                fillColor: fill,
                strokeColor: stroke,
                strokeWidth: stroke != null ? 2.4 : 0,
                dashed: dashed,
              ),
            ),
          ),
          const SizedBox(width: 6),
          Text(
            label,
            style: theme.textTheme.labelSmall?.copyWith(
              fontWeight: FontWeight.w600,
              color: palette.onSurface,
            ),
          ),
        ],
      ),
    );
  }
}

class _GuideRuleCard extends StatelessWidget {
  const _GuideRuleCard({
    required this.step,
    required this.title,
    required this.description,
  });

  final String step;
  final String title;
  final String description;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: MelbelCard(
        borderRadius: 20,
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 28,
              height: 28,
              decoration: BoxDecoration(
                color: palette.sageSoft,
                shape: BoxShape.circle,
              ),
              alignment: Alignment.center,
              child: Text(
                step,
                style: theme.textTheme.labelLarge?.copyWith(
                  fontWeight: FontWeight.w800,
                  color: palette.accentInk,
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: theme.textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    description,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: palette.onSurfaceVariant,
                      height: 1.4,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
