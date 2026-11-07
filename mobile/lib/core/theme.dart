import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class MelbelColors {
  static bool isDark = false;
  static Color _pick(int day, int night) => Color(isDark ? night : day);

  static Color get kBackground => _pick(0xFFF4F1EA, 0xFF15161A);
  static Color get kSurface => _pick(0xFFF4F1EA, 0xFF1E2029);
  static Color get kSurfaceDim => _pick(0xFFF4F1EA, 0xFF15161A);
  static Color get kSurfaceBright => _pick(0xFFFFFFFF, 0xFF2B2D36);
  static Color get kSurfaceContainerLowest => _pick(0xFFFFFFFF, 0xFF1A1B20);
  static Color get kSurfaceContainerLow => _pick(0xFFFFFFFF, 0xFF20222A);
  static Color get kSurfaceContainer => _pick(0xFFFFFFFF, 0xFF252832);
  static Color get kSurfaceContainerHigh => _pick(0xFFF4F1EA, 0xFF2B2D36);
  static Color get kSurfaceContainerHighest => _pick(0xFFDDD8CB, 0xFF424653);
  static Color get kOnSurface => _pick(0xFF2B2D36, 0xFFF4F1EA);
  static Color get kOnSurfaceVariant => _pick(0xFF6B6F7C, 0xFFB7BAC4);
  static Color get kTextPrimary => kOnSurface;
  static Color get kTextSecondary => kOnSurfaceVariant;
  static Color get kPrimary => const Color(0xFFC9A94E);
  static Color get kOnPrimary => const Color(0xFF2B2D36);
  static Color get kPrimaryContainer => const Color(0xFFC9A94E);
  static Color get kOnPrimaryContainer => const Color(0xFF2B2D36);
  static Color get kSecondary => _pick(0xFF6B6F7C, 0xFFB7BAC4);
  static Color get kOnSecondary => const Color(0xFFFFFFFF);
  static Color get kSecondaryContainer => _pick(0xFFF4F1EA, 0xFF353945);
  static Color get kOnSecondaryContainer => kOnSurface;
  static Color get kSage => _pick(0xFF4F7A5E, 0xFF7CA58A);
  static Color get kTertiary => _pick(0xFFC98B3F, 0xFFE1AC69);
  static Color get kOnTertiary => const Color(0xFF2B2D36);
  static Color get kTertiaryContainer => _pick(0xFFF4F1EA, 0xFF353945);
  static Color get kOnTertiaryContainer => kOnSurface;
  static Color get kTertiaryFixedDim => kOnSurfaceVariant;
  static Color get kAmber => kTertiary;
  static Color get kOutline => _pick(0xFFDDD8CB, 0xFF424653);
  static Color get kOutlineVariant => kOutline;
  static Color get kError => kTertiary;
  static Color get kOnError => const Color(0xFF2B2D36);
}

class MelbelTheme {
  static ThemeData get currentTheme {
    final dark = MelbelColors.isDark;
    final brightness = dark ? Brightness.dark : Brightness.light;
    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      scaffoldBackgroundColor: MelbelColors.kSurface,
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
        outline: MelbelColors.kOutline,
        outlineVariant: MelbelColors.kOutlineVariant,
        errorContainer: MelbelColors.kTertiaryContainer,
        onErrorContainer: MelbelColors.kOnTertiaryContainer,
        surfaceTint: MelbelColors.kPrimary,
        inverseSurface: MelbelColors.kOnSurface,
        onInverseSurface: MelbelColors.kSurface,
        inversePrimary: MelbelColors.kPrimary,
      ),
      textTheme: GoogleFonts.beVietnamProTextTheme(
        dark ? ThemeData.dark().textTheme : ThemeData.light().textTheme,
      ).apply(
        bodyColor: MelbelColors.kOnSurface,
        displayColor: MelbelColors.kOnSurface,
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
