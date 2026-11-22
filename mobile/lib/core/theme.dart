import 'package:flutter/material.dart';

/// Calm, low-arousal semantic palette designed for ADHD-I (W3C COGA).
///
/// Keeps unified, ultra-gentle Dusk Lavender (`#8470B3`) and Soft Chamomile
/// Honey (`#C9A044`) brand accents identical across Light and Dark modes so
/// switching appearance never shifts brand hue, while surfaces adapt smoothly
/// between anti-glare Warm Butter-Cream and Deep Twilight Plum.
class MelbelColors {
  static bool isDark = false;
  static Color _pick(int day, int night) => Color(isDark ? night : day);

  static const Color kBrandLavender = Color(0xFF7A5EBD);
  static const Color kOnBrandLavender = Color(0xFFFFFDFB);
  static const Color kBrandHoney = Color(0xFFD9AE48);
  static const Color kOnBrandHoney = Color(0xFF2B2212);
  static const Color kBrandRose = Color(0xFFAA545E);

  static Color get kBackground => _pick(0xFFFBF5EC, 0xFF1A1523);
  static Color get kSurface => _pick(0xFFFFFDFB, 0xFF282235);
  static Color get kSurfaceDim => kBackground;
  static Color get kSurfaceBright => kSurfaceContainerHigh;
  static Color get kSurfaceContainerLowest => kBackground;
  static Color get kSurfaceContainerLow => kMutedSurface;
  static Color get kSurfaceContainer => kSurface;
  static Color get kSurfaceContainerHigh => kMutedSurface;
  static Color get kSurfaceContainerHighest => kSelectedSurface;
  static Color get kOnSurface => _pick(0xFF2B2338, 0xFFF5F1FA);
  static Color get kOnSurfaceVariant => _pick(0xFF6B607C, 0xFFBFB7CE);
  static Color get kTextPrimary => kOnSurface;
  static Color get kTextSecondary => kOnSurfaceVariant;

  static Color get kPrimary => kBrandLavender;
  static Color get kOnPrimary => kOnBrandLavender;
  static Color get kPrimaryContainer => kPrimary;
  static Color get kOnPrimaryContainer => kOnPrimary;

  static Color get kSecondary => kBrandLavender;
  static Color get kOnSecondary => kOnBrandLavender;
  static Color get kSecondaryContainer => _pick(0xFFECE3FA, 0xFF3D3252);
  static Color get kOnSecondaryContainer => _pick(0xFF2B2338, 0xFFF5F1FA);
  static Color get kSage => kPrimary;

  static Color get kTertiary => kBrandHoney;
  static Color get kOnTertiary => kOnBrandHoney;
  static Color get kTertiaryContainer => _pick(0xFFFCF6EA, 0xFF3D352E);
  static Color get kOnTertiaryContainer => _pick(0xFF3E3018, 0xFFF6E7C3);
  static Color get kTertiaryFixedDim => kTertiary;
  static Color get kAmber => kTertiary;

  static Color get kOutline => _pick(0xFFECE2D4, 0xFF3C334F);
  static Color get kOutlineVariant => kOutline;
  static Color get kMutedSurface => _pick(0xFFF1EAE0, 0xFF302940);
  static Color get kSelectedSurface => _pick(0xFFE9DFFA, 0xFF534273);
  static Color get kControlOutline => _pick(0xFF9387A6, 0xFF8C80A3);
  static Color get kShadow => const Color(0xFF000000);

  static Color get kError => kBrandRose;
  static Color get kOnError => kOnBrandLavender;
  static Color get kErrorContainer => _pick(0xFFFAEEF0, 0xFF382429);
  static Color get kOnErrorContainer => _pick(0xFF612830, 0xFFF6D8DC);
}

@immutable
class MelbelPalette extends ThemeExtension<MelbelPalette> {
  const MelbelPalette({
    required this.background,
    required this.surface,
    required this.mutedSurface,
    required this.selectedSurface,
    required this.onSurface,
    required this.onSurfaceVariant,
    required this.sage,
    required this.onSage,
    required this.sageSoft,
    required this.onSageSoft,
    required this.amber,
    required this.amberSoft,
    required this.onAmberSoft,
    required this.softError,
    required this.softErrorSoft,
    required this.onSoftErrorSoft,
    required this.accentInk,
    required this.amberInk,
    required this.errorInk,
    required this.hairline,
    required this.controlOutline,
  });

