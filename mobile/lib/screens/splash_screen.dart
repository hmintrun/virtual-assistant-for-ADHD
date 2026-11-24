import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../core/theme.dart';
import '../services/melbel_sound_service.dart';
import '../widgets/melbel_ui.dart';

/// Calm, 60 FPS animated launch screen showing the ProPlan VA logo,
/// Melbel greeting animation, and app name before entering the app.
///
/// Uses GPU-composited transforms and opacity inside [RepaintBoundary] layers,
/// honors reduced-motion preferences, and supports tap-to-skip once ready.
class MelbelSplashScreen extends StatefulWidget {
  const MelbelSplashScreen({
    required this.onComplete,
    this.duration = const Duration(milliseconds: 1650),
    this.canSkip = true,
    super.key,
  });

  final VoidCallback onComplete;
  final Duration duration;
  final bool canSkip;

  @override
  State<MelbelSplashScreen> createState() => _MelbelSplashScreenState();
}

class _MelbelSplashScreenState extends State<MelbelSplashScreen>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: widget.duration,
  );
  Timer? _reducedMotionTimer;
  bool _started = false;
  bool _completed = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    unawaited(
      MelbelSoundService.instance.playMeow(
        volume: 0.55,
        bypassThrottle: true,
      ),
    );
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    if (reducedMotion) {
      _controller.value = 1.0;
      _reducedMotionTimer = Timer(
        const Duration(milliseconds: 420),
        _finish,
      );
    } else {
      _controller.forward().whenComplete(_finish);
    }
  }

  void _finish() {
    if (!mounted || _completed) return;
    _completed = true;
    widget.onComplete();
  }

  @override
  void dispose() {
    _reducedMotionTimer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final dark = Theme.of(context).brightness == Brightness.dark;

    final logoScale = CurvedAnimation(
      parent: _controller,
      curve: const Interval(0.0, 0.42, curve: Curves.easeOutBack),
    );
    final logoOpacity = CurvedAnimation(
      parent: _controller,
      curve: const Interval(0.0, 0.28, curve: Curves.easeOut),
    );
    final haloRipple = CurvedAnimation(
      parent: _controller,
      curve: const Interval(0.08, 0.72, curve: Curves.easeOutCubic),
    );
    final titleReveal = CurvedAnimation(
      parent: _controller,
      curve: const Interval(0.24, 0.60, curve: Curves.easeOutCubic),
    );
    final subtitleReveal = CurvedAnimation(
      parent: _controller,
      curve: const Interval(0.42, 0.74, curve: Curves.easeOutCubic),
    );
    final sparkleBurst = CurvedAnimation(
      parent: _controller,
      curve: const Interval(0.36, 0.84, curve: Curves.easeOutCubic),
    );
    final barProgress = CurvedAnimation(
      parent: _controller,
      curve: const Interval(0.18, 0.96, curve: Curves.easeInOutCubic),
    );

    return MelbelPageBackdrop(
      showTopCloud: true,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        body: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.canSkip ? _finish : null,
        child: SafeArea(
          child: Semantics(
            label: 'Đang mở ứng dụng ProPlan VA cùng trợ lý Melbel',
            child: Center(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 28),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    RepaintBoundary(
                      child: SizedBox(
                        width: 196,
                        height: 196,
                        child: AnimatedBuilder(
                          animation: _controller,
                          builder: (context, _) {
                            return Stack(
                              alignment: Alignment.center,
                              children: [
                                // Outer gentle butter-gold ripple ring
                                Transform.scale(
                                  scale: 0.78 + 0.36 * haloRipple.value,
                                  child: Opacity(
                                    opacity: (1 - haloRipple.value) * 0.48,
                                    child: DecoratedBox(
                                      decoration: BoxDecoration(
                                        borderRadius: BorderRadius.circular(42),
                                        border: Border.all(
                                          color: palette.amber,
                                          width: 1.6,
                                        ),
                                      ),
                                      child: const SizedBox.square(
                                        dimension: 172,
                                      ),
                                    ),
                                  ),
                                ),
                                // Animated rounded-squircle brand logo emblem
                                Transform.translate(
                                  offset: Offset(
                                    0,
                                    12 * (1 - logoOpacity.value),
                                  ),
                                  child: Transform.scale(
                                    scale: 0.86 + 0.14 * logoScale.value,
                                    child: Opacity(
                                      opacity: logoOpacity.value,
                                      child: Container(
                                        width: 156,
                                        height: 156,
                                        decoration: BoxDecoration(
                                          color: const Color(0xFFFCF5EB),
                                          borderRadius:
                                              BorderRadius.circular(36),
                                          border: Border.all(
                                            color: dark
                                                ? palette.sage
                                                    .withValues(alpha: 0.45)
                                                : palette.hairline,
                                            width: 1.2,
                                          ),
                                          boxShadow: [
                                            BoxShadow(
                                              color: Colors.black.withValues(
                                                alpha: dark ? 0.28 : 0.08,
                                              ),
                                              blurRadius: 20,
                                              offset: const Offset(0, 8),
                                            ),
                                          ],
                                        ),
                                        child: ClipRRect(
                                          borderRadius:
                                              BorderRadius.circular(35),
                                          child: Image.asset(
                                            'assets/branding/app_logo.png',
                                            width: 156,
                                            height: 156,
                                            fit: BoxFit.cover,
                                            filterQuality: FilterQuality.medium,
                                            excludeFromSemantics: true,
                                          ),
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                                // Calm pastel sparkles
                                if (sparkleBurst.value > 0 &&
                                    sparkleBurst.value < 1)
                                  Positioned.fill(
                                    child: CustomPaint(
                                      painter: _SplashSparklePainter(
                                        progress: sparkleBurst.value,
                                        sage: palette.sage,
                                        amber: palette.amber,
                                      ),
                                    ),
                                  ),
                              ],
                            );
                          },
                        ),
                      ),
                    ),
                    const SizedBox(height: 18),
                    RepaintBoundary(
                      child: AnimatedBuilder(
                        animation: titleReveal,
                        builder: (context, child) => Transform.translate(
                          offset: Offset(0, 12 * (1 - titleReveal.value)),
                          child: Opacity(
                            opacity: titleReveal.value,
                            child: child,
                          ),
                        ),
                        child: Text(
                          'ProPlan VA',
                          textAlign: TextAlign.center,
                          style: textTheme.headlineMedium?.copyWith(
                            fontSize: 28,
                            fontWeight: FontWeight.w700,
                            letterSpacing: -0.5,
                            color: palette.onSurface,
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 8),
                    RepaintBoundary(
                      child: AnimatedBuilder(
                        animation: subtitleReveal,
                        builder: (context, child) => Transform.translate(
                          offset: Offset(0, 8 * (1 - subtitleReveal.value)),
                          child: Opacity(
                            opacity: subtitleReveal.value,
                            child: child,
                          ),
                        ),
                        child: const MelbelStatusPill(
                          label: 'Trợ lý Melbel · Đồng hành từng bước nhỏ',
                          icon: Icons.spa_outlined,
                          tone: MelbelCardTone.sage,
                        ),
                      ),
                    ),
                    const SizedBox(height: 28),
                    RepaintBoundary(
                      child: AnimatedBuilder(
                        animation: barProgress,
                        builder: (context, _) => ClipRRect(
                          borderRadius: BorderRadius.circular(999),
                          child: SizedBox(
                            width: 72,
                            height: 4,
                            child: Stack(
                              fit: StackFit.expand,
                              children: [
                                ColoredBox(color: palette.selectedSurface),
                                FractionallySizedBox(
                                  alignment: Alignment.centerLeft,
                                  widthFactor:
                                      barProgress.value.clamp(0.05, 1.0),
                                  child: ColoredBox(color: palette.sage),
                                ),
                              ],
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
        ),
      ),
    ),
    );
  }
}

class _SplashSparklePainter extends CustomPainter {
  const _SplashSparklePainter({
    required this.progress,
    required this.sage,
    required this.amber,
  });

  final double progress;
  final Color sage;
  final Color amber;

  @override
  void paint(Canvas canvas, Size size) {
    final alpha = math.sin(progress * math.pi).clamp(0.0, 1.0);
    final points = <(double x, double y, double lift, bool isAmber)>[
      (0.20, 0.26, 16, false),
      (0.80, 0.24, 18, true),
      (0.74, 0.74, 12, false),
    ];

    for (final p in points) {
      final center = Offset(
        size.width * p.$1,
        size.height * p.$2 - p.$3 * progress,
      );
      final r = 5.5 * (0.65 + 0.35 * alpha);
      final paint = Paint()
        ..color = (p.$4 ? amber : sage).withValues(alpha: alpha * 0.85)
        ..style = PaintingStyle.fill;
      final path = Path()
        ..moveTo(center.dx, center.dy - r)
        ..quadraticBezierTo(center.dx, center.dy, center.dx + r, center.dy)
        ..quadraticBezierTo(center.dx, center.dy, center.dx, center.dy + r)
        ..quadraticBezierTo(center.dx, center.dy, center.dx - r, center.dy)
        ..quadraticBezierTo(center.dx, center.dy, center.dx, center.dy - r)
        ..close();
      canvas.drawPath(path, paint);
    }
  }

  @override
  bool shouldRepaint(_SplashSparklePainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.sage != sage ||
      oldDelegate.amber != amber;
}
