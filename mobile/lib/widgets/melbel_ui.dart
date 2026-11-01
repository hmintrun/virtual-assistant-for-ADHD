import 'package:flutter/material.dart';

import '../core/theme.dart';

class MelbelAppBar extends StatelessWidget implements PreferredSizeWidget {
  const MelbelAppBar({
    required this.title,
    this.showBack = false,
    this.showGlobalActions = true,
    this.leadingIcon,
    this.onBack,
    super.key,
  });

  final String title;
  final bool showBack;
  final bool showGlobalActions;
  final IconData? leadingIcon;
  final VoidCallback? onBack;

  @override
  Size get preferredSize => const Size.fromHeight(56);

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 56 + MediaQuery.paddingOf(context).top,
      padding: EdgeInsets.only(
        top: MediaQuery.paddingOf(context).top,
        left: 16,
        right: 16,
      ),
      color: MelbelColors.kSurface.withValues(alpha: 0.85),
      child: Row(
        children: [
          if (showBack)
            IconButton(
              tooltip: 'Quay lại',
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(minWidth: 40, minHeight: 40),
              onPressed: onBack ?? () => Navigator.maybePop(context),
              icon: const Icon(
                Icons.arrow_back,
                color: MelbelColors.kOnSurfaceVariant,
                size: 22,
              ),
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
              style: const TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 20,
                fontWeight: FontWeight.w500,
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
              onPressed: () => showMelbelInfo(
                context,
                title: 'Trợ giúp & Ý nghĩa',
                message:
                    'Không gian êm dịu giúp bạn hoàn thành công việc theo từng nhịp nhỏ, không áp lực, không phán xét.',
                icon: Icons.help_outline,
              ),
              icon: const Icon(
                Icons.help_outline,
                color: MelbelColors.kOnSurfaceVariant,
                size: 20,
              ),
            ),
            const SizedBox(width: 4),
            InkWell(
              borderRadius: BorderRadius.circular(16),
              onTap: () => showMelbelInfo(
                context,
                title: 'Không gian cá nhân',
                message: 'Hồ sơ người dùng và các thiết lập trợ lý cá nhân.',
                icon: Icons.person_outline,
              ),
              child: Container(
                width: 32,
                height: 32,
                decoration: const BoxDecoration(
                  color: MelbelColors.kPrimary,
                  shape: BoxShape.circle,
                ),
                child: const Icon(
                  Icons.person,
                  size: 18,
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
              style: const TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 20,
                fontWeight: FontWeight.w600,
                color: MelbelColors.kOnSurface,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              message,
              style: const TextStyle(
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

class MelbelCard extends StatelessWidget {
  const MelbelCard({
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.color,
    this.borderColor,
    this.borderWidth = 1.0,
    this.borderRadius = 12.0,
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
    return Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        color: color ?? MelbelColors.kSurfaceContainer,
        borderRadius: BorderRadius.circular(borderRadius),
        border: Border.all(
          color: borderColor ?? MelbelColors.kOutlineVariant,
          width: borderWidth,
        ),
      ),
      child: child,
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
              Icon(icon, size: 20, color: MelbelColors.kOnPrimary),
              const SizedBox(width: 8),
            ],
            Text(
              label,
              style: const TextStyle(
                fontFamily: 'Be Vietnam Pro',
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: MelbelColors.kOnPrimary,
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
          side: const BorderSide(color: MelbelColors.kOutlineVariant),
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
              style: const TextStyle(
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
