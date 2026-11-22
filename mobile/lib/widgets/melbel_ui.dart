import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/theme_provider.dart';
import '../services/melbel_sound_service.dart';

enum MelbelCardTone { neutral, muted, sage, amber, softError }

class MelbelAppBar extends StatelessWidget implements PreferredSizeWidget {
  const MelbelAppBar({
    required this.title,
    this.showBack = false,
    this.showGlobalActions = true,
    this.showAccountAction = true,
    this.leadingIcon,
    this.showThemeToggle = false,
    this.showTitleRays = false,
    this.trailingAction,
    this.onBack,
    super.key,
  });

  final String title;
  final bool showBack;
  final bool showGlobalActions;
  final bool showAccountAction;
  final IconData? leadingIcon;
  final bool showThemeToggle;
  final bool showTitleRays;
  final Widget? trailingAction;
  final VoidCallback? onBack;

  @override
  Size get preferredSize => const Size.fromHeight(64);

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final topPad = MediaQuery.paddingOf(context).top;

    Widget buildActionBadge({
      required Widget child,
      required Color bg,
    }) =>
        Container(
          width: 44,
          height: 44,
          decoration: BoxDecoration(
            color: bg,
            borderRadius: BorderRadius.circular(15),
          ),
          alignment: Alignment.center,
          child: child,
        );

