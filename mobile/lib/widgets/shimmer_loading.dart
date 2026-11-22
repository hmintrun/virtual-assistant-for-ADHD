import 'package:flutter/material.dart';
import 'package:shimmer/shimmer.dart';

import '../core/theme.dart';

/// Calm, GPU-friendly skeleton loader replacing harsh spinning indicators.
///
/// Automatically falls back to static low-contrast placeholders when the user
/// enables reduced motion (`MediaQuery.disableAnimationsOf`).
class MelbelLoadingSkeleton extends StatelessWidget {
  const MelbelLoadingSkeleton({
    this.cardCount = 2,
    this.caption,
    super.key,
  });

  final int cardCount;
  final String? caption;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final skeleton = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < cardCount; i++) ...[
          _SkeletonCard(palette: palette, compact: i > 0),
          if (i < cardCount - 1) const SizedBox(height: 12),
        ],
      ],
    );

    return Semantics(
      label: caption ?? 'Đang tải nội dung',
      liveRegion: true,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (reducedMotion)
            skeleton
          else
            RepaintBoundary(
              child: Shimmer.fromColors(
                baseColor: palette.mutedSurface,
                highlightColor: palette.selectedSurface,
                period: const Duration(milliseconds: 1600),
                child: skeleton,
              ),
            ),
          if (caption != null) ...[
            const SizedBox(height: 12),
            Text(
              caption!,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: palette.onSurfaceVariant,
                  ),
            ),
          ],
        ],
      ),
    );
  }
}

class _SkeletonCard extends StatelessWidget {
  const _SkeletonCard({required this.palette, required this.compact});

  final MelbelPalette palette;
  final bool compact;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: palette.mutedSurface,
          borderRadius: BorderRadius.circular(18),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _SkeletonBar(width: 96, height: 12, color: palette.selectedSurface),
            const SizedBox(height: 12),
            _SkeletonBar(
              width: double.infinity,
              height: 18,
              color: palette.selectedSurface,
            ),
            const SizedBox(height: 8),
            _SkeletonBar(
              width: compact ? 160 : 220,
              height: 14,
              color: palette.selectedSurface,
            ),
            if (!compact) ...[
              const SizedBox(height: 18),
              _SkeletonBar(
                width: double.infinity,
                height: 48,
                radius: 14,
                color: palette.selectedSurface,
              ),
            ],
          ],
        ),
      );
}

class _SkeletonBar extends StatelessWidget {
  const _SkeletonBar({
    required this.width,
    required this.height,
    required this.color,
    this.radius = 8,
  });

  final double width;
  final double height;
  final Color color;
  final double radius;

  @override
  Widget build(BuildContext context) => Container(
        width: width,
        height: height,
        decoration: BoxDecoration(
          color: color,
          borderRadius: BorderRadius.circular(radius),
        ),
      );
}

/// Soothing breathing ring indicator for AI analysis / task breakdown.
class MelbelBreathingRing extends StatefulWidget {
  const MelbelBreathingRing({
    this.size = 76,
    this.icon = Icons.auto_awesome_rounded,
    super.key,
  });

  final double size;
  final IconData icon;

  @override
  State<MelbelBreathingRing> createState() => _MelbelBreathingRingState();
}

class _MelbelBreathingRingState extends State<MelbelBreathingRing>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2200),
  );
  bool _reducedMotion = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final reduced = MediaQuery.disableAnimationsOf(context);
    if (reduced == _reducedMotion && (_controller.isAnimating || reduced)) {
      return;
    }
    _reducedMotion = reduced;
    if (reduced) {
      _controller.stop();
      _controller.value = 0.5;
    } else {
      _controller.repeat(reverse: true);
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
    return RepaintBoundary(
      child: SizedBox(
        width: widget.size,
        height: widget.size,
        child: AnimatedBuilder(
          animation: _controller,
          builder: (context, child) {
            final t = Curves.easeInOutSine.transform(_controller.value);
            return Stack(
              alignment: Alignment.center,
              children: [
                Transform.scale(
                  scale: 0.88 + 0.14 * t,
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: palette.sageSoft.withValues(alpha: 0.45 + 0.3 * t),
                    ),
                    child: SizedBox.square(dimension: widget.size),
                  ),
                ),
                Transform.scale(
                  scale: 0.94 + 0.06 * t,
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: palette.sageSoft,
                      border: Border.all(
                        color: palette.sage.withValues(alpha: 0.35),
                        width: 1.5,
                      ),
                    ),
                    child: SizedBox.square(dimension: widget.size * 0.68),
                  ),
                ),
                child!,
              ],
            );
          },
          child: Icon(
            widget.icon,
            size: widget.size * 0.34,
            color: palette.sage,
          ),
        ),
      ),
    );
  }
}
