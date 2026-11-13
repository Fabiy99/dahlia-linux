// Colors and shapes of the Dahlia design (same tokens as the dashboard's
// style.css, light and dark).

import 'package:material_ui/material_ui.dart';

@immutable
class Palette extends ThemeExtension<Palette> {
  const Palette({
    required this.bg,
    required this.fg,
    required this.muted,
    required this.card,
    required this.line,
    required this.accent,
    required this.accentFg,
    required this.accentSoft,
    required this.danger,
    required this.warn,
    required this.ok,
    required this.track,
    required this.glow,
    required this.glass,
    required this.glassLine,
    required this.bar,
    required this.navActive,
    required this.navActiveFg,
    required this.sub,
    required this.bloomHome,
  });

  final Color bg, fg, muted, card, line, accent, accentFg, accentSoft;
  final Color danger, warn, ok, track, glow, glass, glassLine, bar, navActive, navActiveFg, sub;
  final double bloomHome;

  static const light = Palette(
    bg: Color(0xFFF4F3F7),
    fg: Color(0xFF1D1B22),
    muted: Color(0xFF6B6775),
    card: Color(0xFFFFFFFF),
    line: Color(0xFFE2DFE8),
    accent: Color(0xFFA8458F),
    accentFg: Color(0xFFFFFFFF),
    accentSoft: Color(0xFFF3E3EE),
    danger: Color(0xFFC0352B),
    warn: Color(0xFFB26A00),
    ok: Color(0xFF1F7A45),
    track: Color(0xFFE9E6EE),
    glow: Color(0xFFF3E3EE),
    glass: Color(0xB8FFFFFF),
    glassLine: Color(0x17281432),
    bar: Color(0x9EFFFFFF),
    navActive: Color(0x24A8458F),
    navActiveFg: Color(0xFF8A2F74),
    sub: Color(0x0A281432),
    bloomHome: .42,
  );

  static const dark = Palette(
    bg: Color(0xFF141218),
    fg: Color(0xFFEFE8F2),
    muted: Color(0xFFB3A9BD),
    card: Color(0xFF1C1A22),
    line: Color(0xFF2F2B37),
    accent: Color(0xFFF2A7D6),
    accentFg: Color(0xFF1D1020),
    accentSoft: Color(0xFF3A2433),
    danger: Color(0xFFF2948C),
    warn: Color(0xFFF0B45A),
    ok: Color(0xFF6FD39A),
    track: Color(0xFF2C2833),
    glow: Color(0xFF2A1626),
    glass: Color(0x9E1A1620),
    glassLine: Color(0x12FFFFFF),
    bar: Color(0x8C18141E),
    navActive: Color(0x33F2A7D6),
    navActiveFg: Color(0xFFFFD6EE),
    sub: Color(0x0DFFFFFF),
    bloomHome: .62,
  );

  @override
  Palette copyWith() => this;

  @override
  Palette lerp(Palette? other, double t) => t < .5 ? this : (other ?? this);
}

extension PaletteOf on BuildContext {
  Palette get palette => Theme.of(this).extension<Palette>()!;
}

ThemeData dahliaTheme(Brightness b) {
  final p = b == Brightness.dark ? Palette.dark : Palette.light;
  final scheme = ColorScheme.fromSeed(
    seedColor: p.accent,
    brightness: b,
    primary: p.accent,
    onPrimary: p.accentFg,
    surface: p.card,
    onSurface: p.fg,
    error: p.danger,
  );
  final radius = BorderRadius.circular(12);
  return ThemeData(
    useMaterial3: true,
    brightness: b,
    colorScheme: scheme,
    fontFamily: 'Inter',
    scaffoldBackgroundColor: p.bg,
    extensions: [p],
    textTheme: Typography.material2021(platform: TargetPlatform.linux)
        .englishLike
        .apply(fontFamily: 'Inter', bodyColor: p.fg, displayColor: p.fg),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: p.sub,
      border: OutlineInputBorder(borderRadius: radius, borderSide: BorderSide(color: p.line)),
      enabledBorder: OutlineInputBorder(borderRadius: radius, borderSide: BorderSide(color: p.line)),
      focusedBorder: OutlineInputBorder(borderRadius: radius, borderSide: BorderSide(color: p.accent, width: 2)),
      labelStyle: TextStyle(color: p.muted),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: p.accent,
        foregroundColor: p.accentFg,
        minimumSize: const Size(44, 46),
        padding: const EdgeInsets.symmetric(horizontal: 20),
        shape: RoundedRectangleBorder(borderRadius: radius),
        textStyle: const TextStyle(fontFamily: 'Inter', fontWeight: FontWeight.w600, fontSize: 14),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: p.fg,
        minimumSize: const Size(44, 46),
        padding: const EdgeInsets.symmetric(horizontal: 18),
        side: BorderSide(color: p.line),
        shape: RoundedRectangleBorder(borderRadius: radius),
        textStyle: const TextStyle(fontFamily: 'Inter', fontWeight: FontWeight.w500, fontSize: 14),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: p.navActiveFg,
        minimumSize: const Size(44, 44),
        shape: RoundedRectangleBorder(borderRadius: radius),
      ),
    ),
    checkboxTheme: CheckboxThemeData(
      fillColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? p.accent : null),
      checkColor: WidgetStatePropertyAll(p.accentFg),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: p.card,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
    ),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating, width: 560),
    progressIndicatorTheme: ProgressIndicatorThemeData(color: p.accent, linearTrackColor: p.track),
  );
}
