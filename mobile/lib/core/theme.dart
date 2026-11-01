import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class MelbelColors {
  // Core Surfaces
  static const kSurface = Color(0xFF10131D);
  static const kSurfaceDim = Color(0xFF10131D);
  static const kSurfaceBright = Color(0xFF363944);
  static const kSurfaceContainerLowest = Color(0xFF0B0E17);
  static const kSurfaceContainerLow = Color(0xFF181B25);
  static const kSurfaceContainer = Color(0xFF1C1F29);
  static const kSurfaceContainerHigh = Color(0xFF272A34);
  static const kSurfaceContainerHighest = Color(0xFF32343F);

  // Text / On-surface
  static const kOnSurface = Color(0xFFE0E2F0);
  static const kOnSurfaceVariant = Color(0xFFCDC6B7);
  static const kTextPrimary = Color(0xFFE0E2F0);
  static const kTextSecondary = Color(0xFF8B90A0);

  // Primary / Moon Accent
  static const kPrimary = Color(0xFFFFF4DD);
  static const kOnPrimary = Color(0xFF39300C);
  static const kPrimaryContainer = Color(0xFFE9D8A6);
  static const kOnPrimaryContainer = Color(0xFF6A5E36);

  // Secondary / Sage Green
  static const kSecondary = Color(0xFFB2CDBA);
  static const kOnSecondary = Color(0xFF1E3528);
  static const kSecondaryContainer = Color(0xFF364E3F);
  static const kOnSecondaryContainer = Color(0xFFA4BFAC);
  static const kSage = Color(0xFFB2CDBA);

  // Tertiary / Warm Amber (Deadline, gentle warnings)
  static const kTertiary = Color(0xFFFFF3EB);
  static const kOnTertiary = Color(0xFF472A00);
  static const kTertiaryContainer = Color(0xFFFFD19F);
  static const kOnTertiaryContainer = Color(0xFF7C5729);
  static const kTertiaryFixedDim = Color(0xFFEFBE86);
  static const kAmber = Color(0xFFEFBE86);

  // Outline / Borders
  static const kOutline = Color(0xFF969083);
  static const kOutlineVariant = Color(0xFF4B463B);

  // Error
  static const kError = Color(0xFFFFB4AB);
  static const kOnError = Color(0xFF690005);
}

class MelbelTheme {
  static ThemeData get darkTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: MelbelColors.kSurface,
      canvasColor: MelbelColors.kSurface,
      colorScheme: const ColorScheme.dark(
        surface: MelbelColors.kSurface,
        primary: MelbelColors.kPrimaryContainer,
        onPrimary: MelbelColors.kOnPrimary,
        secondary: MelbelColors.kSecondary,
        onSecondary: MelbelColors.kOnSecondary,
        surfaceContainer: MelbelColors.kSurfaceContainer,
        surfaceContainerHigh: MelbelColors.kSurfaceContainerHigh,
        surfaceContainerLow: MelbelColors.kSurfaceContainerLow,
        outline: MelbelColors.kOutline,
        outlineVariant: MelbelColors.kOutlineVariant,
      ),
      textTheme: GoogleFonts.beVietnamProTextTheme(
        ThemeData.dark().textTheme,
      ).apply(
        bodyColor: MelbelColors.kOnSurface,
        displayColor: MelbelColors.kOnSurface,
      ),
      appBarTheme: const AppBarTheme(
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
          backgroundColor: MelbelColors.kPrimaryContainer,
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