    return Container(
      height: 64 + topPad,
      padding: EdgeInsets.only(top: topPad, left: 14, right: 14),
      color: Colors.transparent,
      child: Row(
        children: [
          if (showBack)
            IconButton(
              tooltip: 'Quay lại',
              constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
              padding: EdgeInsets.zero,
              onPressed: onBack ?? () => Navigator.maybePop(context),
              icon: buildActionBadge(
                bg: palette.mutedSurface,
                child: Icon(
                  Icons.arrow_back_rounded,
                  color: palette.onSurface,
                  size: 20,
                ),
              ),
            )
          else if (showThemeToggle)
            IconButton(
              tooltip: isDark ? 'Bật chế độ sáng' : 'Bật chế độ tối',
              constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
              padding: EdgeInsets.zero,
              onPressed: () {
                HapticFeedback.selectionClick();
                context.read<ThemeProvider>().setAppearance(
                      isDark ? 'light' : 'dark',
                    );
              },
              icon: buildActionBadge(
                bg: isDark ? palette.mutedSurface : palette.sageSoft,
                child: Icon(
                  isDark
                      ? Icons.nightlight_outlined
                      : Icons.wb_sunny_outlined,
                  color: isDark ? palette.amberInk : palette.accentInk,
                  size: 21,
                ),
              ),
            )
          else ...[
            const SizedBox(width: 4),
            buildActionBadge(
              bg: palette.sageSoft,
              child: Icon(
                leadingIcon ?? Icons.spa_outlined,
                color: palette.accentInk,
                size: 20,
              ),
            ),
            const SizedBox(width: 8),
          ],
          const SizedBox(width: 8),
          Expanded(
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Flexible(
                  child: Text(
                    title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.titleLarge?.copyWith(
                      fontSize: 22,
                      fontWeight: FontWeight.w800,
                      letterSpacing: -0.4,
                      color: palette.onSurface,
                    ),
                  ),
                ),
                if (showTitleRays) ...[
                  const SizedBox(width: 4),
                  Transform.translate(
                    offset: const Offset(0, -8),
                    child: Transform.rotate(
                      angle: 0.22,
                      child: MelbelDoodleRays(
                        size: 18,
                        color: isDark
                            ? const Color(0xFFB49EE8)
                            : const Color(0xFF9E85DA),
                      ),
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (showGlobalActions) ...[
            if (trailingAction != null) ...[
              trailingAction!,
              const SizedBox(width: 2),
            ],
            IconButton(
              tooltip: 'Trợ giúp & Hướng dẫn',
              constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
              padding: EdgeInsets.zero,
              onPressed: () => Navigator.of(context).pushNamed('/help'),
              icon: buildActionBadge(
                bg: palette.mutedSurface,
                child: Icon(
                  Icons.help_outline_rounded,
                  color: palette.onSurface.withValues(alpha: 0.78),
                  size: 22,
                ),
              ),
            ),
            if (showAccountAction)
              IconButton(
                tooltip: 'Tài khoản',
                constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
                padding: EdgeInsets.zero,
                onPressed: () => Navigator.of(context).pushNamed('/account'),
                icon: buildActionBadge(
                  bg: palette.sage,
                  child: CatAccountIcon(
                    size: 21,
                    color: palette.onSage,
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }
}

Future<void> showMelbelInfo(
  BuildContext context, {
  required String title,
  required String message,
  required IconData icon,
}) {
  return showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (sheetContext) {
      final palette = sheetContext.melbel;
      final textTheme = Theme.of(sheetContext).textTheme;
      return SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 8, 24, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: palette.sageSoft,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(icon, color: palette.sage, size: 24),
              ),
              const SizedBox(height: 14),
              Text(
                title,
                style: textTheme.titleLarge?.copyWith(
                  color: palette.onSurface,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                message,
                style: textTheme.bodyMedium?.copyWith(
                  color: palette.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 20),
              PrimaryCta(
                label: 'Đã hiểu',
                height: 48,
                onPressed: () => Navigator.pop(sheetContext),
              ),
            ],
          ),
        ),
      );
    },
  );
}

enum CatExpression { neutral, happy, sleepy }

/// Interactive Melbel VA companion mascot.
///
/// Supports tap and gentle stroking ("petting") with GPU-composited spring
/// bounce, soft haptic feedback, and calm floating sparkles inside a
/// [RepaintBoundary] so animations stay locked at 60 FPS with zero idle cost.
class MelbelMascot extends StatefulWidget {
  const MelbelMascot({
    this.expression = CatExpression.neutral,
    this.interactive = true,
    this.width = 120,
    this.height = 100,
    this.forceLightSvg = false,
    this.onPetted,
    super.key,
  });

  final CatExpression expression;
  final bool interactive;
  final double width;
  final double height;
  final bool forceLightSvg;
  final ValueChanged<String>? onPetted;

  static const List<String> companionWhispers = [
    'Mình ở đây cùng bạn nhé.',
    'Cứ làm từng bước thật nhỏ thôi.',
    'Hít sâu một nhịp rồi mình tiếp tục nha.',
    'Bạn đang cố gắng rất tốt rồi đấy.',
    'Nghỉ một chút cũng là một phần của tiến độ.',
    'Không cần hoàn hảo ngay đâu, cứ bắt đầu thôi.',
  ];

  @override
  State<MelbelMascot> createState() => MelbelMascotState();
}

class MelbelMascotState extends State<MelbelMascot>
    with TickerProviderStateMixin {
  late final AnimationController _bounce = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 520),
  );
  late final AnimationController _sparkles = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 850),
  );

  bool _petted = false;
  bool _cuddling = false;
  double _strokeLean = 0.0;
  int _petCount = 0;
  bool _strokeCoolingDown = false;
  Timer? _strokeCooldownTimer;
  Timer? _relaxTimer;

  @override
  void dispose() {
    _relaxTimer?.cancel();
    _strokeCooldownTimer?.cancel();
    _bounce.dispose();
    _sparkles.dispose();
    super.dispose();
  }

  /// Triggers Melbel's standard tap-pet reaction (haptic, meow, bounce,
  /// sparkles, happy expression, and `meo~` cue).
  void pet() => _triggerPet(isStroke: false);

  void _triggerPet({required bool isStroke}) {
    if (!widget.interactive) return;
    if (isStroke && _strokeCoolingDown) {
      return;
    }
    if (isStroke) {
      _strokeCoolingDown = true;
      _strokeCooldownTimer?.cancel();
      _strokeCooldownTimer = Timer(const Duration(milliseconds: 240), () {
        _strokeCoolingDown = false;
      });
    }
    _petCount++;

    if (isStroke) {
      HapticFeedback.selectionClick();
    } else {
      HapticFeedback.lightImpact();
    }
    unawaited(
      MelbelSoundService.instance.playMeow(
        volume: isStroke ? 0.45 : 0.6,
      ),
    );

    final whisper = MelbelMascot.companionWhispers[
        (_petCount - 1) % MelbelMascot.companionWhispers.length];
    widget.onPetted?.call(whisper);

    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    if (!reducedMotion) {
      _bounce.forward(from: 0);
      _sparkles.forward(from: 0);
    }

    if (!_petted) {
      setState(() => _petted = true);
    }

    _relaxTimer?.cancel();
    _relaxTimer = Timer(const Duration(milliseconds: 1700), () {
      if (mounted) {
        setState(() {
          _petted = false;
          _strokeLean = 0.0;
        });
      }
    });
  }

  void _handleStrokeUpdate(DragUpdateDetails details) {
    if (!widget.interactive) return;
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    if (!reducedMotion) {
      final nextLean =
          (_strokeLean + details.delta.dx * 0.004).clamp(-0.09, 0.09);
      if ((nextLean - _strokeLean).abs() > 0.002) {
        setState(() => _strokeLean = nextLean);
      }
    }
    _triggerPet(isStroke: true);
  }

  void _resetStrokeLean() {
    if (!mounted || _strokeLean == 0.0) return;
    setState(() => _strokeLean = 0.0);
  }

  void _startCuddle() {
    if (!widget.interactive) return;
    HapticFeedback.lightImpact();
    _relaxTimer?.cancel();
    if (!_cuddling) {
      setState(() => _cuddling = true);
    }
  }

  void _endCuddle() {
    if (!mounted || !_cuddling) return;
    setState(() => _cuddling = false);
    _triggerPet(isStroke: false);
  }

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final palette = context.melbel;
    final mode = widget.forceLightSvg ? 'light' : (dark ? 'dark' : 'light');
    final activeExpression = _cuddling
        ? CatExpression.sleepy
        : (_petted ? CatExpression.happy : widget.expression);
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final isReacting = _petted || _cuddling;

    final svg = AnimatedSwitcher(
      duration:
          reducedMotion ? Duration.zero : const Duration(milliseconds: 180),
      switchInCurve: Curves.easeOut,
      switchOutCurve: Curves.easeIn,
      child: SvgPicture.asset(
        'assets/melbel_svg/cat_${mode}_${activeExpression.name}.svg',
        key: ValueKey('${mode}_${activeExpression.name}'),
        width: widget.width,
        height: widget.height,
        fit: BoxFit.contain,
        excludeFromSemantics: true,
      ),
    );

    final animatedMascot = RepaintBoundary(
      child: SizedBox(
        width: widget.width,
        height: widget.height,
        child: Stack(
          clipBehavior: Clip.none,
          alignment: Alignment.center,
          children: [
            TweenAnimationBuilder<double>(
              tween: Tween<double>(
                end: reducedMotion ? 0.0 : _strokeLean,
              ),
              duration: reducedMotion
                  ? Duration.zero
                  : const Duration(milliseconds: 130),
              curve: Curves.easeOutCubic,
              builder: (context, leanAngle, child) => AnimatedBuilder(
                animation: _bounce,
                builder: (context, innerChild) {
                  final t = _bounce.value;
                  final wave = (t == 0 || t == 1)
                      ? 0.0
                      : math.sin(t * math.pi * 2) * (1 - t);
                  final cuddleX = _cuddling && !reducedMotion ? 1.07 : 1.0;
                  final cuddleY = _cuddling && !reducedMotion ? 0.93 : 1.0;
                  final scaleX = (1.0 + 0.09 * wave) * cuddleX;
                  final scaleY = (1.0 - 0.08 * wave) * cuddleY;
                  final bounceTilt = (t == 0 || t == 1)
                      ? 0.0
                      : 0.05 * math.sin(t * math.pi * 3) * (1 - t);
                  return Transform.rotate(
                    angle: bounceTilt + leanAngle,
                    alignment: Alignment.bottomCenter,
                    child: Transform.scale(
                      scaleX: scaleX,
                      scaleY: scaleY,
                      alignment: Alignment.bottomCenter,
                      child: innerChild,
                    ),
                  );
                },
                child: child,
              ),
              child: svg,
            ),
            if (widget.interactive)
              Positioned.fill(
                child: IgnorePointer(
                  child: AnimatedBuilder(
                    animation: _sparkles,
                    builder: (context, _) {
                      if (_sparkles.value <= 0 || _sparkles.value >= 1) {
                        return const SizedBox.shrink();
                      }
                      return CustomPaint(
                        painter: _PetSparklesPainter(
                          progress: _sparkles.value,
                          seed: _petCount,
                          primaryColor: palette.sage,
                          accentColor: palette.amber,
                        ),
                      );
                    },
                  ),
                ),
              ),
            if (widget.interactive && widget.onPetted == null)
              Positioned(
                top: -10,
                right: -4,
                child: IgnorePointer(
                  child: AnimatedOpacity(
                    opacity: isReacting ? 1.0 : 0.0,
                    duration: reducedMotion
                        ? Duration.zero
                        : const Duration(milliseconds: 180),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 3,
                      ),
                      decoration: BoxDecoration(
                        color: palette.amberSoft,
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: Text(
                        _cuddling ? 'grrr~' : 'meo~',
                        style: Theme.of(context).textTheme.labelSmall?.copyWith(
                              color: palette.onAmberSoft,
                              fontWeight: FontWeight.w700,
                            ),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );

    if (!widget.interactive) return animatedMascot;

    return Semantics(
      button: true,
      label: 'Trợ lý Melbel. Chạm, vuốt nhẹ hoặc nhấn giữ để cưng nựng',
      child: MouseRegion(
        cursor: _cuddling
            ? SystemMouseCursors.grabbing
            : SystemMouseCursors.grab,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () => _triggerPet(isStroke: false),
          onHorizontalDragUpdate: _handleStrokeUpdate,
          onHorizontalDragEnd: (_) => _resetStrokeLean(),
          onHorizontalDragCancel: _resetStrokeLean,
          onLongPressStart: (_) => _startCuddle(),
          onLongPressEnd: (_) => _endCuddle(),
          onLongPressCancel: _endCuddle,
          child: animatedMascot,
        ),
      ),
    );
  }
}

class _PetSparklesPainter extends CustomPainter {
  const _PetSparklesPainter({
    required this.progress,
    required this.seed,
    required this.primaryColor,
    required this.accentColor,
  });

  final double progress;
  final int seed;
  final Color primaryColor;
  final Color accentColor;

  @override
  void paint(Canvas canvas, Size size) {
    final ease = Curves.easeOutCubic.transform(progress);
    final alpha = (1 - progress).clamp(0.0, 1.0);
    final particles = <(double dx, double startY, double lift, int kind)>[
      (0.24, 0.28, 22, 0),
      (0.76, 0.24, 26, 1),
      (0.52, 0.14, 28, 0),
      (0.18, 0.56, 16, 2),
      (0.84, 0.54, 18, 2),
    ];

    for (var i = 0; i < particles.length; i++) {
      final p = particles[i];
      final drift = math.sin((ease * math.pi) + i + seed) * 5;
      final center = Offset(
        size.width * p.$1 + drift,
        size.height * p.$2 - p.$3 * ease,
      );
      final scale = 0.65 + 0.45 * math.sin(progress * math.pi);
      final color = (i.isEven ? primaryColor : accentColor)
          .withValues(alpha: alpha * 0.9);

      if (p.$4 == 0) {
        final paint = Paint()
          ..color = color
          ..style = PaintingStyle.fill;
        _drawMiniHeart(canvas, center, 5.5 * scale, paint);
      } else if (p.$4 == 1) {
        final paint = Paint()
          ..color = color
          ..style = PaintingStyle.fill;
        _drawMiniSparkle(canvas, center, 6.0 * scale, paint);
      } else {
        final stroke = Paint()
          ..color = color.withValues(alpha: alpha * 0.72)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.6
          ..strokeCap = StrokeCap.round;
        _drawPurrWave(canvas, center, 6.5 * scale, stroke);
      }
    }
  }

  void _drawMiniHeart(Canvas canvas, Offset c, double r, Paint paint) {
    final path = Path()
      ..moveTo(c.dx, c.dy + r * 0.8)
      ..cubicTo(
        c.dx - r * 1.4,
        c.dy - r * 0.1,
        c.dx - r * 0.75,
        c.dy - r * 1.25,
        c.dx,
        c.dy - r * 0.45,
      )
      ..cubicTo(
        c.dx + r * 0.75,
        c.dy - r * 1.25,
        c.dx + r * 1.4,
        c.dy - r * 0.1,
        c.dx,
        c.dy + r * 0.8,
      )
      ..close();
    canvas.drawPath(path, paint);
  }

  void _drawMiniSparkle(Canvas canvas, Offset c, double r, Paint paint) {
    final path = Path()
      ..moveTo(c.dx, c.dy - r)
      ..quadraticBezierTo(c.dx, c.dy, c.dx + r, c.dy)
      ..quadraticBezierTo(c.dx, c.dy, c.dx, c.dy + r)
      ..quadraticBezierTo(c.dx, c.dy, c.dx - r, c.dy)
      ..quadraticBezierTo(c.dx, c.dy, c.dx, c.dy - r)
      ..close();
    canvas.drawPath(path, paint);
  }

  void _drawPurrWave(Canvas canvas, Offset c, double r, Paint paint) {
    final path = Path()
      ..moveTo(c.dx - r, c.dy)
      ..quadraticBezierTo(c.dx - r * 0.5, c.dy - r * 0.55, c.dx, c.dy)
      ..quadraticBezierTo(c.dx + r * 0.5, c.dy + r * 0.55, c.dx + r, c.dy);
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(_PetSparklesPainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.seed != seed ||
      oldDelegate.primaryColor != primaryColor ||
      oldDelegate.accentColor != accentColor;
}

class CatAccountIcon extends StatelessWidget {
  const CatAccountIcon({required this.color, this.size = 24, super.key});

  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) => CustomPaint(
        size: Size.square(size),
        painter: _CatAccountPainter(color),
      );
}

class _CatAccountPainter extends CustomPainter {
  const _CatAccountPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / 24, size.height / 24);
    final fill = Paint()..color = color;
    canvas.drawPath(
      Path()
        ..moveTo(7, 8)
        ..lineTo(7, 3)
        ..quadraticBezierTo(7, 2, 8, 2.5)
        ..lineTo(11, 5)
        ..lineTo(13, 5)
        ..lineTo(16, 2.5)
        ..quadraticBezierTo(17, 2, 17, 3)
        ..lineTo(17, 8)
        ..close(),
      fill,
    );
    canvas.drawCircle(const Offset(12, 9), 5, fill);
    canvas.drawPath(
      Path()
        ..moveTo(4, 22)
        ..quadraticBezierTo(4, 16, 12, 16)
        ..quadraticBezierTo(20, 16, 20, 22)
        ..close(),
      fill,
    );
    canvas.restore();
  }

  @override
  bool shouldRepaint(_CatAccountPainter oldDelegate) =>
      oldDelegate.color != color;
}

class MelbelEntrance extends StatelessWidget {
  const MelbelEntrance({
    required this.child,
    this.offsetY = 6,
    super.key,
  });

  final Widget child;
  final double offsetY;

  @override
  Widget build(BuildContext context) {
    if (MediaQuery.disableAnimationsOf(context)) return child;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: 1),
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      child: child,
      builder: (context, progress, child) => Transform.translate(
        offset: Offset(0, offsetY * (1 - progress)),
        child: Opacity(opacity: progress, child: child),
      ),
    );
  }
}

class MelbelStepProgressBar extends StatefulWidget {
  const MelbelStepProgressBar({
    required this.totalSteps,
    required this.currentStep,
    this.height = 6,
    this.spacing = 6,
    super.key,
  });

  final int totalSteps;
  final int currentStep;
  final double height;
  final double spacing;

  @override
  State<MelbelStepProgressBar> createState() => _MelbelStepProgressBarState();
}

class _MelbelStepProgressBarState extends State<MelbelStepProgressBar>
    with SingleTickerProviderStateMixin {
  late int _fromStep;
  late int _toStep;
  bool _hasStarted = false;
  bool _isMountAnimation = true;
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1150),
  );

  @override
  void initState() {
    super.initState();
    _toStep = widget.currentStep.clamp(0, widget.totalSteps);
    _fromStep = (_toStep - 1).clamp(0, widget.totalSteps);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    if (!_hasStarted) {
      _hasStarted = true;
      if (reducedMotion || _toStep == 0) {
        _controller.value = 1.0;
      } else {
        _controller.forward(from: 0.0);
      }
    } else if (reducedMotion && _controller.isAnimating) {
      _controller.value = 1.0;
    }
  }

  @override
  void didUpdateWidget(covariant MelbelStepProgressBar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.currentStep != oldWidget.currentStep ||
        widget.totalSteps != oldWidget.totalSteps) {
      final nextTo = widget.currentStep.clamp(0, widget.totalSteps);
      if (widget.totalSteps != oldWidget.totalSteps) {
        _fromStep = (nextTo - 1).clamp(0, widget.totalSteps);
        _isMountAnimation = true;
      } else {
        _fromStep = oldWidget.currentStep.clamp(0, widget.totalSteps);
        _isMountAnimation = false;
      }
      _toStep = nextTo;
      if (MediaQuery.disableAnimationsOf(context)) {
        _controller.value = 1.0;
      } else {
        _controller.forward(from: 0.0);
      }
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final trackColor =
        isDark ? const Color(0xFF3E3454) : const Color(0xFFE6DCF7);
    final accentHighlight = Color.lerp(
      palette.sage,
      Colors.white,
      isDark ? 0.24 : 0.16,
    )!;

    return RepaintBoundary(
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          final t = reducedMotion ? 1.0 : _controller.value;
          final fillInterval = _isMountAnimation
              ? const Interval(0.08, 0.54, curve: Curves.easeOutCubic)
              : const Interval(0.0, 0.48, curve: Curves.easeOutCubic);
          final sheenInterval = _isMountAnimation
              ? const Interval(0.50, 1.0, curve: Curves.easeInOutCubic)
              : const Interval(0.44, 1.0, curve: Curves.easeInOutCubic);
          final fillT = fillInterval.transform(t);
          final sheenT = sheenInterval.transform(t);
          final advancing = _toStep >= _fromStep;

          return Row(
            spacing: widget.spacing,
            children: [
              for (var index = 1; index <= widget.totalSteps; index++)
                () {
                  final double fill;
                  if (advancing) {
                    if (index <= _fromStep) {
                      fill = 1.0;
                    } else if (index <= _toStep) {
                      fill = fillT;
                    } else {
                      fill = 0.0;
                    }
                  } else {
                    if (index <= _toStep) {
                      fill = 1.0;
                    } else if (index <= _fromStep) {
                      fill = 1.0 - fillT;
                    } else {
                      fill = 0.0;
                    }
                  }

                  final showSheen = !reducedMotion &&
                      advancing &&
                      index == _toStep &&
                      sheenT > 0.0 &&
                      sheenT < 1.0;
                  final sheenOpacity = showSheen
                      ? math.sin(sheenT * math.pi).clamp(0.0, 1.0)
                      : 0.0;

                  return Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(999),
                      child: SizedBox(
                        height: widget.height,
                        child: DecoratedBox(
                          decoration: BoxDecoration(color: trackColor),
                          child: Stack(
                            fit: StackFit.expand,
                            children: [
                              if (fill > 0.001)
                                Transform.scale(
                                  alignment: Alignment.centerLeft,
                                  scaleX: fill.clamp(0.0, 1.0),
                                  child: DecoratedBox(
                                    decoration: BoxDecoration(
                                      borderRadius: BorderRadius.circular(999),
                                      gradient: LinearGradient(
                                        colors: [palette.sage, accentHighlight],
                                      ),
                                    ),
                                  ),
                                ),
                              if (showSheen && sheenOpacity > 0.01)
                                Opacity(
                                  opacity: sheenOpacity,
                                  child: FractionalTranslation(
                                    translation:
                                        Offset(-1.05 + 2.1 * sheenT, 0),
                                    child: const DecoratedBox(
                                      decoration: BoxDecoration(
                                        gradient: LinearGradient(
                                          colors: [
                                            Color(0x00FFFFFF),
                                            Color(0x99FFFFFF),
                                            Color(0x33FFFFFF),
                                            Color(0x00FFFFFF),
                                          ],
                                          stops: [0.0, 0.45, 0.65, 1.0],
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  );
                }(),
            ],
          );
        },
      ),
    );
  }
}

class MelbelAnimatedProgressBar extends StatefulWidget {
  const MelbelAnimatedProgressBar({
    required this.value,
    this.height = 8,
    this.semanticsLabel,
    this.animateFromZeroOnMount = true,
    super.key,
  });

  final double value;
  final double height;
  final String? semanticsLabel;
  final bool animateFromZeroOnMount;

  @override
  State<MelbelAnimatedProgressBar> createState() =>
      _MelbelAnimatedProgressBarState();
}

class _MelbelAnimatedProgressBarState extends State<MelbelAnimatedProgressBar>
    with SingleTickerProviderStateMixin {
  late double _fromValue;
  late double _toValue;
  bool _hasStarted = false;
  late bool _playSheen;
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 680),
  );

  double _fillProgress(double t) {
    final curve = _playSheen
        ? const Interval(0.0, 0.65, curve: Curves.easeOutCubic)
        : Curves.easeOutCubic;
    final curved = curve.transform(t.clamp(0.0, 1.0));
    return (_fromValue + (_toValue - _fromValue) * curved).clamp(0.0, 1.0);
  }

  @override
  void initState() {
    super.initState();
    final clamped = widget.value.clamp(0.0, 1.0).toDouble();
    _toValue = clamped;
    _fromValue = widget.animateFromZeroOnMount ? 0.0 : clamped;
    _playSheen = widget.animateFromZeroOnMount && clamped > 0.05;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    if (!_hasStarted) {
      _hasStarted = true;
      if (reducedMotion || (_fromValue - _toValue).abs() < 0.001) {
        _controller.value = 1.0;
      } else {
        _controller.forward(from: 0.0);
      }
    } else if (reducedMotion && _controller.isAnimating) {
      _controller.value = 1.0;
    }
  }

  @override
  void didUpdateWidget(covariant MelbelAnimatedProgressBar oldWidget) {
    super.didUpdateWidget(oldWidget);
    final next = widget.value.clamp(0.0, 1.0).toDouble();
    if ((next - _toValue).abs() >= 0.0005) {
      _fromValue = _fillProgress(_controller.value);
      _toValue = next;
      _playSheen = false;
      _controller.duration = const Duration(milliseconds: 320);
      if (MediaQuery.disableAnimationsOf(context)) {
        _controller.value = 1.0;
      } else {
        _controller.forward(from: 0.0);
      }
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final trackColor =
        isDark ? const Color(0xFF3E3454) : const Color(0xFFE6DCF7);
    final accentHighlight = Color.lerp(
      palette.sage,
      Colors.white,
      isDark ? 0.24 : 0.16,
    )!;

    return Semantics(
      label: widget.semanticsLabel,
      value: '${(_toValue * 100).round()}%',
      child: RepaintBoundary(
        child: AnimatedBuilder(
          animation: _controller,
          builder: (context, _) {
            final t = reducedMotion ? 1.0 : _controller.value;
            final currentFill = reducedMotion ? _toValue : _fillProgress(t);
            final sheenT = _playSheen
                ? const Interval(0.55, 1.0, curve: Curves.easeInOutCubic)
                    .transform(t)
                : 0.0;
            final showSheen = !reducedMotion &&
                _playSheen &&
                currentFill > 0.05 &&
                sheenT > 0.0 &&
                sheenT < 1.0;
            final sheenOpacity =
                showSheen ? math.sin(sheenT * math.pi).clamp(0.0, 1.0) : 0.0;

            return ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: SizedBox(
                width: double.infinity,
                height: widget.height,
                child: DecoratedBox(
                  decoration: BoxDecoration(color: trackColor),
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      Transform.scale(
                        alignment: Alignment.centerLeft,
                        scaleX: currentFill.clamp(0.0, 1.0),
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(999),
                            gradient: LinearGradient(
                              colors: [palette.sage, accentHighlight],
                            ),
                          ),
                        ),
                      ),
                      if (showSheen && sheenOpacity > 0.01)
                        Opacity(
                          opacity: sheenOpacity,
                          child: FractionalTranslation(
                            translation: Offset(-1.05 + 2.1 * sheenT, 0),
                            child: const DecoratedBox(
                              decoration: BoxDecoration(
                                gradient: LinearGradient(
                                  colors: [
                                    Color(0x00FFFFFF),
                                    Color(0x99FFFFFF),
                                    Color(0x33FFFFFF),
                                    Color(0x00FFFFFF),
                                  ],
                                  stops: [0.0, 0.45, 0.65, 1.0],
                                ),
                              ),
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

class PlanningStageHeader extends StatelessWidget {
  const PlanningStageHeader({
    required this.step,
    required this.label,
    super.key,
  });

  final int step;
  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    return Semantics(
      label: 'Bước $step trên 3: $label',
      child: MelbelEntrance(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                MelbelStatusPill(
                  label: 'Bước $step/3',
                  tone: MelbelCardTone.sage,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.w600,
                      color: palette.onSurfaceVariant,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            MelbelStepProgressBar(
              totalSteps: 3,
              currentStep: step,
            ),
          ],
        ),
      ),
    );
  }
}

class _MelbelPressScale extends StatefulWidget {
  const _MelbelPressScale({
    required this.enabled,
    required this.child,
  });

  final bool enabled;
  final Widget child;

  @override
  State<_MelbelPressScale> createState() => _MelbelPressScaleState();
}

class _MelbelPressScaleState extends State<_MelbelPressScale> {
  bool _pressed = false;

  void _setPressed(bool next) {
    if (_pressed == next || !mounted) return;
    setState(() => _pressed = next);
  }

  @override
  Widget build(BuildContext context) {
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final active = widget.enabled && !reducedMotion;
    return Listener(
      behavior: HitTestBehavior.translucent,
      onPointerDown: active ? (_) => _setPressed(true) : null,
      onPointerUp: active ? (_) => _setPressed(false) : null,
      onPointerCancel: active ? (_) => _setPressed(false) : null,
      child: AnimatedScale(
        scale: active && _pressed ? 0.985 : 1.0,
        duration: reducedMotion
            ? Duration.zero
            : const Duration(milliseconds: 90),
        curve: Curves.easeOutCubic,
        child: widget.child,
      ),
    );
  }
}

class MelbelCard extends StatelessWidget {
  const MelbelCard({
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.color,
    this.borderColor,
    this.borderWidth = 0.0,
    this.borderRadius = 26.0,
    this.tone = MelbelCardTone.neutral,
    this.elevated = true,
    this.onTap,
    super.key,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? color;
  final Color? borderColor;
  final double borderWidth;
  final double borderRadius;
  final MelbelCardTone tone;
  final bool elevated;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final highContrast = MediaQuery.highContrastOf(context);
    final (bg, stroke) = switch (tone) {
      MelbelCardTone.neutral => (palette.surface, palette.hairline),
      MelbelCardTone.muted => (palette.mutedSurface, palette.hairline),
      MelbelCardTone.sage => (
          palette.sageSoft,
          palette.sage.withValues(alpha: 0.3),
        ),
      MelbelCardTone.amber => (
          palette.amberSoft,
          palette.amber.withValues(alpha: 0.32),
        ),
      MelbelCardTone.softError => (
          palette.softErrorSoft,
          palette.softError.withValues(alpha: 0.32),
        ),
    };

    final resolvedBg = color ?? bg;
    final effectiveBorderWidth = borderWidth > 0
        ? borderWidth
        : highContrast
            ? 1.5
            : (isDark &&
                    (tone == MelbelCardTone.neutral ||
                        tone == MelbelCardTone.muted))
                ? 1.0
                : 0.0;
    final effectiveBorderColor = borderWidth > 0
        ? (borderColor ?? stroke)
        : highContrast
            ? palette.controlOutline
            : palette.hairline.withValues(alpha: 0.65);
    final side = effectiveBorderWidth <= 0
        ? BorderSide.none
        : BorderSide(color: effectiveBorderColor, width: effectiveBorderWidth);
    final shadows = !elevated
        ? const <BoxShadow>[]
        : isDark
            ? [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.22),
                  blurRadius: 18,
                  offset: const Offset(0, 6),
                ),
              ]
            : tone == MelbelCardTone.muted
                ? const <BoxShadow>[]
                : [
                    BoxShadow(
                      color: const Color(0xFF534070).withValues(alpha: 0.06),
                      blurRadius: 22,
                      offset: const Offset(0, 7),
                    ),
                  ];
    final content = Padding(padding: padding, child: child);

    final card = SizedBox(
      width: double.infinity,
      child: DecoratedBox(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(borderRadius),
          boxShadow: shadows,
        ),
        child: Material(
          color: resolvedBg,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(borderRadius),
            side: side,
          ),
          clipBehavior: Clip.antiAlias,
          child: onTap == null
              ? content
              : Semantics(
                  button: true,
                  child: InkWell(
                    onTap: () {
                      HapticFeedback.selectionClick();
                      onTap!();
                    },
                    hoverColor: palette.accentInk.withValues(
                      alpha: isDark ? 0.08 : 0.05,
                    ),
                    focusColor: palette.accentInk.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(borderRadius),
                    child: content,
                  ),
                ),
        ),
      ),
    );
    return onTap == null ? card : _MelbelPressScale(enabled: true, child: card);
  }
}

/// Secondary details stay reachable without competing with the next action.
class MelbelDisclosure extends StatelessWidget {
  const MelbelDisclosure({
    required this.title,
    required this.children,
    this.summary,
    this.icon,
    super.key,
  });

  final String title;
  final String? summary;
  final IconData? icon;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final shape =
        RoundedRectangleBorder(borderRadius: BorderRadius.circular(24));
    return MelbelCard(
      borderRadius: 24,
      padding: EdgeInsets.zero,
      child: Theme(
        data: theme.copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          key: PageStorageKey<String>(title),
          shape: shape,
          collapsedShape: shape,
          tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          expansionAnimationStyle: MediaQuery.disableAnimationsOf(context)
              ? AnimationStyle.noAnimation
              : null,
          leading: icon == null
              ? null
              : Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: palette.sageSoft,
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Icon(
                    icon,
                    size: 20,
                    color: isDark ? palette.onSageSoft : palette.sage,
                  ),
                ),
          title: Text(
            title,
            style: theme.textTheme.titleMedium?.copyWith(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: palette.onSurface,
            ),
          ),
          subtitle: summary == null
              ? null
              : Text(
                  summary!,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: palette.onSurfaceVariant,
                  ),
                ),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
          children: children,
        ),
      ),
    );
  }
}

class MelbelStatusPill extends StatelessWidget {
  const MelbelStatusPill({
    required this.label,
    this.icon,
    this.tone = MelbelCardTone.muted,
    super.key,
  });

  final String label;
  final IconData? icon;
  final MelbelCardTone tone;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final (bg, fg) = switch (tone) {
      MelbelCardTone.neutral => (palette.surface, palette.onSurface),
      MelbelCardTone.muted => (palette.mutedSurface, palette.onSurfaceVariant),
      MelbelCardTone.sage => (palette.sageSoft, palette.onSageSoft),
      MelbelCardTone.amber => (palette.amberSoft, palette.onAmberSoft),
      MelbelCardTone.softError => (
          palette.softErrorSoft,
          palette.onSoftErrorSoft,
        ),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 14, color: fg),
            const SizedBox(width: 5),
          ],
          Flexible(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                    color: fg,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.1,
                  ),
            ),
          ),
        ],
      ),
    );
  }
}