  final Color background;
  final Color surface;
  final Color mutedSurface;
  final Color selectedSurface;
  final Color onSurface;
  final Color onSurfaceVariant;
  final Color sage;
  final Color onSage;
  final Color sageSoft;
  final Color onSageSoft;
  final Color amber;
  final Color amberSoft;
  final Color onAmberSoft;
  final Color softError;
  final Color softErrorSoft;
  final Color onSoftErrorSoft;
  final Color accentInk;
  final Color amberInk;
  final Color errorInk;
  final Color hairline;
  final Color controlOutline;

  static const light = MelbelPalette(
    background: Color(0xFFFBF5EC),
    surface: Color(0xFFFFFDFB),
    mutedSurface: Color(0xFFF1EAE0),
    selectedSurface: Color(0xFFE9DFFA),
    onSurface: Color(0xFF2B2338),
    onSurfaceVariant: Color(0xFF6B607C),
    sage: MelbelColors.kBrandLavender,
    onSage: MelbelColors.kOnBrandLavender,
    sageSoft: Color(0xFFECE3FA),
    onSageSoft: Color(0xFF2B2338),
    amber: MelbelColors.kBrandHoney,
    amberSoft: Color(0xFFFCF6EA),
    onAmberSoft: Color(0xFF3E3018),
    softError: MelbelColors.kBrandRose,
    softErrorSoft: Color(0xFFFAEEF0),
    onSoftErrorSoft: Color(0xFF612830),
    accentInk: Color(0xFF6449A6),
    amberInk: Color(0xFF876010),
    errorInk: Color(0xFF943F4A),
    hairline: Color(0xFFECE2D4),
    controlOutline: Color(0xFF9387A6),
  );

  static const dark = MelbelPalette(
    background: Color(0xFF1A1523),
    surface: Color(0xFF282235),
    mutedSurface: Color(0xFF302940),
    selectedSurface: Color(0xFF534273),
    onSurface: Color(0xFFF5F1FA),
    onSurfaceVariant: Color(0xFFBFB7CE),
    sage: MelbelColors.kBrandLavender,
    onSage: MelbelColors.kOnBrandLavender,
    sageSoft: Color(0xFF3D3252),
    onSageSoft: Color(0xFFF5F1FA),
    amber: MelbelColors.kBrandHoney,
    amberSoft: Color(0xFF3D352E),
    onAmberSoft: Color(0xFFF6E7C3),
    softError: MelbelColors.kBrandRose,
    softErrorSoft: Color(0xFF382429),
    onSoftErrorSoft: Color(0xFFF6D8DC),
    accentInk: Color(0xFFC7B6F2),
    amberInk: Color(0xFFE8C468),
    errorInk: Color(0xFFEDA1AA),
    hairline: Color(0xFF3C334F),
    controlOutline: Color(0xFF8C80A3),
  );

  static const lightHighContrast = MelbelPalette(
    background: Color(0xFFFBF5EC),
    surface: Color(0xFFFFFDFB),
    mutedSurface: Color(0xFFF1EAE0),
    selectedSurface: Color(0xFFE9DFFA),
    onSurface: Color(0xFF2B2338),
    onSurfaceVariant: Color(0xFF4A4158),
    sage: MelbelColors.kBrandLavender,
    onSage: MelbelColors.kOnBrandLavender,
    sageSoft: Color(0xFFECE3FA),
    onSageSoft: Color(0xFF2B2338),
    amber: MelbelColors.kBrandHoney,
    amberSoft: Color(0xFFFCF6EA),
    onAmberSoft: Color(0xFF3E3018),
    softError: MelbelColors.kBrandRose,
    softErrorSoft: Color(0xFFFAEEF0),
    onSoftErrorSoft: Color(0xFF612830),
    accentInk: Color(0xFF4E348F),
    amberInk: Color(0xFF6E4C08),
    errorInk: Color(0xFF7B2B36),
    hairline: Color(0xFF8F82A6),
    controlOutline: Color(0xFF5D5270),
  );

  static const darkHighContrast = MelbelPalette(
    background: Color(0xFF1A1523),
    surface: Color(0xFF282235),
    mutedSurface: Color(0xFF302940),
    selectedSurface: Color(0xFF534273),
    onSurface: Color(0xFFF5F1FA),
    onSurfaceVariant: Color(0xFFDFD8EB),
    sage: MelbelColors.kBrandLavender,
    onSage: MelbelColors.kOnBrandLavender,
    sageSoft: Color(0xFF3D3252),
    onSageSoft: Color(0xFFF5F1FA),
    amber: MelbelColors.kBrandHoney,
    amberSoft: Color(0xFF3D352E),
    onAmberSoft: Color(0xFFF6E7C3),
    softError: MelbelColors.kBrandRose,
    softErrorSoft: Color(0xFF382429),
    onSoftErrorSoft: Color(0xFFF6D8DC),
    accentInk: Color(0xFFDDD0FA),
    amberInk: Color(0xFFF4D88A),
    errorInk: Color(0xFFF6BCC3),
    hairline: Color(0xFF80749B),
    controlOutline: Color(0xFFB8AECB),
  );

