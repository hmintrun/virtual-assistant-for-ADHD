import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class MelbelColors {
  static bool isDark = false;
  static Color _pick(int day, int night) => Color(isDark ? night : day);

  static Color get kBackground => _pick(0xFFF7F7F8, 0xFF18181B);
  static Color get kSurface => _pick(0xFFFFFFFF, 0xFF242428);
  static Color get kSurfaceDim => kBackground;
  static Color get kSurfaceBright => kSurfaceContainerHigh;
  static Color get kSurfaceContainerLowest => kBackground;
  static Color get kSurfaceContainerLow => kMutedSurface;
  static Color get kSurfaceContainer => kSurface;
  static Color get kSurfaceContainerHigh => kMutedSurface;
  static Color get kSurfaceContainerHighest => kSelectedSurface;
  static Color get kOnSurface => _pick(0xFF18181B, 0xFFFAFAFA);
  static Color get kOnSurfaceVariant => _pick(0xFF52525B, 0xFFD4D4D8);
  static Color get kTextPrimary => kOnSurface;
  static Color get kTextSecondary => kOnSurfaceVariant;
  static Color get kPrimary => kOnSurface;
  static Color get kOnPrimary => _pick(0xFFFFFFFF, 0xFF18181B);
  static Color get kPrimaryContainer => kPrimary;
  static Color get kOnPrimaryContainer => kOnPrimary;
  static Color get kSecondary => kOnSurfaceVariant;
  static Color get kOnSecondary => kOnPrimary;
  static Color get kSecondaryContainer => kMutedSurface;
  static Color get kOnSecondaryContainer => kOnSurface;
  static Color get kSage => kOnSurfaceVariant;
  static Color get kTertiary => kOnSurfaceVariant;
  static Color get kOnTertiary => kOnPrimary;
  static Color get kTertiaryContainer => kMutedSurface;
  static Color get kOnTertiaryContainer => kOnSurface;
  static Color get kTertiaryFixedDim => kOnSurfaceVariant;
  static Color get kAmber => kTertiary;
  static Color get kOutline => _pick(0xFFA1A1AA, 0xFF71717A);
  static Color get kOutlineVariant => kOutline;
  static Color get kMutedSurface => _pick(0xFFF0F0F2, 0xFF2C2C31);
  static Color get kSelectedSurface => _pick(0xFFE4E4E7, 0xFF38383F);
  static Color get kControlOutline => _pick(0xFF71717A, 0xFFA1A1AA);
  static Color get kShadow => const Color(0xFF000000);
  static Color get kError => kOnSurface;
  static Color get kOnError => kOnPrimary;
}

class MelbelTheme {
  static ThemeData get currentTheme {
    final dark = MelbelColors.isDark;
    final brightness = dark ? Brightness.dark : Brightness.light;
    final textTheme = GoogleFonts.beVietnamProTextTheme(
      dark ? ThemeData.dark().textTheme : ThemeData.light().textTheme,
    ).apply(
      bodyColor: MelbelColors.kOnSurface,
      displayColor: MelbelColors.kOnSurface,
    );
    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      scaffoldBackgroundColor: MelbelColors.kBackground,
      focusColor: MelbelColors.kOnSurface.withValues(alpha: 0.22),
      disabledColor: MelbelColors.kControlOutline,
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: MelbelColors.kPrimary,
        linearTrackColor: MelbelColors.kSelectedSurface,
      ),
      inputDecorationTheme: InputDecorationTheme(
        focusedBorder: OutlineInputBorder(
          borderSide: BorderSide(color: MelbelColors.kPrimary, width: 2),
        ),
      ),
      canvasColor: MelbelColors.kSurface,
      colorScheme: ColorScheme(
        brightness: brightness,
        surface: MelbelColors.kSurface,
        onSurface: MelbelColors.kOnSurface,
        primary: MelbelColors.kPrimary,
        onPrimary: MelbelColors.kOnPrimary,
        secondary: MelbelColors.kSecondary,
        onSecondary: MelbelColors.kOnSecondary,
        tertiary: MelbelColors.kTertiary,
        onTertiary: MelbelColors.kOnTertiary,
        error: MelbelColors.kError,
        onError: MelbelColors.kOnError,
        surfaceContainer: MelbelColors.kSurfaceContainer,
        surfaceContainerHigh: MelbelColors.kSurfaceContainerHigh,
        surfaceContainerLow: MelbelColors.kSurfaceContainerLow,
        outline: MelbelColors.kControlOutline,
        outlineVariant: MelbelColors.kOutlineVariant,
        errorContainer: MelbelColors.kTertiaryContainer,
        onErrorContainer: MelbelColors.kOnTertiaryContainer,
        surfaceTint: Colors.transparent,
        inverseSurface: MelbelColors.kOnSurface,
        onInverseSurface: MelbelColors.kSurface,
        inversePrimary: MelbelColors.kPrimary,
      ),
      textTheme: textTheme.copyWith(
        bodyLarge: textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w500),
        bodyMedium: textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w500),
        bodySmall: textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w500),
        titleLarge: textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
        titleMedium: textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: MelbelColors.kSurface,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(
          color: MelbelColors.kOnSurface,
          fontSize: 20,
          fontWeight: FontWeight.w500,
          fontFamily: 'Be Vietnam Pro',
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: MelbelColors.kPrimary,
          foregroundColor: MelbelColors.kOnPrimary,
          minimumSize: const Size.fromHeight(56),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: GoogleFonts.beVietnamPro(
            fontWeight: FontWeight.w600,
            fontSize: 16,
          ),
        ),
      ),
    );
  }
}
