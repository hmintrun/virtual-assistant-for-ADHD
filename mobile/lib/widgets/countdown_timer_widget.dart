import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../core/theme.dart';

/// GPU-friendly 1-Step UI circular focus timer ring for UF3.
///
/// Renders inside a [RepaintBoundary] using a lightweight [CustomPainter] and
/// tabular numerals so digits never jitter horizontally at 60 FPS.
class MelbelFocusTimerRing extends StatelessWidget {
  const MelbelFocusTimerRing({
    required this.timeText,
    required this.progress,
    required this.subtitle,
    this.isBreak = false,
    this.isOffline = false,
    this.size = 224,
    super.key,
  });

  final String timeText;
  final double progress;
  final String subtitle;
  final bool isBreak;
  final bool isOffline;
  final double size;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final ringColor = isBreak ? palette.amber : palette.sage;
    final trackColor = isBreak ? palette.amberSoft : palette.sageSoft;
    final clamped = progress.clamp(0.0, 1.0).toDouble();

    return Semantics(
      container: true,
      excludeSemantics: true,
      label: isOffline ? '$subtitle · Ngoại tuyến' : subtitle,
      value: 'Còn lại $timeText',
      child: Center(
        child: RepaintBoundary(
          child: SizedBox(
            width: size,
            height: size,
            child: CustomPaint(
              painter: _FocusRingPainter(
                progress: clamped,
                ringColor: ringColor,
                trackColor: trackColor,
                strokeWidth: 10,
              ),
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 20),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      FittedBox(
                        fit: BoxFit.scaleDown,
                        child: Text(
                          timeText,
                          maxLines: 1,
                          style: theme.textTheme.displayMedium?.copyWith(
                            fontSize: 46,
                            fontWeight: FontWeight.w700,
                            letterSpacing: -1.0,
                            color: palette.onSurface,
                            fontFeatures: const [FontFeature.tabularFigures()],
                          ),
                        ),
                      ),
                      const SizedBox(height: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: trackColor,
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Text(
                          isOffline ? '$subtitle · Ngoại tuyến' : subtitle,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.labelMedium?.copyWith(
                            color: isBreak
                                ? palette.onAmberSoft
                                : palette.onSageSoft,
                            letterSpacing: 0.2,
                          ),
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

class _FocusRingPainter extends CustomPainter {
  const _FocusRingPainter({
    required this.progress,
    required this.ringColor,
    required this.trackColor,
    required this.strokeWidth,
  });

  final double progress;
  final Color ringColor;
  final Color trackColor;
  final double strokeWidth;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (math.min(size.width, size.height) - strokeWidth) / 2;
    final rect = Rect.fromCircle(center: center, radius: radius);

    final trackPaint = Paint()
      ..color = trackColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth;
    canvas.drawCircle(center, radius, trackPaint);

    if (progress <= 0) return;

    final sweepPaint = Paint()
      ..color = ringColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(
      rect,
      -math.pi / 2,
      2 * math.pi * progress,
      false,
      sweepPaint,
    );
  }

  @override
  bool shouldRepaint(_FocusRingPainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.ringColor != ringColor ||
      oldDelegate.trackColor != trackColor ||
      oldDelegate.strokeWidth != strokeWidth;
}