  static MelbelPalette of(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final highContrast = MediaQuery.maybeHighContrastOf(context) ?? false;
    final ext = theme.extension<MelbelPalette>();
    if (ext != null) {
      if (highContrast && (identical(ext, light) || identical(ext, dark))) {
        return isDark ? darkHighContrast : lightHighContrast;
      }
      return ext;
    }
    if (highContrast) {
      return isDark ? darkHighContrast : lightHighContrast;
    }
    return isDark ? dark : light;
  }

  @override
  MelbelPalette copyWith({
    Color? background,
    Color? surface,
    Color? mutedSurface,
    Color? selectedSurface,
    Color? onSurface,
    Color? onSurfaceVariant,
    Color? sage,
    Color? onSage,
    Color? sageSoft,
    Color? onSageSoft,
    Color? amber,
    Color? amberSoft,
    Color? onAmberSoft,
    Color? softError,
    Color? softErrorSoft,
    Color? onSoftErrorSoft,
    Color? accentInk,
    Color? amberInk,
    Color? errorInk,
    Color? hairline,
    Color? controlOutline,
  }) =>
      MelbelPalette(
        background: background ?? this.background,
        surface: surface ?? this.surface,
        mutedSurface: mutedSurface ?? this.mutedSurface,
        selectedSurface: selectedSurface ?? this.selectedSurface,
        onSurface: onSurface ?? this.onSurface,
        onSurfaceVariant: onSurfaceVariant ?? this.onSurfaceVariant,
        sage: sage ?? this.sage,
        onSage: onSage ?? this.onSage,
        sageSoft: sageSoft ?? this.sageSoft,
        onSageSoft: onSageSoft ?? this.onSageSoft,
        amber: amber ?? this.amber,
        amberSoft: amberSoft ?? this.amberSoft,
        onAmberSoft: onAmberSoft ?? this.onAmberSoft,
        softError: softError ?? this.softError,
        softErrorSoft: softErrorSoft ?? this.softErrorSoft,
        onSoftErrorSoft: onSoftErrorSoft ?? this.onSoftErrorSoft,
        accentInk: accentInk ?? this.accentInk,
        amberInk: amberInk ?? this.amberInk,
        errorInk: errorInk ?? this.errorInk,
        hairline: hairline ?? this.hairline,
        controlOutline: controlOutline ?? this.controlOutline,
      );

  @override
  MelbelPalette lerp(ThemeExtension<MelbelPalette>? other, double t) {
    if (other is! MelbelPalette) return this;
    return MelbelPalette(
      background: Color.lerp(background, other.background, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      mutedSurface: Color.lerp(mutedSurface, other.mutedSurface, t)!,
      selectedSurface: Color.lerp(selectedSurface, other.selectedSurface, t)!,
      onSurface: Color.lerp(onSurface, other.onSurface, t)!,
      onSurfaceVariant:
          Color.lerp(onSurfaceVariant, other.onSurfaceVariant, t)!,
      sage: Color.lerp(sage, other.sage, t)!,
      onSage: Color.lerp(onSage, other.onSage, t)!,
      sageSoft: Color.lerp(sageSoft, other.sageSoft, t)!,
      onSageSoft: Color.lerp(onSageSoft, other.onSageSoft, t)!,
      amber: Color.lerp(amber, other.amber, t)!,
      amberSoft: Color.lerp(amberSoft, other.amberSoft, t)!,
      onAmberSoft: Color.lerp(onAmberSoft, other.onAmberSoft, t)!,
      softError: Color.lerp(softError, other.softError, t)!,
      softErrorSoft: Color.lerp(softErrorSoft, other.softErrorSoft, t)!,
      onSoftErrorSoft: Color.lerp(onSoftErrorSoft, other.onSoftErrorSoft, t)!,
      accentInk: Color.lerp(accentInk, other.accentInk, t)!,
      amberInk: Color.lerp(amberInk, other.amberInk, t)!,
      errorInk: Color.lerp(errorInk, other.errorInk, t)!,
      hairline: Color.lerp(hairline, other.hairline, t)!,
      controlOutline: Color.lerp(controlOutline, other.controlOutline, t)!,
    );
  }
}

extension MelbelContextTheme on BuildContext {
  MelbelPalette get melbel => MelbelPalette.of(this);
}

class MelbelTheme {
  static ThemeData get currentTheme =>
      themeFor(isDark: MelbelColors.isDark, highContrast: false);

