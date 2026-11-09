import 'package:flutter/material.dart';

/// Shared layout rules for every Melbel screen.
///
/// The app keeps a phone-sized reading column on wide displays while page
/// gutters shrink on compact devices. Screen content should use flexible
/// constraints instead of fixed pixel positions.
abstract final class MelbelLayout {
  static const double maxContentWidth = 430;

  static double pageGutter(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    if (width < 360) return 16;
    if (width < 600) return 20;
    return 24;
  }

  static EdgeInsets pageInsets(
    BuildContext context, {
    required double top,
    required double bottom,
  }) {
    final gutter = pageGutter(context);
    return EdgeInsets.fromLTRB(gutter, top, gutter, bottom);
  }
}
