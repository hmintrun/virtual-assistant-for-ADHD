import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/theme_provider.dart';

class MelbelAppBar extends StatelessWidget implements PreferredSizeWidget {
  const MelbelAppBar({
    required this.title,
    this.showBack = false,
    this.showGlobalActions = true,
    this.leadingIcon,
    this.showThemeToggle = false,
    this.onBack,
    super.key,
  });

  final String title;
  final bool showBack;
  final bool showGlobalActions;
  final IconData? leadingIcon;
  final bool showThemeToggle;
  final VoidCallback? onBack;

  @override
  Size get preferredSize => const Size.fromHeight(56);

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final isDark = colors.brightness == Brightness.dark;
    return Container(
      height: 56 + MediaQuery.paddingOf(context).top,
      padding: EdgeInsets.only(
        top: MediaQuery.paddingOf(context).top,
        left: 16,
        right: 16,
      ),
      color: colors.surface.withValues(alpha: 0.85),
      child: Row(
        children: [
          if (showBack)
            IconButton(
              tooltip: 'Quay lại',
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(minWidth: 40, minHeight: 40),
              onPressed: onBack ?? () => Navigator.maybePop(context),
              icon: Icon(
                Icons.arrow_back,
                color: MelbelColors.kOnSurfaceVariant,
                size: 22,
              ),
            )
          else if (showThemeToggle)
            IconButton(
              tooltip: isDark ? 'Bật chế độ sáng' : 'Bật chế độ tối',
              onPressed: () => context.read<ThemeProvider>().setAppearance(
                    isDark ? 'light' : 'dark',
                  ),
              icon: Icon(isDark
                  ? Icons.dark_mode_outlined
                  : Icons.light_mode_outlined),
            )
          else ...[
            Icon(
              leadingIcon ?? Icons.dark_mode_outlined,
              color: MelbelColors.kPrimaryContainer,
              size: 22,
            ),
            const SizedBox(width: 10),
          ],
          Expanded(
            child: Text(
              title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 20,
                fontWeight: FontWeight.w700,
                color: MelbelColors.kOnSurface,
                letterSpacing: -0.3,
              ),
            ),
          ),
          if (showGlobalActions) ...[
            IconButton(
              tooltip: 'Trợ giúp & Hướng dẫn',
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(minWidth: 40, minHeight: 40),
              onPressed: () => Navigator.of(context).pushNamed('/help'),
              icon: Icon(
                Icons.help_outline,
                color: MelbelColors.kOnSurfaceVariant,
                size: 20,
              ),
            ),
            const SizedBox(width: 4),
            IconButton(
              tooltip: 'Tài khoản',
              constraints: const BoxConstraints.tightFor(width: 48, height: 48),
              onPressed: () => Navigator.of(context).pushNamed('/account'),
              icon: CircleAvatar(
                radius: 16,
                backgroundColor: MelbelColors.kPrimary,
                child: CatAccountIcon(
                  size: 22,
                  color: MelbelColors.kOnPrimary,
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
    backgroundColor: MelbelColors.kSurfaceContainer,
    builder: (context) => SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 8, 24, 28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: MelbelColors.kPrimaryContainer, size: 28),
            const SizedBox(height: 12),
            Text(
              title,
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 20,
                fontWeight: FontWeight.w600,
                color: MelbelColors.kOnSurface,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              message,
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 14,
                color: MelbelColors.kOnSurfaceVariant,
                height: 1.5,
              ),
            ),
            const SizedBox(height: 20),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(
                  backgroundColor: MelbelColors.kPrimaryContainer,
                  foregroundColor: MelbelColors.kOnPrimary,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
                onPressed: () => Navigator.pop(context),
                child: const Text('Đóng'),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

enum CatExpression { neutral, happy, sleepy }

class MelbelMascot extends StatelessWidget {
  const MelbelMascot({
    this.expression = CatExpression.neutral,
    super.key,
  });

  final CatExpression expression;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final mode = dark ? 'dark' : 'light';
    return SvgPicture.asset(
      'assets/melbel_svg/cat_${mode}_${expression.name}.svg',
      width: 120,
      height: 100,
      fit: BoxFit.contain,
      excludeFromSemantics: true,
    );
  }
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
  const MelbelEntrance({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    if (MediaQuery.disableAnimationsOf(context)) return child;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: 1),
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOutCubic,
      child: child,
      builder: (context, progress, child) => Transform.translate(
        offset: Offset(0, 6 * (1 - progress)),
        child: Opacity(opacity: progress, child: child),
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
    final colors = theme.colorScheme;
    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Bước $step/3 · $label', style: theme.textTheme.labelLarge),
          const SizedBox(height: 8),
          Row(
            spacing: 6,
            children: [
              for (var index = 1; index <= 3; index++)
                Expanded(
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      color: index <= step
                          ? colors.primary
                          : colors.outlineVariant,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: const SizedBox(height: 4),
                  ),
                ),
            ],
          ),
        ],
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
    this.borderWidth = 1.25,
    this.borderRadius = 16.0,
    super.key,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? color;
  final Color? borderColor;
  final double borderWidth;
  final double borderRadius;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return SizedBox(
      width: double.infinity,
      child: Material(
        color: color ?? colors.surfaceContainer,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(borderRadius),
          side: BorderSide(
            color: borderColor ?? colors.outlineVariant,
            width: borderWidth,
          ),
        ),
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}

class PrimaryCta extends StatelessWidget {
  const PrimaryCta({
    required this.label,
    required this.onPressed,
    this.icon,
    this.height = 56.0,
    super.key,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final double height;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      height: height,
      child: ElevatedButton(
        style: ElevatedButton.styleFrom(
          backgroundColor: MelbelColors.kPrimaryContainer,
          foregroundColor: MelbelColors.kOnPrimary,
          disabledBackgroundColor: MelbelColors.kSelectedSurface,
          disabledForegroundColor: MelbelColors.kOnSurfaceVariant,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          elevation: 0,
        ),
        onPressed: onPressed,
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            if (icon != null) ...[
              Icon(icon, size: 20),
              const SizedBox(width: 8),
            ],
            Text(
              label,
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 16,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
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
    this.height = 48.0,
    super.key,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final double height;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      height: height,
      child: OutlinedButton(
        style: OutlinedButton.styleFrom(
          backgroundColor: MelbelColors.kSurfaceContainer,
          foregroundColor: MelbelColors.kOnSurface,
          side: BorderSide(color: MelbelColors.kControlOutline),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
        ),
        onPressed: onPressed,
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            if (icon != null) ...[
              Icon(icon, size: 18, color: MelbelColors.kOnSurfaceVariant),
              const SizedBox(width: 8),
            ],
            Text(
              label,
              style: TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 14,
                fontWeight: FontWeight.w500,
                color: MelbelColors.kOnSurface,
              ),
            ),
          ],
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
    return Text(
      text.toUpperCase(),
      style: TextStyle(
        fontFamily: 'Be Vietnam Pro',
        fontSize: 11,
        fontWeight: FontWeight.w600,
        letterSpacing: 1.0,
        color: color ?? MelbelColors.kOnSurfaceVariant,
      ),
    );
  }
}