  static ThemeData themeFor({
    required bool isDark,
    bool highContrast = false,
  }) {
    final dark = isDark;
    final palette = highContrast
        ? (dark
            ? MelbelPalette.darkHighContrast
            : MelbelPalette.lightHighContrast)
        : (dark ? MelbelPalette.dark : MelbelPalette.light);
    final brightness = dark ? Brightness.dark : Brightness.light;
    final baseTextTheme =
        (dark ? ThemeData.dark() : ThemeData.light()).textTheme.apply(
              fontFamily: 'Be Vietnam Pro',
              bodyColor: palette.onSurface,
              displayColor: palette.onSurface,
            );

    final textTheme = baseTextTheme.copyWith(
      headlineMedium: baseTextTheme.headlineMedium?.copyWith(
        fontWeight: FontWeight.w700,
        letterSpacing: -0.4,
        height: 1.25,
      ),
      headlineSmall: baseTextTheme.headlineSmall?.copyWith(
        fontSize: 22,
        fontWeight: FontWeight.w700,
        letterSpacing: -0.3,
        height: 1.3,
      ),
      titleLarge: baseTextTheme.titleLarge?.copyWith(
        fontSize: 20,
        fontWeight: FontWeight.w700,
        letterSpacing: -0.25,
        height: 1.3,
      ),
      titleMedium: baseTextTheme.titleMedium?.copyWith(
        fontSize: 16,
        fontWeight: FontWeight.w600,
        height: 1.4,
      ),
      titleSmall: baseTextTheme.titleSmall?.copyWith(
        fontSize: 14,
        fontWeight: FontWeight.w600,
        height: 1.4,
      ),
      bodyLarge: baseTextTheme.bodyLarge?.copyWith(
        fontSize: 16,
        height: 1.55,
        fontWeight: FontWeight.w400,
      ),
      bodyMedium: baseTextTheme.bodyMedium?.copyWith(
        fontSize: 16,
        height: 1.5,
        fontWeight: FontWeight.w400,
      ),
      bodySmall: baseTextTheme.bodySmall?.copyWith(
        fontSize: 13,
        height: 1.45,
        fontWeight: FontWeight.w500,
        color: palette.onSurfaceVariant,
      ),
      labelLarge: baseTextTheme.labelLarge?.copyWith(
        fontSize: 14,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.1,
      ),
      labelMedium: baseTextTheme.labelMedium?.copyWith(
        fontSize: 12,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.5,
        color: palette.onSurfaceVariant,
      ),
      labelSmall: baseTextTheme.labelSmall?.copyWith(
        fontSize: 12,
        fontWeight: FontWeight.w500,
        color: palette.onSurfaceVariant,
      ),
    );

    final colorScheme = ColorScheme(
      brightness: brightness,
      surface: palette.surface,
      onSurface: palette.onSurface,
      onSurfaceVariant: palette.onSurfaceVariant,
      primary: palette.sage,
      onPrimary: palette.onSage,
      primaryContainer: palette.sageSoft,
      onPrimaryContainer: palette.onSageSoft,
      secondary: palette.sage,
      onSecondary: palette.onSage,
      secondaryContainer: palette.sageSoft,
      onSecondaryContainer: palette.onSageSoft,
      tertiary: palette.amber,
      onTertiary: MelbelColors.kOnTertiary,
      tertiaryContainer: palette.amberSoft,
      onTertiaryContainer: palette.onAmberSoft,
      error: palette.softError,
      onError: MelbelColors.kOnError,
      errorContainer: palette.softErrorSoft,
      onErrorContainer: palette.onSoftErrorSoft,
      surfaceContainerLowest: palette.background,
      surfaceContainerLow: palette.mutedSurface,
      surfaceContainer: palette.surface,
      surfaceContainerHigh: palette.mutedSurface,
      surfaceContainerHighest: palette.selectedSurface,
      outline: palette.controlOutline,
      outlineVariant: palette.hairline,
      surfaceTint: Colors.transparent,
      inverseSurface: palette.onSurface,
      onInverseSurface: palette.surface,
      inversePrimary: palette.sage,
    );

    return ThemeData(
      useMaterial3: true,
      fontFamily: 'Be Vietnam Pro',
      brightness: brightness,
      scaffoldBackgroundColor: palette.background,
      canvasColor: palette.surface,
      focusColor: palette.sage.withValues(alpha: 0.18),
      disabledColor: palette.controlOutline.withValues(alpha: 0.6),
      colorScheme: colorScheme,
      extensions: [palette],
      textTheme: textTheme,
      dividerTheme: DividerThemeData(
        color: palette.hairline,
        thickness: 1,
        space: 1,
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: palette.sage,
        linearTrackColor: palette.selectedSurface,
        circularTrackColor: palette.selectedSurface,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: palette.mutedSurface,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 18,
          vertical: 15,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(20),
          borderSide: highContrast
              ? BorderSide(color: palette.controlOutline, width: 1.2)
              : BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(20),
          borderSide: highContrast
              ? BorderSide(color: palette.controlOutline, width: 1.2)
              : BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(20),
          borderSide: BorderSide(color: palette.sage, width: 1.8),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(20),
          borderSide: BorderSide(color: palette.softError, width: 1.5),
        ),
        labelStyle: textTheme.bodyMedium?.copyWith(
          color: palette.onSurfaceVariant,
        ),
        hintStyle: textTheme.bodyMedium?.copyWith(
          color: palette.onSurfaceVariant,
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: palette.mutedSurface,
        selectedColor: palette.selectedSurface,
        disabledColor: palette.mutedSurface.withValues(alpha: 0.5),
        side: BorderSide.none,
        shape: const StadiumBorder(),
        labelStyle: textTheme.labelLarge?.copyWith(color: palette.onSurface),
        secondaryLabelStyle:
            textTheme.labelLarge?.copyWith(color: palette.onSageSoft),
        checkmarkColor: palette.onSageSoft,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: dark ? palette.selectedSurface : palette.onSurface,
        contentTextStyle: textTheme.bodyMedium?.copyWith(
          color: dark ? palette.onSurface : palette.surface,
        ),
        actionTextColor: palette.amber,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
        ),
        elevation: 2,
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: palette.surface,
        surfaceTintColor: Colors.transparent,
        dragHandleColor: palette.controlOutline.withValues(alpha: 0.55),
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: palette.background,
        foregroundColor: palette.onSurface,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: textTheme.titleLarge?.copyWith(
          color: palette.onSurface,
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: palette.sage,
          foregroundColor: palette.onSage,
          minimumSize: const Size.fromHeight(56),
          elevation: 0,
          shape: const StadiumBorder(),
          textStyle: textTheme.titleMedium?.copyWith(
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          backgroundColor: dark ? const Color(0xFF231D2E) : palette.surface,
          foregroundColor: palette.onSurface,
          minimumSize: const Size(48, 48),
          side: highContrast
              ? BorderSide(color: palette.controlOutline, width: 1.5)
              : dark
                  ? BorderSide(color: palette.hairline, width: 1.0)
                  : BorderSide.none,
          shape: const StadiumBorder(),
          textStyle: textTheme.labelLarge?.copyWith(
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: palette.accentInk,
          minimumSize: const Size(48, 48),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: textTheme.labelLarge,
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          minimumSize: const Size(48, 48),
          tapTargetSize: MaterialTapTargetSize.padded,
        ),
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: MelbelFlowPageTransitionsBuilder(),
          TargetPlatform.iOS: MelbelFlowPageTransitionsBuilder(),
          TargetPlatform.windows: MelbelFlowPageTransitionsBuilder(),
          TargetPlatform.linux: MelbelFlowPageTransitionsBuilder(),
          TargetPlatform.macOS: MelbelFlowPageTransitionsBuilder(),
        },
      ),
    );
  }
}

/// GPU-composited horizontal flow transition (`FadeTransition` + subtle
/// parallax `SlideTransition`) that stays at 60 FPS and respects reduced motion.
class MelbelFlowPageTransitionsBuilder extends PageTransitionsBuilder {
  const MelbelFlowPageTransitionsBuilder();

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    if (MediaQuery.disableAnimationsOf(context)) return child;

    final primary = CurvedAnimation(
      parent: animation,
      curve: Curves.easeOutCubic,
      reverseCurve: Curves.easeInCubic,
    );
    final secondary = CurvedAnimation(
      parent: secondaryAnimation,
      curve: Curves.easeOutCubic,
      reverseCurve: Curves.easeInCubic,
    );

    return RepaintBoundary(
      child: SlideTransition(
        position: Tween<Offset>(
          begin: Offset.zero,
          end: const Offset(-0.025, 0),
        ).animate(secondary),
        child: SlideTransition(
          position: Tween<Offset>(
            begin: const Offset(0.06, 0),
            end: Offset.zero,
          ).animate(primary),
          child: FadeTransition(
            opacity: primary,
            child: child,
          ),
        ),
      ),
    );
  }
}