class PrimaryCta extends StatelessWidget {
  const PrimaryCta({
    required this.label,
    required this.onPressed,
    this.icon,
    this.height = 58.0,
    super.key,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final double height;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final enabled = onPressed != null;
    final pillFill = palette.sage;

    return _MelbelPressScale(
      enabled: enabled,
      child: SizedBox(
        width: double.infinity,
        child: DecoratedBox(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(999),
            boxShadow: enabled && !isDark
                ? [
                    BoxShadow(
                      color: palette.sage.withValues(alpha: 0.25),
                      blurRadius: 14,
                      offset: const Offset(0, 5),
                    ),
                  ]
                : const [],
          ),
          child: ConstrainedBox(
            constraints: BoxConstraints(minHeight: height),
            child: ElevatedButton(
              style: ElevatedButton.styleFrom(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
                backgroundColor: pillFill,
                foregroundColor: palette.onSage,
                disabledBackgroundColor: palette.selectedSurface,
                disabledForegroundColor: palette.onSurfaceVariant,
                shape: const StadiumBorder(),
                elevation: 0,
                shadowColor: Colors.transparent,
              ),
              onPressed: onPressed == null
                  ? null
                  : () {
                      HapticFeedback.selectionClick();
                      onPressed!();
                    },
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  if (icon != null) ...[
                    Icon(icon, size: 19),
                    const SizedBox(width: 6),
                  ],
                  Flexible(
                    child: Text(
                      label,
                      textAlign: TextAlign.center,
                      style: textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                        color:
                            enabled ? palette.onSage : palette.onSurfaceVariant,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class SecondaryCta extends StatelessWidget {
  const SecondaryCta({
    required this.label,
    required this.onPressed,
    this.icon,
    this.height = 54.0,
    this.backgroundColor,
    super.key,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final double height;
  final Color? backgroundColor;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final highContrast = MediaQuery.highContrastOf(context);
    final enabled = onPressed != null;
    final bg = backgroundColor ??
        (isDark ? const Color(0xFF231D2E) : palette.surface);

    return _MelbelPressScale(
      enabled: enabled,
      child: SizedBox(
        width: double.infinity,
        child: DecoratedBox(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(999),
            boxShadow: enabled && !isDark
                ? [
                    BoxShadow(
                      color: const Color(0xFF534070).withValues(alpha: 0.07),
                      blurRadius: 14,
                      offset: const Offset(0, 4),
                    ),
                  ]
                : const [],
          ),
          child: ConstrainedBox(
            constraints: BoxConstraints(minHeight: height),
            child: OutlinedButton(
              style: OutlinedButton.styleFrom(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
                backgroundColor: bg,
                foregroundColor: palette.onSurface,
                disabledForegroundColor:
                    palette.onSurfaceVariant.withValues(alpha: 0.6),
                side: highContrast
                    ? BorderSide(color: palette.controlOutline, width: 1.5)
                    : isDark
                        ? BorderSide(color: palette.hairline, width: 1.0)
                        : BorderSide.none,
                shape: const StadiumBorder(),
              ),
              onPressed: onPressed == null
                  ? null
                  : () {
                      HapticFeedback.selectionClick();
                      onPressed!();
                    },
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  if (icon != null) ...[
                    Icon(
                      icon,
                      size: 18,
                      color: enabled
                          ? palette.onSurface
                          : palette.onSurfaceVariant.withValues(alpha: 0.6),
                    ),
                    const SizedBox(width: 6),
                  ],
                  Flexible(
                    child: Text(
                      label,
                      textAlign: TextAlign.center,
                      style: textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                        color: enabled
                            ? palette.onSurface
                            : palette.onSurfaceVariant.withValues(alpha: 0.6),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class Eyebrow extends StatelessWidget {
  const Eyebrow(this.text, {this.color, super.key});

  final String text;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    return Text(
      text.toUpperCase(),
      style: Theme.of(context).textTheme.labelMedium?.copyWith(
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.85,
            color: color ?? palette.onSurfaceVariant,
          ),
    );
  }
}

enum MelbelBubbleTail { left, right, top }

/// Creative conversational VA speech bubble replacing flat text blocks.
///
/// Renders a soft curved tail pointing toward Melbel, a gentle sender header
/// with a unified lavender/honey accent dot, and smooth message transitions
/// while staying GPU-light at 60 FPS.
class MelbelChatBubble extends StatelessWidget {
  const MelbelChatBubble({
    this.senderLabel = 'Melbel',
    this.badge,
    this.title,
    this.titleIsHeader = false,
    this.message,
    this.footnote,
    this.child,
    this.tone = MelbelCardTone.sage,
    this.tail = MelbelBubbleTail.left,
    this.centered = false,
    super.key,
  });

  final String? senderLabel;
  final String? badge;
  final String? title;
  final bool titleIsHeader;
  final String? message;
  final String? footnote;
  final Widget? child;
  final MelbelCardTone tone;
  final MelbelBubbleTail tail;
  final bool centered;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final textTheme = theme.textTheme;
    final reducedMotion = MediaQuery.disableAnimationsOf(context);

    final (bg, border, fg, dotColor, badgeBg, badgeFg) = switch (tone) {
      MelbelCardTone.amber => (
          palette.amberSoft,
          palette.amber.withValues(alpha: 0.42),
          palette.onAmberSoft,
          palette.amber,
          isDark
              ? const Color(0xFF4D4238)
              : palette.surface.withValues(alpha: 0.78),
          palette.onAmberSoft,
        ),
      MelbelCardTone.sage => (
          palette.sageSoft,
          palette.sage.withValues(alpha: 0.36),
          palette.onSageSoft,
          palette.amber,
          isDark
              ? const Color(0xFF4B3D65)
              : palette.surface.withValues(alpha: 0.78),
          palette.onSageSoft,
        ),
      MelbelCardTone.muted => (
          palette.mutedSurface,
          palette.hairline,
          palette.onSurface,
          palette.sage,
          palette.surface,
          palette.onSurfaceVariant,
        ),
      MelbelCardTone.softError => (
          palette.softErrorSoft,
          palette.softError.withValues(alpha: 0.38),
          palette.onSoftErrorSoft,
          palette.softError,
          palette.surface.withValues(alpha: 0.72),
          palette.onSoftErrorSoft,
        ),
      MelbelCardTone.neutral => (
          palette.surface,
          palette.hairline,
          palette.onSurface,
          palette.sage,
          palette.mutedSurface,
          palette.onSurfaceVariant,
        ),
    };

    final bubble = AnimatedContainer(
      duration: reducedMotion
          ? Duration.zero
          : const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      padding: EdgeInsets.fromLTRB(
        18 + (tail == MelbelBubbleTail.left ? 9 : 0),
        15 + (tail == MelbelBubbleTail.top ? 8 : 0),
        18 + (tail == MelbelBubbleTail.right ? 9 : 0),
        15,
      ),
      decoration: ShapeDecoration(
        color: bg,
        shadows: isDark
            ? [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.20),
                  blurRadius: 16,
                  offset: const Offset(0, 5),
                ),
              ]
            : [
                BoxShadow(
                  color: const Color(0xFF534070).withValues(alpha: 0.06),
                  blurRadius: 18,
                  offset: const Offset(0, 6),
                ),
              ],
        shape: _MelbelBubbleShape(
          tail: tail,
          borderColor: isDark
              ? (tone == MelbelCardTone.softError
                  ? border
                  : palette.hairline.withValues(alpha: 0.55))
              : Colors.transparent,
          borderWidth: isDark ? 1.0 : 0,
        ),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment:
            centered ? CrossAxisAlignment.center : CrossAxisAlignment.start,
        children: [
          if (senderLabel != null || badge != null) ...[
            Wrap(
              spacing: 8,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              alignment: centered ? WrapAlignment.center : WrapAlignment.start,
              children: [
                if (senderLabel != null)
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Container(
                        width: 7,
                        height: 7,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: dotColor,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        senderLabel!,
                        style: textTheme.labelSmall?.copyWith(
                          color: fg.withValues(alpha: 0.85),
                          fontWeight: FontWeight.w700,
                          letterSpacing: 0.2,
                        ),
                      ),
                    ],
                  ),
                if (badge != null)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 2,
                    ),
                    decoration: BoxDecoration(
                      color: badgeBg,
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      badge!,
                      style: textTheme.labelSmall?.copyWith(
                        fontSize: 12,
                        color: badgeFg.withValues(alpha: 0.9),
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 6),
          ],
          if (title != null) ...[
            Semantics(
              header: titleIsHeader,
              child: Text(
                title!,
                textAlign: centered ? TextAlign.center : TextAlign.start,
                style: textTheme.titleLarge?.copyWith(
                  fontSize: 19,
                  color: fg,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            if (message != null || child != null) const SizedBox(height: 4),
          ],
          if (message != null)
            AnimatedSwitcher(
              duration: reducedMotion
                  ? Duration.zero
                  : const Duration(milliseconds: 200),
              child: Align(
                key: ValueKey(message),
                alignment: centered ? Alignment.center : Alignment.centerLeft,
                child: Text(
                  message!,
                  textAlign: centered ? TextAlign.center : TextAlign.start,
                  style: textTheme.bodyLarge?.copyWith(
                    fontSize: title == null && child == null ? 17.5 : null,
                    fontWeight: title == null && child == null
                        ? FontWeight.w600
                        : null,
                    color: fg.withValues(alpha: title != null ? 0.92 : 1.0),
                    height: 1.42,
                  ),
                ),
              ),
            ),
          if (child != null) child!,
          if (footnote != null) ...[
            const SizedBox(height: 8),
            Row(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Icon(
                    Icons.auto_awesome_rounded,
                    size: 13,
                    color: dotColor,
                  ),
                ),
                const SizedBox(width: 5),
                Flexible(
                  child: Text(
                    footnote!,
                    style: textTheme.labelSmall?.copyWith(
                      color: fg.withValues(alpha: 0.72),
                      fontWeight: FontWeight.w500,
                      height: 1.35,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );

    if (tail == MelbelBubbleTail.top) {
      return bubble;
    }
    return SizedBox(width: double.infinity, child: bubble);
  }
}

class _MelbelBubbleShape extends ShapeBorder {
  const _MelbelBubbleShape({
    required this.tail,
    required this.borderColor,
    this.borderWidth = 1.1,
  });

  final MelbelBubbleTail tail;
  final Color borderColor;
  final double borderWidth;

  @override
  EdgeInsetsGeometry get dimensions => EdgeInsets.zero;

  @override
  ShapeBorder scale(double t) => _MelbelBubbleShape(
        tail: tail,
        borderColor: borderColor,
        borderWidth: borderWidth * t,
      );

  @override
  Path getInnerPath(Rect rect, {TextDirection? textDirection}) =>
      getOuterPath(rect.deflate(borderWidth), textDirection: textDirection);

  @override
  Path getOuterPath(Rect rect, {TextDirection? textDirection}) {
    final tailDepth = tail == MelbelBubbleTail.top ? 8.0 : 9.0;
    final tailSpan = tail == MelbelBubbleTail.top ? 16.0 : 14.0;

    final l = rect.left + (tail == MelbelBubbleTail.left ? tailDepth : 0.0);
    final t = rect.top + (tail == MelbelBubbleTail.top ? tailDepth : 0.0);
    final rEdge =
        rect.right - (tail == MelbelBubbleTail.right ? tailDepth : 0.0);
    final b = rect.bottom;

    final w = math.max(0.0, rEdge - l);
    final h = math.max(0.0, b - t);
    final maxR = math.min(w, h) / 2.0;

    final tlR =
        tail == MelbelBubbleTail.left ? math.min(10.0, maxR) : math.min(22.0, maxR);
    final trR =
        tail == MelbelBubbleTail.right ? math.min(10.0, maxR) : math.min(22.0, maxR);
    final blR = math.min(22.0, maxR);
    final brR = math.min(22.0, maxR);

    final path = Path()..moveTo(l + tlR, t);

    // Top edge (+ optional top tail centered horizontally)
    if (tail == MelbelBubbleTail.top && w >= tailSpan + tlR + trR + 8.0) {
      final cx = (l + rEdge) / 2.0;
      path
        ..lineTo(cx - tailSpan / 2.0, t)
        ..quadraticBezierTo(
            cx - tailSpan * 0.14, t - tailDepth * 0.65, cx, rect.top)
        ..quadraticBezierTo(
            cx + tailSpan * 0.14, t - tailDepth * 0.65, cx + tailSpan / 2.0, t);
    }
    path
      ..lineTo(rEdge - trR, t)
      ..arcToPoint(
        Offset(rEdge, t + trR),
        radius: Radius.circular(trR),
        clockwise: true,
      );

    // Right edge (+ optional right tail aligned with mascot vertically)
    if (tail == MelbelBubbleTail.right && h >= tailSpan + trR + brR + 4.0) {
      final minTop = t + trR + 2.0;
      final maxTop = math.max(minTop, b - brR - tailSpan - 2.0);
      final tailTop = (t + 20.0).clamp(minTop, maxTop);
      final tailBot = tailTop + tailSpan;
      final tailMid = (tailTop + tailBot) / 2.0;
      path
        ..lineTo(rEdge, tailTop)
        ..quadraticBezierTo(
            rEdge + tailDepth * 0.62, tailTop + tailSpan * 0.15, rect.right, tailMid)
        ..quadraticBezierTo(
            rEdge + tailDepth * 0.62, tailBot - tailSpan * 0.15, rEdge, tailBot);
    }
    path
      ..lineTo(rEdge, b - brR)
      ..arcToPoint(
        Offset(rEdge - brR, b),
        radius: Radius.circular(brR),
        clockwise: true,
      )
      ..lineTo(l + blR, b)
      ..arcToPoint(
        Offset(l, b - blR),
        radius: Radius.circular(blR),
        clockwise: true,
      );

    // Left edge (+ optional left tail aligned with mascot vertically)
    if (tail == MelbelBubbleTail.left && h >= tailSpan + tlR + blR + 4.0) {
      final minTop = t + tlR + 2.0;
      final maxTop = math.max(minTop, b - blR - tailSpan - 2.0);
      final tailTop = (t + 20.0).clamp(minTop, maxTop);
      final tailBot = tailTop + tailSpan;
      final tailMid = (tailTop + tailBot) / 2.0;
      path
        ..lineTo(l, tailBot)
        ..quadraticBezierTo(
            l - tailDepth * 0.62, tailBot - tailSpan * 0.15, rect.left, tailMid)
        ..quadraticBezierTo(
            l - tailDepth * 0.62, tailTop + tailSpan * 0.15, l, tailTop);
    }
    path
      ..lineTo(l, t + tlR)
      ..arcToPoint(
        Offset(l + tlR, t),
        radius: Radius.circular(tlR),
        clockwise: true,
      )
      ..close();

    return path;
  }

  @override
  void paint(Canvas canvas, Rect rect, {TextDirection? textDirection}) {
    if (borderWidth <= 0) return;
    final paint = Paint()
      ..color = borderColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = borderWidth
      ..strokeJoin = StrokeJoin.round
      ..strokeCap = StrokeCap.round;
    canvas.drawPath(
      getOuterPath(rect.deflate(borderWidth / 2.0), textDirection: textDirection),
      paint,
    );
  }

  @override
  bool operator ==(Object other) =>
      other is _MelbelBubbleShape &&
      other.tail == tail &&
      other.borderColor == borderColor &&
      other.borderWidth == borderWidth;

  @override
  int get hashCode => Object.hash(tail, borderColor, borderWidth);
}

/// Companion VA chat block pairing an interactive [MelbelMascot] avatar pad
/// with a [MelbelChatBubble] whose tail points directly toward Melbel.
class MelbelChatBlock extends StatelessWidget {
  const MelbelChatBlock({
    this.senderLabel = 'Melbel',
    this.badge,
    this.title,
    this.titleIsHeader = false,
    this.message,
    this.footnote,
    this.child,
    this.tone = MelbelCardTone.sage,
    this.expression = CatExpression.neutral,
    this.avatarOnRight = false,
    this.mascotWidth = 80,
    this.mascotHeight = 74,
    this.onPetted,
    super.key,
  });

  final String? senderLabel;
  final String? badge;
  final String? title;
  final bool titleIsHeader;
  final String? message;
  final String? footnote;
  final Widget? child;
  final MelbelCardTone tone;
  final CatExpression expression;
  final bool avatarOnRight;
  final double mascotWidth;
  final double mascotHeight;
  final ValueChanged<String>? onPetted;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxWidth < 310;
        final w = compact ? math.min(mascotWidth, 68.0) : mascotWidth;
        final h = compact ? math.min(mascotHeight, 62.0) : mascotHeight;

        final avatar = MelbelCushionAvatar(
          expression: expression,
          width: w,
          height: h,
          onPetted: onPetted,
        );

        final bubble = Expanded(
          child: MelbelChatBubble(
            senderLabel: senderLabel,
            badge: badge,
            title: title,
            titleIsHeader: titleIsHeader,
            message: message,
            footnote: footnote,
            tone: tone,
            tail:
                avatarOnRight ? MelbelBubbleTail.right : MelbelBubbleTail.left,
            child: child,
          ),
        );

        return Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: avatarOnRight
              ? [bubble, const SizedBox(width: 8), avatar]
              : [avatar, const SizedBox(width: 8), bubble],
        );
      },
    );
  }
}

/// Compact Bento quick-action tile used across Home and Calendar grids.
class MelbelActionTile extends StatelessWidget {
  const MelbelActionTile({
    required this.icon,
    required this.title,
    required this.onTap,
    this.subtitle,
    this.useHoney = false,
    this.compact = false,
    super.key,
  });

  final IconData icon;
  final String title;
  final String? subtitle;
  final bool useHoney;
  final bool compact;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final text = Theme.of(context).textTheme;
    final badgeBg = useHoney
        ? (isDark ? const Color(0xFF483E36) : const Color(0xFFFDF1D2))
        : (isDark ? const Color(0xFF564576) : const Color(0xFFECE2FA));
    final iconColor = useHoney
        ? (isDark ? const Color(0xFFF0CA6C) : const Color(0xFFC99D3E))
        : (isDark ? const Color(0xFFC5B2F2) : const Color(0xFF8368C4));
    final badgeSize = compact ? 48.0 : 56.0;

    return MelbelCard(
      padding: EdgeInsets.fromLTRB(
        16,
        compact ? 14 : 18,
        16,
        compact ? 14 : 20,
      ),
      borderRadius: 26,
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: badgeSize,
            height: badgeSize,
            decoration: BoxDecoration(
              color: badgeBg,
              borderRadius: BorderRadius.circular(compact ? 16 : 18),
            ),
            child: Icon(icon, size: compact ? 23 : 26, color: iconColor),
          ),
          SizedBox(height: compact ? 12 : 16),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(
              title,
              maxLines: 1,
              style: text.titleMedium?.copyWith(
                fontSize: 16.0,
                color: palette.onSurface,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          if (subtitle != null) ...[
            const SizedBox(height: 4),
            Text(
              subtitle!,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: text.labelSmall?.copyWith(
                color: palette.onSurfaceVariant,
                height: 1.3,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Three playful hand-drawn radiating ticks (`\ | /`) used near Melbel and
/// section titles for gentle emotional warmth.
class MelbelDoodleRays extends StatelessWidget {
  const MelbelDoodleRays({
    required this.color,
    this.size = 20.0,
    super.key,
  });

  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return ExcludeSemantics(
      child: RepaintBoundary(
        child: CustomPaint(
          size: Size.square(size),
          painter: _MelbelDoodleRaysPainter(color),
        ),
      ),
    );
  }
}

class _MelbelDoodleRaysPainter extends CustomPainter {
  const _MelbelDoodleRaysPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = math.max(2.0, size.width * 0.12)
      ..strokeCap = StrokeCap.round;

    final w = size.width;
    final h = size.height;
    // Left upward-left tick
    canvas.drawLine(
      Offset(w * 0.16, h * 0.44),
      Offset(w * 0.25, h * 0.84),
      paint,
    );
    // Center vertical-tilted tick
    canvas.drawLine(
      Offset(w * 0.56, h * 0.12),
      Offset(w * 0.48, h * 0.66),
      paint,
    );
    // Right diagonal tick
    canvas.drawLine(
      Offset(w * 0.94, h * 0.52),
      Offset(w * 0.66, h * 0.78),
      paint,
    );
  }

  @override
  bool shouldRepaint(_MelbelDoodleRaysPainter oldDelegate) =>
      oldDelegate.color != color;
}

/// Illustrated plush Hero Banner matching the Light & Dark Home mockups.
class MelbelHeroBanner extends StatelessWidget {
  const MelbelHeroBanner({
    required this.badge,
    required this.title,
    required this.message,
    this.senderLabel = 'Melbel',
    this.expression = CatExpression.neutral,
    this.onPetted,
    super.key,
  });

  final String senderLabel;
  final String badge;
  final String title;
  final String message;
  final CatExpression expression;
  final ValueChanged<String>? onPetted;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final textTheme = Theme.of(context).textTheme;
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final datePillBg =
        isDark ? const Color(0xFF55486F) : const Color(0xB8FFFDFB);
    final datePillFg =
        isDark ? palette.onSageSoft : palette.onSageSoft.withValues(alpha: 0.78);

    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxWidth < 340;
        final sceneWidth = compact ? 104.0 : 134.0;
        final sceneHeight = compact ? 98.0 : 126.0;
        final mascotWidth = compact ? 82.0 : 104.0;
        final mascotHeight = compact ? 72.0 : 92.0;

        return Container(
          width: double.infinity,
          decoration: BoxDecoration(
            color: palette.sageSoft,
            borderRadius: BorderRadius.circular(28),
            border: isDark
                ? Border.all(
                    color: palette.hairline.withValues(alpha: 0.45),
                    width: 1.0,
                  )
                : null,
            boxShadow: isDark
                ? [
                    BoxShadow(
                      color: Colors.black.withValues(alpha: 0.22),
                      blurRadius: 18,
                      offset: const Offset(0, 6),
                    ),
                  ]
                : [
                    BoxShadow(
                      color: const Color(0xFF534070).withValues(alpha: 0.06),
                      blurRadius: 22,
                      offset: const Offset(0, 7),
                    ),
                  ],
          ),
          clipBehavior: Clip.antiAlias,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: Padding(
                  padding: EdgeInsets.fromLTRB(
                    compact ? 16 : 20,
                    18,
                    6,
                    18,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Wrap(
                        spacing: 8,
                        runSpacing: 4,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Container(
                                width: 8,
                                height: 8,
                                decoration: BoxDecoration(
                                  shape: BoxShape.circle,
                                  color: palette.amber,
                                ),
                              ),
                              const SizedBox(width: 6),
                              Text(
                                senderLabel,
                                style: textTheme.titleSmall?.copyWith(
                                  fontSize: 14,
                                  fontWeight: FontWeight.w600,
                                  color: palette.onSageSoft,
                                ),
                              ),
                            ],
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 10,
                              vertical: 3,
                            ),
                            decoration: BoxDecoration(
                              color: datePillBg,
                              borderRadius: BorderRadius.circular(999),
                            ),
                            child: Text(
                              badge,
                              style: textTheme.labelSmall?.copyWith(
                                fontSize: 12,
                                fontWeight: FontWeight.w600,
                                color: datePillFg,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 10),
                      Semantics(
                        header: true,
                        child: Text(
                          title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: textTheme.headlineSmall?.copyWith(
                            fontSize: compact ? 20 : 24,
                            fontWeight: FontWeight.w800,
                            height: 1.2,
                            letterSpacing: -0.4,
                            color: palette.onSageSoft,
                          ),
                        ),
                      ),
                      const SizedBox(height: 6),
                      AnimatedSwitcher(
                        duration: reducedMotion
                            ? Duration.zero
                            : const Duration(milliseconds: 200),
                        child: Align(
                          key: ValueKey(message),
                          alignment: Alignment.centerLeft,
                          child: Text(
                            message,
                            style: textTheme.bodyMedium?.copyWith(
                              fontSize: 15,
                              height: 1.4,
                              color: palette.onSageSoft.withValues(alpha: 0.86),
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              Padding(
                padding: EdgeInsets.fromLTRB(
                  0,
                  10,
                  compact ? 8 : 12,
                  8,
                ),
                child: SizedBox(
                  width: sceneWidth,
                  height: sceneHeight,
                  child: Stack(
                    clipBehavior: Clip.none,
                    alignment: Alignment.center,
                    children: [
                      Positioned.fill(
                        child: RepaintBoundary(
                          child: CustomPaint(
                            painter: _MelbelCushionScenePainter(isDark: isDark),
                          ),
                        ),
                      ),
                      Positioned(
                        bottom: compact ? 11 : 14,
                        child: MelbelMascot(
                          expression: expression,
                          width: mascotWidth,
                          height: mascotHeight,
                          forceLightSvg: true,
                          onPetted: onPetted,
                        ),
                      ),
                      Positioned(
                        top: compact ? 10 : 12,
                        right: compact ? 8 : 10,
                        child: Transform.rotate(
                          angle: 0.35,
                          child: MelbelDoodleRays(
                            size: compact ? 16 : 20,
                            color: isDark ? palette.amber : palette.sage,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _MelbelCushionScenePainter extends CustomPainter {
  const _MelbelCushionScenePainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;

    // 1. Soft moon circle halo behind Melbel
    final moonPaint = Paint()
      ..color = isDark ? const Color(0xFFA797C4) : const Color(0xFFFFF8E2);
    final moonRadius = math.min(w, h) * 0.43;
    final moonCenter = Offset(w * 0.56, h * 0.45);
    canvas.drawCircle(moonCenter, moonRadius, moonPaint);

    // 2. Two tiny sage leaves peeking from the left edge of the pillow
    final leafPaint = Paint()
      ..color = isDark ? const Color(0xFF94A681) : const Color(0xFFAEBA99)
      ..style = PaintingStyle.fill;
    final veinPaint = Paint()
      ..color = isDark ? const Color(0xFF7D8E6B) : const Color(0xFF95A37E)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2
      ..strokeCap = StrokeCap.round;

    void drawLeaf(Offset base, double angle, double len, double halfWidth) {
      canvas.save();
      canvas.translate(base.dx, base.dy);
      canvas.rotate(angle);
      final path = Path()
        ..moveTo(0, 0)
        ..quadraticBezierTo(-halfWidth, -len * 0.45, 0, -len)
        ..quadraticBezierTo(halfWidth, -len * 0.45, 0, 0)
        ..close();
      canvas.drawPath(path, leafPaint);
      canvas.drawLine(Offset(0, -len * 0.1), Offset(0, -len * 0.78), veinPaint);
      canvas.restore();
    }

    drawLeaf(Offset(w * 0.19, h * 0.78), -0.58, h * 0.25, w * 0.055);
    drawLeaf(Offset(w * 0.16, h * 0.80), -1.08, h * 0.20, w * 0.048);

    // 3. Soft shadow underneath pillow
    final shadowRect = RRect.fromRectAndRadius(
      Rect.fromLTRB(w * 0.14, h * 0.85, w * 0.96, h * 0.97),
      const Radius.circular(99),
    );
    canvas.drawRRect(
      shadowRect,
      Paint()
        ..color = (isDark ? Colors.black : const Color(0xFF6B5399))
            .withValues(alpha: isDark ? 0.24 : 0.16),
    );

    // 4. Plush purple pillow cushion
    final pillowRect = RRect.fromRectAndRadius(
      Rect.fromLTRB(w * 0.10, h * 0.71, w * 0.96, h * 0.94),
      Radius.circular(h * 0.14),
    );
    canvas.drawRRect(
      pillowRect,
      Paint()
        ..color =
            isDark ? const Color(0xFF9B83C6) : const Color(0xFFC2AFE8),
    );

    final topPillowRect = RRect.fromRectAndRadius(
      Rect.fromLTRB(w * 0.12, h * 0.69, w * 0.94, h * 0.88),
      Radius.circular(h * 0.12),
    );
    canvas.drawRRect(
      topPillowRect,
      Paint()
        ..color =
            isDark ? const Color(0xFFB9A3DE) : const Color(0xFFD6C6F4),
    );
  }

  @override
  bool shouldRepaint(_MelbelCushionScenePainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}

/// Floating pill dock bottom navigation matching both Light & Dark mockups.
class MelbelFloatingNavBar extends StatelessWidget {
  const MelbelFloatingNavBar({
    required this.selectedIndex,
    required this.onSelected,
    super.key,
  });

  final int selectedIndex;
  final ValueChanged<int> onSelected;

  static const _items = [
    (Icons.today_outlined, Icons.today_rounded, 'Hôm nay'),
    (Icons.calendar_month_outlined, Icons.calendar_month_rounded, 'Lịch'),
    (Icons.add_circle_outline_rounded, Icons.add_circle_rounded, 'Thêm việc'),
    (Icons.tune_outlined, Icons.tune_rounded, 'Tài khoản'),
  ];

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final highContrast = MediaQuery.highContrastOf(context);
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final activeTint = highContrast
        ? palette.onSageSoft
        : (isDark ? const Color(0xFFD4C5FA) : palette.accentInk);

    return ColoredBox(
      color: palette.background,
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          16,
          4,
          16,
          14 + MediaQuery.paddingOf(context).bottom,
        ),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
          decoration: BoxDecoration(
            color: isDark ? const Color(0xFF272136) : palette.surface,
            borderRadius: BorderRadius.circular(28),
            border: highContrast
                ? Border.all(color: palette.controlOutline, width: 1.5)
                : isDark
                    ? Border.all(
                        color: palette.hairline.withValues(alpha: 0.65),
                        width: 1.0,
                      )
                    : null,
            boxShadow: isDark
                ? [
                    BoxShadow(
                      color: Colors.black.withValues(alpha: 0.28),
                      blurRadius: 20,
                      offset: const Offset(0, 6),
                    ),
                  ]
                : [
                    BoxShadow(
                      color: const Color(0xFF534070).withValues(alpha: 0.08),
                      blurRadius: 24,
                      offset: const Offset(0, 8),
                    ),
                  ],
          ),
          child: SizedBox(
            height: 60,
            child: Stack(
              children: [
                Positioned.fill(
                  child: RepaintBoundary(
                    child: AnimatedAlign(
                      alignment: Alignment(
                        -1.0 + (2.0 * selectedIndex / (_items.length - 1)),
                        0,
                      ),
                      duration: reducedMotion
                          ? Duration.zero
                          : const Duration(milliseconds: 240),
                      curve: Curves.easeOutCubic,
                      child: FractionallySizedBox(
                        widthFactor: 1 / _items.length,
                        heightFactor: 1.0,
                        child: Padding(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 6,
                            vertical: 3,
                          ),
                          child: DecoratedBox(
                            decoration: BoxDecoration(
                              color: palette.selectedSurface,
                              borderRadius: BorderRadius.circular(20),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
                Row(
                  children: [
                    for (var i = 0; i < _items.length; i++)
                      Expanded(
                        child: _FloatingNavTab(
                          icon: i == selectedIndex
                              ? _items[i].$2
                              : _items[i].$1,
                          label: _items[i].$3,
                          isSelected: i == selectedIndex,
                          activeColor: activeTint,
                          inactiveColor: palette.onSurfaceVariant,
                          reducedMotion: reducedMotion,
                          onTap: () => onSelected(i),
                        ),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _FloatingNavTab extends StatelessWidget {
  const _FloatingNavTab({
    required this.icon,
    required this.label,
    required this.isSelected,
    required this.activeColor,
    required this.inactiveColor,
    required this.reducedMotion,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool isSelected;
  final Color activeColor;
  final Color inactiveColor;
  final bool reducedMotion;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final fg = isSelected ? activeColor : inactiveColor;
    return Semantics(
      selected: isSelected,
      button: true,
      label: label,
      excludeSemantics: true,
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 48),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(20),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 2, vertical: 6),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                AnimatedScale(
                  scale: isSelected && !reducedMotion ? 1.06 : 1.0,
                  duration: reducedMotion
                      ? Duration.zero
                      : const Duration(milliseconds: 160),
                  curve: Curves.easeOutCubic,
                  child: Icon(icon, size: 22, color: fg),
                ),
                const SizedBox(height: 4),
                Flexible(
                  child: FittedBox(
                    fit: BoxFit.scaleDown,
                    child: Text(
                      label,
                      maxLines: 1,
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                            fontSize: 12,
                            fontWeight:
                                isSelected ? FontWeight.w700 : FontWeight.w500,
                            color: fg,
                          ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Plush lavender cloud backdrop with subtle corner paw print and bottom sparkle
/// ticks matching the reference Light and Dark storybooks cleanly.
class MelbelPageBackdrop extends StatelessWidget {
  const MelbelPageBackdrop({
    required this.child,
    this.showTopCloud = true,
    this.showBottomClouds = true,
    super.key,
  });

  final Widget child;
  final bool showTopCloud;
  final bool showBottomClouds;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return ColoredBox(
      color: palette.background,
      child: ClipRect(
        child: Stack(
          fit: StackFit.passthrough,
          children: [
            Positioned.fill(
              child: IgnorePointer(
                child: RepaintBoundary(
                  child: CustomPaint(
                    painter: _MelbelPageBackdropPainter(
                      isDark: isDark,
                      showTopCloud: showTopCloud,
                      showBottomClouds: showBottomClouds,
                    ),
                  ),
                ),
              ),
            ),
            child,
          ],
        ),
      ),
    );
  }
}

class _MelbelPageBackdropPainter extends CustomPainter {
  const _MelbelPageBackdropPainter({
    required this.isDark,
    required this.showTopCloud,
    required this.showBottomClouds,
  });

  final bool isDark;
  final bool showTopCloud;
  final bool showBottomClouds;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    final cloudPaint = Paint()
      ..color = isDark
          ? const Color(0xFF332948).withValues(alpha: 0.92)
          : const Color(0xFFECE2FA).withValues(alpha: 0.92);

    if (showTopCloud) {
      // Top-right scalloped pastel cloud
      canvas.drawCircle(Offset(w - 18, 16), 48, cloudPaint);
      canvas.drawCircle(Offset(w - 60, 4), 32, cloudPaint);
      canvas.drawCircle(Offset(w + 4, 52), 34, cloudPaint);

      // Cute subtle paw print tucked inside top-right cloud
      final pawPaint = Paint()
        ..color = isDark
            ? const Color(0xFF9F88D4).withValues(alpha: 0.72)
            : Colors.white.withValues(alpha: 0.88);
      canvas.save();
      canvas.translate(w - 28, 30);
      canvas.rotate(-0.22);
      canvas.drawOval(
        Rect.fromCenter(center: const Offset(0, 3.2), width: 10.5, height: 8.0),
        pawPaint,
      );
      canvas.drawCircle(const Offset(-6.2, -3.0), 2.1, pawPaint);
      canvas.drawCircle(const Offset(-2.1, -5.2), 2.2, pawPaint);
      canvas.drawCircle(const Offset(2.3, -5.1), 2.2, pawPaint);
      canvas.drawCircle(const Offset(6.2, -2.8), 2.0, pawPaint);
      canvas.restore();
    }

    if (showBottomClouds) {
      // Bottom-left soft cloud mound
      canvas.drawCircle(Offset(14, h + 10), 42, cloudPaint);
      canvas.drawCircle(Offset(58, h + 18), 32, cloudPaint);
      canvas.drawCircle(Offset(-10, h - 14), 28, cloudPaint);

      // Bottom-right soft cloud mound
      canvas.drawCircle(Offset(w - 16, h + 8), 46, cloudPaint);
      canvas.drawCircle(Offset(w - 62, h + 16), 34, cloudPaint);
      canvas.drawCircle(Offset(w + 8, h - 14), 30, cloudPaint);

      // Two tiny playful sparkle ticks above bottom-right cloud
      final tickPaint = Paint()
        ..color = isDark ? const Color(0xFFB49EE8) : const Color(0xFF9E85DA)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.1
        ..strokeCap = StrokeCap.round;
      canvas.drawLine(
        Offset(w - 34, h - 58),
        Offset(w - 30, h - 50),
        tickPaint,
      );
      canvas.drawLine(
        Offset(w - 21, h - 55),
        Offset(w - 22, h - 46),
        tickPaint,
      );
    }
  }

  @override
  bool shouldRepaint(_MelbelPageBackdropPainter oldDelegate) =>
      oldDelegate.isDark != isDark ||
      oldDelegate.showTopCloud != showTopCloud ||
      oldDelegate.showBottomClouds != showBottomClouds;
}

/// Crisp 4-point concave diamond sparkle star used across Melbel story scenes.
class MelbelSparkleStar extends StatelessWidget {
  const MelbelSparkleStar({
    required this.color,
    this.size = 16.0,
    super.key,
  });

  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return ExcludeSemantics(
      child: RepaintBoundary(
        child: CustomPaint(
          size: Size.square(size),
          painter: _MelbelSparkleStarPainter(color),
        ),
      ),
    );
  }
}

class _MelbelSparkleStarPainter extends CustomPainter {
  const _MelbelSparkleStarPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    final cx = w * 0.5;
    final cy = h * 0.5;
    final path = Path()
      ..moveTo(cx, 0)
      ..quadraticBezierTo(cx, cy, w, cy)
      ..quadraticBezierTo(cx, cy, cx, h)
      ..quadraticBezierTo(cx, cy, 0, cy)
      ..quadraticBezierTo(cx, cy, cx, 0)
      ..close();
    canvas.drawPath(path, Paint()..color = color);
  }

  @override
  bool shouldRepaint(_MelbelSparkleStarPainter oldDelegate) =>
      oldDelegate.color != color;
}

/// Mini plush-cushion Melbel avatar with sage leaves, gold sparkle star, and
/// doodle rays (no circular ring), used in chat blocks and onboarding cards.
class MelbelCushionAvatar extends StatelessWidget {
  const MelbelCushionAvatar({
    this.expression = CatExpression.neutral,
    this.width = 80,
    this.height = 72,
    this.onPetted,
    super.key,
  });

  final CatExpression expression;
  final double width;
  final double height;
  final ValueChanged<String>? onPetted;

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final boxW = width + 18;
    final boxH = height + 16;

    return SizedBox(
      width: boxW,
      height: boxH,
      child: Stack(
        clipBehavior: Clip.none,
        alignment: Alignment.center,
        children: [
          Positioned.fill(
            child: IgnorePointer(
              child: RepaintBoundary(
                child: CustomPaint(
                  painter: _MelbelCushionAvatarPainter(isDark: isDark),
                ),
              ),
            ),
          ),
          Positioned(
            left: boxW * 0.08,
            top: 2,
            child: MelbelSparkleStar(
              size: math.max(10, width * 0.16),
              color: isDark ? const Color(0xFFF3CA52) : const Color(0xFFF3C854),
            ),
          ),
          Positioned(
            bottom: 7,
            child: MelbelMascot(
              expression: expression,
              width: width * 0.88,
              height: height * 0.86,
              forceLightSvg: true,
              onPetted: onPetted,
            ),
          ),
          Positioned(
            right: 2,
            top: 2,
            child: Transform.rotate(
              angle: 0.34,
              child: MelbelDoodleRays(
                size: math.max(13, width * 0.20),
                color: isDark ? const Color(0xFFB49EE8) : const Color(0xFF9E85DA),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MelbelCushionAvatarPainter extends CustomPainter {
  const _MelbelCushionAvatarPainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;

    // 0. Soft moon circle halo behind Melbel (matches Home hero banner so dark
    //    Melbel stands out cleanly in both Light and Dark modes)
    final moonPaint = Paint()
      ..color = isDark ? const Color(0xFFA797C4) : const Color(0xFFFFF8E2);
    final moonRadius = math.min(w, h) * 0.41;
    canvas.drawCircle(Offset(w * 0.52, h * 0.48), moonRadius, moonPaint);

    // 1. Two curved sage leaves behind Melbel's left shoulder
    final leafPaint = Paint()
      ..color = isDark ? const Color(0xFF8FA379) : const Color(0xFFAEC096);
    final veinPaint = Paint()
      ..color = isDark ? const Color(0xFF72855F) : const Color(0xFF91A37B)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.1
      ..strokeCap = StrokeCap.round;

    void drawLeaf(Offset base, double angle, double len, double halfW) {
      canvas.save();
      canvas.translate(base.dx, base.dy);
      canvas.rotate(angle);
      final p = Path()
        ..moveTo(0, 0)
        ..quadraticBezierTo(-halfW, -len * 0.45, 0, -len)
        ..quadraticBezierTo(halfW, -len * 0.45, 0, 0)
        ..close();
      canvas.drawPath(p, leafPaint);
      canvas.drawLine(Offset(0, -len * 0.12), Offset(0, -len * 0.78), veinPaint);
      canvas.restore();
    }

    drawLeaf(Offset(w * 0.24, h * 0.74), -0.58, h * 0.42, w * 0.085);
    drawLeaf(Offset(w * 0.21, h * 0.78), -1.08, h * 0.32, w * 0.072);

    // 2. Soft ambient shadow under mini cushion
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTRB(w * 0.12, h * 0.85, w * 0.90, h * 0.98),
        const Radius.circular(99),
      ),
      Paint()
        ..color = (isDark ? Colors.black : const Color(0xFF6B5399))
            .withValues(alpha: isDark ? 0.26 : 0.15),
    );

    // 3. Plush purple pillow cushion
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTRB(w * 0.08, h * 0.73, w * 0.92, h * 0.95),
        Radius.circular(h * 0.16),
      ),
      Paint()
        ..color = isDark ? const Color(0xFF9178C3) : const Color(0xFFC0ACE7),
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTRB(w * 0.11, h * 0.70, w * 0.89, h * 0.88),
        Radius.circular(h * 0.14),
      ),
      Paint()
        ..color = isDark ? const Color(0xFFB39ADF) : const Color(0xFFD6C7F5),
    );
  }

  @override
  bool shouldRepaint(_MelbelCushionAvatarPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}

/// Decorative art motif shown inside a [MelbelSectionBanner].
enum MelbelSectionArt { happyMelbel, sprout }

/// Rounded plush lavender section banner with storybook illustrations matching
/// the Help and Calendar setup headers in both Light and Dark modes.
class MelbelSectionBanner extends StatelessWidget {
  const MelbelSectionBanner({
    required this.title,
    required this.art,
    super.key,
  });

  final String title;
  final MelbelSectionArt art;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final bg = isDark ? const Color(0xFF332947) : const Color(0xFFF1E8FD);

    return Semantics(
      header: true,
      child: Container(
        constraints: const BoxConstraints(minHeight: 58),
        clipBehavior: Clip.antiAlias,
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(24),
          border: isDark
              ? Border.all(
                  color: palette.hairline.withValues(alpha: 0.50),
                  width: 1.0,
                )
              : null,
          boxShadow: isDark
              ? [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.20),
                    blurRadius: 14,
                    offset: const Offset(0, 5),
                  ),
                ]
              : [
                  BoxShadow(
                    color: const Color(0xFF534070).withValues(alpha: 0.05),
                    blurRadius: 16,
                    offset: const Offset(0, 5),
                  ),
                ],
        ),
        child: Stack(
          alignment: Alignment.centerLeft,
          children: [
            Positioned(
              right: 0,
              bottom: 0,
              top: 0,
              width: 128,
              child: IgnorePointer(
                child: RepaintBoundary(
                  child: CustomPaint(
                    painter: _MelbelSectionBannerPainter(
                      art: art,
                      isDark: isDark,
                    ),
                  ),
                ),
              ),
            ),
            if (art == MelbelSectionArt.happyMelbel) ...[
              const Positioned(
                right: 16,
                bottom: -6,
                child: IgnorePointer(
                  child: MelbelMascot(
                    expression: CatExpression.happy,
                    width: 64,
                    height: 56,
                    forceLightSvg: true,
                  ),
                ),
              ),
              Positioned(
                right: 16,
                bottom: 2,
                child: IgnorePointer(
                  child: RepaintBoundary(
                    child: CustomPaint(
                      size: const Size(64, 14),
                      painter: _SectionRimPawsPainter(isDark: isDark),
                    ),
                  ),
                ),
              ),
              Positioned(
                right: 6,
                top: 8,
                child: IgnorePointer(
                  child: Transform.rotate(
                    angle: 0.34,
                    child: MelbelDoodleRays(
                      size: 16,
                      color: isDark
                          ? const Color(0xFFB49EE8)
                          : const Color(0xFF534070),
                    ),
                  ),
                ),
              ),
            ],
            Padding(
              padding: const EdgeInsets.fromLTRB(18, 14, 118, 14),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Flexible(
                    child: Text(
                      title,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontSize: 17.5,
                        fontWeight: FontWeight.w800,
                        letterSpacing: -0.3,
                        color: palette.onSurface,
                      ),
                    ),
                  ),
                  if (art == MelbelSectionArt.sprout) ...[
                    const SizedBox(width: 5),
                    Transform.translate(
                      offset: const Offset(0, -6),
                      child: Transform.rotate(
                        angle: 0.25,
                        child: MelbelDoodleRays(
                          size: 15,
                          color: isDark
                              ? const Color(0xFFB49EE8)
                              : const Color(0xFF9E85DA),
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _MelbelSectionBannerPainter extends CustomPainter {
  const _MelbelSectionBannerPainter({
    required this.art,
    required this.isDark,
  });

  final MelbelSectionArt art;
  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;

    if (art == MelbelSectionArt.happyMelbel) {
      // Soft moon halo behind peeking Melbel so dark Melbel never blends into
      // Dark mode banner backgrounds
      final moonPaint = Paint()
        ..color = isDark ? const Color(0xFFA797C4) : const Color(0xFFFFF8E2);
      canvas.drawCircle(Offset(w - 48, h * 0.54), 30, moonPaint);
    }

    final cloudBack = Paint()
      ..color = isDark
          ? const Color(0xFF473766).withValues(alpha: 0.85)
          : const Color(0xFFD5C3F4).withValues(alpha: 0.72);
    final cloudFront = Paint()
      ..color = isDark
          ? const Color(0xFF5A487D).withValues(alpha: 0.85)
          : const Color(0xFFE1D3FA).withValues(alpha: 0.85);

    // Soft layered clouds at bottom-right
    canvas.drawCircle(Offset(w * 0.25, h * 0.96), 24, cloudBack);
    canvas.drawCircle(Offset(w * 0.56, h * 0.86), 30, cloudBack);
    canvas.drawCircle(Offset(w * 0.86, h * 0.78), 32, cloudBack);
    canvas.drawCircle(Offset(w * 0.36, h * 1.05), 24, cloudFront);
    canvas.drawCircle(Offset(w * 0.72, h * 0.98), 26, cloudFront);

    // Gold 4-point sparkle star above-left of the scene
    final starColor =
        isDark ? const Color(0xFFF3CA52) : const Color(0xFFF3C854);
    final starCx = art == MelbelSectionArt.sprout ? w * 0.36 : w * 0.20;
    final starCy = art == MelbelSectionArt.sprout ? h * 0.34 : h * 0.32;
    const starR = 6.5;
    final starPath = Path()
      ..moveTo(starCx, starCy - starR)
      ..quadraticBezierTo(starCx, starCy, starCx + starR, starCy)
      ..quadraticBezierTo(starCx, starCy, starCx, starCy + starR)
      ..quadraticBezierTo(starCx, starCy, starCx - starR, starCy)
      ..quadraticBezierTo(starCx, starCy, starCx, starCy - starR)
      ..close();
    canvas.drawPath(starPath, Paint()..color = starColor);

    final leafPaint = Paint()
      ..color = isDark ? const Color(0xFF90A779) : const Color(0xFFB0C394);
    final leafLight = Paint()
      ..color = isDark ? const Color(0xFFA4BA8D) : const Color(0xFFC1D2A6);

    void drawLeaf(
      Offset base,
      double angle,
      double len,
      double halfW,
      Paint paint,
    ) {
      canvas.save();
      canvas.translate(base.dx, base.dy);
      canvas.rotate(angle);
      final p = Path()
        ..moveTo(0, 0)
        ..quadraticBezierTo(-halfW, -len * 0.45, 0, -len)
        ..quadraticBezierTo(halfW, -len * 0.45, 0, 0)
        ..close();
      canvas.drawPath(p, paint);
      canvas.restore();
    }

    if (art == MelbelSectionArt.sprout) {
      drawLeaf(Offset(w * 0.66, h * 0.88), -0.45, 28, 7.5, leafLight);
      drawLeaf(Offset(w * 0.68, h * 0.88), 0.58, 22, 6.2, leafPaint);
    } else {
      drawLeaf(Offset(w * 0.34, h * 0.86), -0.62, 22, 6.0, leafPaint);
    }
  }

  @override
  bool shouldRepaint(_MelbelSectionBannerPainter oldDelegate) =>
      oldDelegate.art != art || oldDelegate.isDark != isDark;
}

class _SectionRimPawsPainter extends CustomPainter {
  const _SectionRimPawsPainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final pawFill = Paint()..color = const Color(0xFF2B2732);
    final rimStroke = Paint()
      ..color = isDark ? const Color(0xFF6B598F) : const Color(0xFFD6C7F5)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2;

    for (final x in [size.width * 0.24, size.width * 0.76]) {
      final rect = Rect.fromCenter(
        center: Offset(x, size.height * 0.5),
        width: 14,
        height: 10,
      );
      canvas.drawOval(rect, pawFill);
      canvas.drawOval(rect, rimStroke);
    }
  }

  @override
  bool shouldRepaint(_SectionRimPawsPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}

/// Circular user avatar framed with cute rounded cat ears perched on top.
class MelbelCatEarAvatar extends StatelessWidget {
  const MelbelCatEarAvatar({
    this.photoUrl,
    this.displayName,
    this.size = 76,
    super.key,
  });

  final String? photoUrl;
  final String? displayName;
  final double size;

  Widget _buildFallback(MelbelPalette palette, bool isDark) {
    final trimmed = displayName?.trim() ?? '';
    final hasCustomName =
        trimmed.isNotEmpty && trimmed != 'Tài khoản của bạn';
    final initial = hasCustomName ? trimmed[0].toUpperCase() : null;
    final bg = isDark ? palette.selectedSurface : const Color(0xFFFFF8E2);

    return DecoratedBox(
      decoration: BoxDecoration(shape: BoxShape.circle, color: bg),
      child: Center(
        child: initial != null
            ? Text(
                initial,
                style: TextStyle(
                  fontSize: size * 0.38,
                  fontWeight: FontWeight.w800,
                  color: palette.accentInk,
                ),
              )
            : Icon(
                Icons.person_rounded,
                size: size * 0.48,
                color: palette.accentInk,
              ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final borderColor =
        isDark ? const Color(0xFF6B5891) : Colors.white.withValues(alpha: 0.92);
    final earOuter = isDark ? const Color(0xFF4F3E6E) : palette.sage;
    final earInner = isDark ? const Color(0xFF2B223C) : const Color(0xFFFFF4DC);
    final trimmedUrl = photoUrl?.trim();
    final fallback = _buildFallback(palette, isDark);

    return Semantics(
      image: true,
      label: 'Ảnh đại diện tài khoản Google',
      child: SizedBox(
        width: size + 8,
        height: size + 12,
        child: Stack(
          clipBehavior: Clip.none,
          alignment: Alignment.bottomCenter,
          children: [
            Positioned.fill(
              child: IgnorePointer(
                child: RepaintBoundary(
                  child: CustomPaint(
                    painter: _CatEarAvatarFramePainter(
                      circleDiameter: size,
                      outerColor: earOuter,
                      innerColor: earInner,
                      borderColor: borderColor,
                    ),
                  ),
                ),
              ),
            ),
            Container(
              width: size,
              height: size,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: isDark ? palette.mutedSurface : const Color(0xFFFFF8E2),
                border: Border.all(color: borderColor, width: 2.5),
              ),
              clipBehavior: Clip.antiAlias,
              child: ClipOval(
                child: trimmedUrl != null && trimmedUrl.isNotEmpty
                    ? Image.network(
                        trimmedUrl,
                        width: size,
                        height: size,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => fallback,
                        loadingBuilder: (_, child, progress) =>
                            progress == null ? child : fallback,
                      )
                    : fallback,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CatEarAvatarFramePainter extends CustomPainter {
  const _CatEarAvatarFramePainter({
    required this.circleDiameter,
    required this.outerColor,
    required this.innerColor,
    required this.borderColor,
  });

  final double circleDiameter;
  final Color outerColor;
  final Color innerColor;
  final Color borderColor;

  @override
  void paint(Canvas canvas, Size size) {
    final radius = circleDiameter / 2;
    final center = Offset(size.width / 2, size.height - radius);
    final outerFill = Paint()..color = outerColor;
    final innerFill = Paint()..color = innerColor;
    final stroke = Paint()
      ..color = borderColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.2
      ..strokeJoin = StrokeJoin.round;

    void drawEar(double dir) {
      final baseInner = center + Offset(dir * radius * 0.28, -radius * 0.88);
      final baseOuter = center + Offset(dir * radius * 0.88, -radius * 0.36);
      final tip = center + Offset(dir * radius * 0.80, -radius * 1.18);

      final path = Path()
        ..moveTo(baseInner.dx, baseInner.dy)
        ..quadraticBezierTo(
          center.dx + dir * radius * 0.56,
          center.dy - radius * 1.24,
          tip.dx,
          tip.dy,
        )
        ..quadraticBezierTo(
          center.dx + dir * radius * 0.96,
          center.dy - radius * 0.96,
          baseOuter.dx,
          baseOuter.dy,
        )
        ..close();

      canvas.drawPath(path, outerFill);
      canvas.drawPath(path, stroke);

      final innerTip = Offset.lerp(center, tip, 0.92)!;
      final innerA = Offset.lerp(baseInner, innerTip, 0.25)!;
      final innerB = Offset.lerp(baseOuter, innerTip, 0.25)!;
      final innerCenter = Offset.lerp(innerA, innerB, 0.5)!;
      final innerPath = Path()
        ..moveTo(innerA.dx, innerA.dy)
        ..quadraticBezierTo(
          innerTip.dx,
          innerTip.dy,
          innerB.dx,
          innerB.dy,
        )
        ..quadraticBezierTo(
          innerCenter.dx,
          innerCenter.dy,
          innerA.dx,
          innerA.dy,
        )
        ..close();
      canvas.drawPath(innerPath, innerFill);
    }

    drawEar(-1);
    drawEar(1);
  }

  @override
  bool shouldRepaint(_CatEarAvatarFramePainter oldDelegate) =>
      oldDelegate.circleDiameter != circleDiameter ||
      oldDelegate.outerColor != outerColor ||
      oldDelegate.innerColor != innerColor ||
      oldDelegate.borderColor != borderColor;
}

/// Unified 24-hour (`HH:mm`) custom Melbel digital alarm-clock picker.
Future<TimeOfDay?> showMelbelTimePicker({
  required BuildContext context,
  required TimeOfDay initialTime,
  String? helpText,
  String? confirmText,
  TimePickerEntryMode initialEntryMode = TimePickerEntryMode.dial,
}) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  final reducedMotion = MediaQuery.disableAnimationsOf(context);
  return showGeneralDialog<TimeOfDay>(
    context: context,
    barrierDismissible: true,
    barrierLabel: 'Đóng chọn giờ',
    barrierColor: Colors.black.withValues(alpha: isDark ? 0.62 : 0.36),
    transitionDuration:
        reducedMotion ? Duration.zero : const Duration(milliseconds: 220),
    pageBuilder: (dialogContext, _, __) => _MelbelTimePickerDialog(
      initialTime: initialTime,
      helpText: helpText,
      confirmText: confirmText,
    ),
    transitionBuilder: (dialogContext, animation, _, child) {
      if (reducedMotion) return child;
      final curved = CurvedAnimation(
        parent: animation,
        curve: Curves.easeOutCubic,
        reverseCurve: Curves.easeInCubic,
      );
      return FadeTransition(
        opacity: curved,
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.94, end: 1.0).animate(curved),
          child: child,
        ),
      );
    },
  );
}

class _MelbelTimePickerDialog extends StatefulWidget {
  const _MelbelTimePickerDialog({
    required this.initialTime,
    this.helpText,
    this.confirmText,
  });

  final TimeOfDay initialTime;
  final String? helpText;
  final String? confirmText;

  @override
  State<_MelbelTimePickerDialog> createState() =>
      _MelbelTimePickerDialogState();
}

class _MelbelTimePickerDialogState extends State<_MelbelTimePickerDialog> {
  late final TextEditingController _hourController;
  late final TextEditingController _minuteController;
  final FocusNode _hourFocus = FocusNode();
  final FocusNode _minuteFocus = FocusNode();
  double _hourDragDelta = 0;
  double _minuteDragDelta = 0;

  @override
  void initState() {
    super.initState();
    _hourController = TextEditingController(
      text: widget.initialTime.hour.toString().padLeft(2, '0'),
    );
    _minuteController = TextEditingController(
      text: widget.initialTime.minute.toString().padLeft(2, '0'),
    );
    _hourFocus.addListener(_onHourFocusChange);
    _minuteFocus.addListener(_onMinuteFocusChange);
  }

  void _onHourFocusChange() {
    if (_hourFocus.hasFocus) {
      _hourController.selection = TextSelection(
        baseOffset: 0,
        extentOffset: _hourController.text.length,
      );
    } else {
      final h = _parsedHour;
      if (h != null) {
        _hourController.text = h.toString().padLeft(2, '0');
      }
    }
    if (mounted) setState(() {});
  }

  void _onMinuteFocusChange() {
    if (_minuteFocus.hasFocus) {
      _minuteController.selection = TextSelection(
        baseOffset: 0,
        extentOffset: _minuteController.text.length,
      );
    } else {
      final m = _parsedMinute;
      if (m != null) {
        _minuteController.text = m.toString().padLeft(2, '0');
      }
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _hourFocus.removeListener(_onHourFocusChange);
    _minuteFocus.removeListener(_onMinuteFocusChange);
    _hourFocus.dispose();
    _minuteFocus.dispose();
    _hourController.dispose();
    _minuteController.dispose();
    super.dispose();
  }

  int? get _parsedHour {
    final raw = _hourController.text.trim();
    if (raw.isEmpty) return null;
    final val = int.tryParse(raw);
    if (val == null || val < 0 || val > 23) return null;
    return val;
  }

  int? get _parsedMinute {
    final raw = _minuteController.text.trim();
    if (raw.isEmpty) return null;
    final val = int.tryParse(raw);
    if (val == null || val < 0 || val > 59) return null;
    return val;
  }

  bool get _isValid => _parsedHour != null && _parsedMinute != null;

  void _nudgeHour(int delta) {
    final current = _parsedHour ?? widget.initialTime.hour;
    final next = (current + delta) % 24;
    final wrapped = next < 0 ? next + 24 : next;
    _hourController.text = wrapped.toString().padLeft(2, '0');
    HapticFeedback.selectionClick();
    setState(() {});
  }

  void _nudgeMinute(int delta) {
    final current = _parsedMinute ?? widget.initialTime.minute;
    final next = (current + delta) % 60;
    final wrapped = next < 0 ? next + 60 : next;
    _minuteController.text = wrapped.toString().padLeft(2, '0');
    HapticFeedback.selectionClick();
    setState(() {});
  }

  void _submit() {
    final h = _parsedHour;
    final m = _parsedMinute;
    if (h == null || m == null) return;
    HapticFeedback.selectionClick();
    Navigator.of(context).pop(TimeOfDay(hour: h, minute: m));
  }

  Widget _digitBox({
    required TextEditingController controller,
    required FocusNode focusNode,
    required String semanticLabel,
    required MelbelPalette palette,
    required bool isDark,
    required VoidCallback onIncrement,
    required VoidCallback onDecrement,
    required void Function(double delta) onDragUpdate,
    required VoidCallback onDragReset,
    required ValueChanged<String> onChanged,
    TextInputAction textInputAction = TextInputAction.next,
    VoidCallback? onSubmitted,
  }) {
    final focused = focusNode.hasFocus;
    final highContrast = MediaQuery.highContrastOf(context);
    return Semantics(
      textField: true,
      label: semanticLabel,
      child: GestureDetector(
        onVerticalDragStart: (_) => onDragReset(),
        onVerticalDragUpdate: (details) => onDragUpdate(details.delta.dy),
        onVerticalDragEnd: (_) => onDragReset(),
        onVerticalDragCancel: onDragReset,
        child: CallbackShortcuts(
          bindings: {
            const SingleActivator(LogicalKeyboardKey.arrowUp): onIncrement,
            const SingleActivator(LogicalKeyboardKey.arrowDown): onDecrement,
          },
          child: AnimatedContainer(
            duration: MediaQuery.disableAnimationsOf(context)
                ? Duration.zero
                : const Duration(milliseconds: 140),
            width: 74,
            height: 58,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: focused ? palette.selectedSurface : palette.sageSoft,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: focused
                    ? palette.sage
                    : highContrast
                        ? palette.controlOutline
                        : isDark
                            ? palette.hairline
                            : palette.sage.withValues(alpha: 0.22),
                width: focused ? 2.0 : 1.2,
              ),
            ),
            child: TextField(
              controller: controller,
              focusNode: focusNode,
              keyboardType: TextInputType.number,
              textAlign: TextAlign.center,
              maxLines: 1,
              textInputAction: textInputAction,
              inputFormatters: [
                FilteringTextInputFormatter.digitsOnly,
                LengthLimitingTextInputFormatter(2),
              ],
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 30,
                fontWeight: FontWeight.w800,
                letterSpacing: -0.5,
                color: palette.onSurface,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
              decoration: const InputDecoration(
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                errorBorder: InputBorder.none,
                isDense: true,
                filled: false,
                contentPadding: EdgeInsets.zero,
              ),
              onChanged: onChanged,
              onSubmitted: (_) => onSubmitted?.call(),
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final textTheme = theme.textTheme;
    final isDark = theme.brightness == Brightness.dark;
    final highContrast = MediaQuery.highContrastOf(context);
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final screenPadding = MediaQuery.viewInsetsOf(context);

    return SafeArea(
      child: Center(
        child: Padding(
          padding: EdgeInsets.fromLTRB(
            16,
            16,
            16,
            16 + screenPadding.bottom,
          ),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 356),
            child: SingleChildScrollView(
              child: Material(
                color: palette.surface,
                elevation: isDark ? 0 : 10,
                shadowColor: const Color(0xFF534070).withValues(alpha: 0.16),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(32),
                  side: highContrast
                      ? BorderSide(color: palette.controlOutline, width: 1.5)
                      : isDark
                          ? BorderSide(color: palette.hairline, width: 1.0)
                          : BorderSide.none,
                ),
                clipBehavior: Clip.antiAlias,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Row(
                        children: [
                          Container(
                            width: 42,
                            height: 42,
                            decoration: BoxDecoration(
                              color: palette.sageSoft,
                              borderRadius: BorderRadius.circular(14),
                            ),
                            alignment: Alignment.center,
                            child: Icon(
                              Icons.alarm_rounded,
                              size: 22,
                              color: palette.accentInk,
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Flexible(
                                  child: Text(
                                    widget.helpText ?? 'Giờ bắt đầu',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: textTheme.titleLarge?.copyWith(
                                      fontSize: 20,
                                      fontWeight: FontWeight.w800,
                                      letterSpacing: -0.3,
                                      color: palette.onSurface,
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 4),
                                Transform.translate(
                                  offset: const Offset(0, -6),
                                  child: Transform.rotate(
                                    angle: 0.24,
                                    child: MelbelDoodleRays(
                                      size: 15,
                                      color: palette.accentInk,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                          IconButton(
                            tooltip: 'Đóng',
                            constraints: const BoxConstraints(
                              minWidth: 48,
                              minHeight: 48,
                            ),
                            padding: EdgeInsets.zero,
                            onPressed: () => Navigator.of(context).pop(),
                            icon: Container(
                              width: 40,
                              height: 40,
                              decoration: BoxDecoration(
                                color: palette.sageSoft,
                                shape: BoxShape.circle,
                              ),
                              alignment: Alignment.center,
                              child: Icon(
                                Icons.close_rounded,
                                size: 20,
                                color: palette.onSurface,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Center(
                        child: FittedBox(
                          fit: BoxFit.scaleDown,
                          child: SizedBox(
                            width: 268,
                            height: 196,
                            child: Stack(
                              clipBehavior: Clip.none,
                              alignment: Alignment.bottomCenter,
                              children: [
                                Positioned(
                                  left: 0,
                                  right: 0,
                                  bottom: 0,
                                  height: 146,
                                  child: IgnorePointer(
                                    child: RepaintBoundary(
                                      child: CustomPaint(
                                        painter: _MelbelAlarmClockPainter(
                                          isDark: isDark,
                                          palette: palette,
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                                Positioned(
                                  top: 0,
                                  child: MelbelMascot(
                                    width: 96,
                                    height: 78,
                                    expression: _isValid
                                        ? CatExpression.neutral
                                        : CatExpression.sleepy,
                                  ),
                                ),
                                Positioned(
                                  top: 8,
                                  right: 38,
                                  child: IgnorePointer(
                                    child: Transform.rotate(
                                      angle: 0.34,
                                      child: MelbelDoodleRays(
                                        size: 20,
                                        color: palette.amber,
                                      ),
                                    ),
                                  ),
                                ),
                                Positioned(
                                  bottom: 120,
                                  width: 112,
                                  height: 24,
                                  child: IgnorePointer(
                                    child: CustomPaint(
                                      painter: _MelbelClockPawsPainter(
                                        isDark: isDark,
                                      ),
                                    ),
                                  ),
                                ),
                                Positioned(
                                  bottom: 36,
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      _digitBox(
                                        controller: _hourController,
                                        focusNode: _hourFocus,
                                        semanticLabel: 'Giờ (0 đến 23)',
                                        palette: palette,
                                        isDark: isDark,
                                        onIncrement: () => _nudgeHour(1),
                                        onDecrement: () => _nudgeHour(-1),
                                        onDragReset: () => _hourDragDelta = 0,
                                        onDragUpdate: (dy) {
                                          _hourDragDelta += dy;
                                          if (_hourDragDelta <= -16) {
                                            _hourDragDelta = 0;
                                            _nudgeHour(1);
                                          } else if (_hourDragDelta >= 16) {
                                            _hourDragDelta = 0;
                                            _nudgeHour(-1);
                                          }
                                        },
                                        onChanged: (value) {
                                          setState(() {});
                                          final h = int.tryParse(value);
                                          if (value.length == 2 &&
                                              h != null &&
                                              h <= 23) {
                                            _minuteFocus.requestFocus();
                                          }
                                        },
                                      ),
                                      Padding(
                                        padding: const EdgeInsets.symmetric(
                                          horizontal: 10,
                                        ),
                                        child: Text(
                                          ':',
                                          style: TextStyle(
                                            fontFamily: 'Be Vietnam Pro',
                                            fontSize: 32,
                                            fontWeight: FontWeight.w800,
                                            color: palette.onSurface,
                                          ),
                                        ),
                                      ),
                                      _digitBox(
                                        controller: _minuteController,
                                        focusNode: _minuteFocus,
                                        semanticLabel: 'Phút (0 đến 59)',
                                        palette: palette,
                                        isDark: isDark,
                                        textInputAction: TextInputAction.done,
                                        onIncrement: () => _nudgeMinute(5),
                                        onDecrement: () => _nudgeMinute(-5),
                                        onDragReset: () => _minuteDragDelta = 0,
                                        onDragUpdate: (dy) {
                                          _minuteDragDelta += dy;
                                          if (_minuteDragDelta <= -16) {
                                            _minuteDragDelta = 0;
                                            _nudgeMinute(5);
                                          } else if (_minuteDragDelta >= 16) {
                                            _minuteDragDelta = 0;
                                            _nudgeMinute(-5);
                                          }
                                        },
                                        onChanged: (_) => setState(() {}),
                                        onSubmitted: () {
                                          if (_isValid) _submit();
                                        },
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(height: 10),
                      SizedBox(
                        height: 26,
                        child: AnimatedSwitcher(
                          duration: reducedMotion
                              ? Duration.zero
                              : const Duration(milliseconds: 160),
                          child: !_isValid
                              ? Row(
                                  key: const ValueKey('time_err'),
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    Icon(
                                      Icons.schedule_outlined,
                                      size: 18,
                                      color: palette.errorInk,
                                    ),
                                    const SizedBox(width: 6),
                                    Flexible(
                                      child: Text(
                                        'Nhập thời gian hợp lệ',
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                        style: textTheme.bodySmall?.copyWith(
                                          color: palette.errorInk,
                                          fontWeight: FontWeight.w600,
                                        ),
                                      ),
                                    ),
                                  ],
                                )
                              : Center(
                                  key: const ValueKey('time_hint'),
                                  child: Text(
                                    'Chạm số để nhập · vuốt lên/xuống để chỉnh',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: textTheme.labelSmall?.copyWith(
                                      color: palette.onSurfaceVariant,
                                    ),
                                  ),
                                ),
                        ),
                      ),
                      const SizedBox(height: 14),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.end,
                        children: [
                          TextButton(
                            onPressed: () => Navigator.of(context).pop(),
                            child: const Text('Hủy'),
                          ),
                          const SizedBox(width: 8),
                          _MelbelPressScale(
                            enabled: _isValid,
                            child: ConstrainedBox(
                              constraints: const BoxConstraints(
                                minWidth: 104,
                                minHeight: 48,
                              ),
                              child: ElevatedButton(
                                style: ElevatedButton.styleFrom(
                                  minimumSize: const Size(104, 48),
                                  backgroundColor: palette.sage,
                                  foregroundColor: palette.onSage,
                                  disabledBackgroundColor:
                                      palette.selectedSurface,
                                  disabledForegroundColor:
                                      palette.onSurfaceVariant,
                                  shape: const StadiumBorder(),
                                  elevation: 0,
                                  shadowColor: Colors.transparent,
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 24,
                                    vertical: 12,
                                  ),
                                ),
                                onPressed: _isValid ? _submit : null,
                                child: Text(widget.confirmText ?? 'OK'),
                              ),
                            ),
                          ),
                        ],
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

class _MelbelAlarmClockPainter extends CustomPainter {
  const _MelbelAlarmClockPainter({
    required this.isDark,
    required this.palette,
  });

  final bool isDark;
  final MelbelPalette palette;

  @override
  void paint(Canvas canvas, Size size) {
    const bodyRect = Rect.fromLTWH(20, 14, 224, 114);
    final borderColor =
        isDark ? const Color(0xFF9F8AD6) : palette.sage;
    final chassisFill =
        isDark ? const Color(0xFF332B45) : const Color(0xFFFFFBF4);
    final accentFill =
        isDark ? const Color(0xFF4A3C66) : const Color(0xFFD8C6FA);
    final screenBezel =
        isDark ? const Color(0xFF282138) : const Color(0xFFF3ECFC);

    final shadowPaint = Paint()
      ..color = isDark
          ? Colors.black.withValues(alpha: 0.35)
          : const Color(0xFFE5DBF8);
    canvas.drawOval(
      Rect.fromCenter(
        center: Offset(size.width / 2, bodyRect.bottom + 8),
        width: 228,
        height: 18,
      ),
      shadowPaint,
    );

    if (isDark) {
      final moonPaint = Paint()..color = const Color(0xFFA797C4);
      canvas.drawCircle(Offset(size.width / 2, 0), 34, moonPaint);
    }

    // Left sage leaf sprig peeking beside the clock
    final darkLeaf = Paint()
      ..color = isDark ? const Color(0xFF7D9368) : const Color(0xFF9EB386);
    final lightLeaf = Paint()
      ..color = isDark ? const Color(0xFF9BAF85) : const Color(0xFFB7C8A0);
    void drawLeaf(Offset base, double angle, double len, double hw, Paint p) {
      canvas.save();
      canvas.translate(base.dx, base.dy);
      canvas.rotate(angle);
      final path = Path()
        ..moveTo(0, 0)
        ..quadraticBezierTo(-hw, -len * 0.48, 0, -len)
        ..quadraticBezierTo(hw, -len * 0.48, 0, 0)
        ..close();
      canvas.drawPath(path, p);
      canvas.restore();
    }

    drawLeaf(Offset(28, bodyRect.bottom - 12), -0.78, 34, 8, lightLeaf);
    drawLeaf(Offset(30, bodyRect.bottom - 2), -1.18, 28, 7, darkLeaf);

    final knobFill = Paint()..color = accentFill;
    final outline = Paint()
      ..color = borderColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.4
      ..strokeJoin = StrokeJoin.round;

    // Two top snooze buttons
    for (final x in [bodyRect.left + 32, bodyRect.right - 66]) {
      final btn = RRect.fromRectAndCorners(
        Rect.fromLTWH(x, bodyRect.top - 9, 34, 12),
        topLeft: const Radius.circular(6),
        topRight: const Radius.circular(6),
      );
      canvas.drawRRect(btn, knobFill);
      canvas.drawRRect(btn, outline);
    }

    // Right side crown button
    final sideKnob = RRect.fromRectAndCorners(
      Rect.fromLTWH(bodyRect.right - 2, bodyRect.center.dy - 14, 11, 28),
      topRight: const Radius.circular(8),
      bottomRight: const Radius.circular(8),
    );
    canvas.drawRRect(sideKnob, knobFill);
    canvas.drawRRect(sideKnob, outline);

    // Two bottom clock feet
    for (final x in [bodyRect.left + 34, bodyRect.right - 56]) {
      final foot = RRect.fromRectAndCorners(
        Rect.fromLTWH(x, bodyRect.bottom - 2, 22, 10),
        bottomLeft: const Radius.circular(6),
        bottomRight: const Radius.circular(6),
      );
      canvas.drawRRect(foot, knobFill);
      canvas.drawRRect(foot, outline);
    }

    // Main alarm clock chassis
    final outerRRect = RRect.fromRectAndRadius(
      bodyRect,
      const Radius.circular(32),
    );
    canvas.drawRRect(outerRRect, Paint()..color = chassisFill);
    canvas.drawRRect(
      outerRRect,
      Paint()
        ..color = borderColor
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.6,
    );

    // Inner digital screen bezel
    final innerRRect = RRect.fromRectAndRadius(
      bodyRect.deflate(12),
      const Radius.circular(22),
    );
    canvas.drawRRect(innerRRect, Paint()..color = screenBezel);

    // Subtle top-left rim sheen
    final sheen = Paint()
      ..color = Colors.white.withValues(alpha: isDark ? 0.12 : 0.72)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.4
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(
      Rect.fromLTWH(bodyRect.left + 6, bodyRect.top + 6, 36, 28),
      math.pi,
      math.pi * 0.45,
      false,
      sheen,
    );

    // Tiny decorative cat paw print at bottom-right of clock bezel
    final pawPaint = Paint()
      ..color = borderColor.withValues(alpha: isDark ? 0.55 : 0.45);
    final pawCenter = Offset(bodyRect.right - 16, bodyRect.bottom - 14);
    canvas.drawOval(
      Rect.fromCenter(center: pawCenter, width: 8, height: 6.5),
      pawPaint,
    );
    for (final toe in const [
      Offset(-4.5, -4.5),
      Offset(0, -6.0),
      Offset(4.5, -4.5),
    ]) {
      canvas.drawCircle(pawCenter + toe, 1.7, pawPaint);
    }
  }

  @override
  bool shouldRepaint(_MelbelAlarmClockPainter oldDelegate) =>
      oldDelegate.isDark != isDark || oldDelegate.palette != palette;
}

class _MelbelClockPawsPainter extends CustomPainter {
  const _MelbelClockPawsPainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final shadowPaint = Paint()
      ..color = Colors.black.withValues(alpha: isDark ? 0.32 : 0.14);
    final pawFill = Paint()..color = const Color(0xFF18181B);
    final stroke = Paint()
      ..color = isDark ? const Color(0xFF4F465E) : const Color(0xFF3A3544)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.1;

    for (final cx in [size.width * 0.25, size.width * 0.75]) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromCenter(center: Offset(cx, 14), width: 20, height: 15),
          const Radius.circular(7.5),
        ),
        shadowPaint,
      );
      final paw = RRect.fromRectAndRadius(
        Rect.fromCenter(center: Offset(cx, 11.5), width: 19, height: 14),
        const Radius.circular(7),
      );
      canvas.drawRRect(paw, pawFill);
      canvas.drawRRect(paw, stroke);
    }
  }

  @override
  bool shouldRepaint(_MelbelClockPawsPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}

