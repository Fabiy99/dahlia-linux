// The big dahlia behind every page, as in the dashboard (index.html .bloom):
// four rings of petals turning slowly in opposite directions, breathing.
// Large and clear on the start page, small and faint elsewhere.

import 'dart:math' as math;
import 'dart:ui' show ImageFilter;

import 'package:material_ui/material_ui.dart';

import '../src/home_layout.dart';

import 'theme.dart';

class _Ring {
  const _Ring(this.petal, this.count, this.offset, this.colors, this.period, this.stroke);

  /// Cubic petal from the dashboard's SVG: (c1x, c1y, c2x, c2y, tip).
  final List<double> petal;
  final int count;
  final double offset;
  final List<Color> colors;

  /// Seconds per turn; negative turns the other way.
  final double period;
  final double stroke;
}

const _rings = [
  _Ring([65, -72, 50, -204, -240], 16, 0, [Color(0xFF6E1A54), Color(0xFFBD4597), Color(0xFFF29BD4)], 300, 1.5),
  _Ring([54, -56, 42, -157, -185], 16, 11.25, [Color(0xFF93296F), Color(0xFFE07AB9), Color(0xFFFFC2E5)], -220, 1.5),
  _Ring([45, -39, 35, -110, -130], 12, 15, [Color(0xFFC4579B), Color(0xFFF6AAD7), Color(0xFFFFE3F2)], 160, 1.2),
  _Ring([31, -22, 24, -61, -72], 8, 22.5, [Color(0xFFFFE4F3), Color(0xFFFFE4F3), Color(0xFFFFE4F3)], -120, 1),
];

class BloomPainter extends CustomPainter {
  BloomPainter(this.seconds) : super(repaint: null);

  final double seconds;

  @override
  void paint(Canvas canvas, Size size) {
    final scale = size.shortestSide / 520;
    canvas
      ..save()
      ..translate(size.width / 2, size.height / 2)
      ..scale(scale);
    final breathe = 1 + .0175 * (1 - math.cos(seconds / 9 * 2 * math.pi));
    canvas.scale(breathe);
    final outline = Paint()
      ..style = PaintingStyle.stroke
      ..color = const Color(0x4D1A1020);
    for (final r in _rings) {
      final tip = r.petal[4];
      final path = Path()
        ..moveTo(0, 0)
        ..cubicTo(r.petal[0], r.petal[1], r.petal[2], r.petal[3], 0, tip)
        ..cubicTo(-r.petal[2], r.petal[3], -r.petal[0], r.petal[1], 0, 0)
        ..close();
      final fill = Paint()
        ..shader = LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: r.colors,
          stops: const [0, .58, 1],
        ).createShader(Rect.fromLTRB(-1, tip, 1, 0));
      outline.strokeWidth = r.stroke;
      final turn = seconds / r.period * 2 * math.pi;
      for (var i = 0; i < r.count; i++) {
        canvas
          ..save()
          ..rotate(turn + (r.offset + i * 360 / r.count) * math.pi / 180)
          ..drawPath(path, fill)
          ..drawPath(path, outline)
          ..restore();
      }
    }
    canvas
      ..drawCircle(Offset.zero, 24, Paint()..color = const Color(0xFFF2B45C))
      ..drawCircle(Offset.zero, 11, Paint()..color = const Color(0xFFD97A2E))
      ..restore();
  }

  @override
  bool shouldRepaint(BloomPainter old) => old.seconds != seconds;
}

/// Glow and dahlia behind the page; [home] moves it in and makes it clear.
class Backdrop extends StatefulWidget {
  const Backdrop({super.key, required this.home, required this.child, this.look = const Look(), this.mood});

  final bool home;
  final Widget child;

  /// Background and weather effects chosen on the start screen.
  final Look look;

  /// Weather of the selected place; null shows no weather.
  final ({String kind, bool day})? mood;

  @override
  State<Backdrop> createState() => _BackdropState();
}

/// Saturation and brightness as the CSS filters of the weather moods.
ColorFilter _moodFilter(double saturate, double brightness) {
  const r = .2126, g = .7152, b = .0722;
  final s = saturate, br = brightness;
  return ColorFilter.matrix([
    (r + (1 - r) * s) * br, (g - g * s) * br, (b - b * s) * br, 0, 0,
    (r - r * s) * br, (g + (1 - g) * s) * br, (b - b * s) * br, 0, 0,
    (r - r * s) * br, (g - g * s) * br, (b + (1 - b) * s) * br, 0, 0,
    0, 0, 0, 1, 0,
  ]);
}

class _BackdropState extends State<Backdrop> with SingleTickerProviderStateMixin {
  late final AnimationController _clock =
      AnimationController(vsync: this, duration: const Duration(hours: 1))..repeat();

  @override
  void dispose() {
    _clock.dispose();
    super.dispose();
  }

  Color _glow(Palette p, bool dark) {
    final m = widget.mood;
    if (m == null) return p.glow;
    if (dark) {
      if (!m.day) return const Color(0xFF191428);
      return switch (m.kind) {
        'clear' => const Color(0xFF3A2224),
        'rain' || 'storm' => const Color(0xFF1D2030),
        'snow' => const Color(0xFF262A36),
        _ => p.glow,
      };
    }
    return m.kind == 'clear' ? const Color(0xFFF7E2D0) : p.glow;
  }

  ColorFilter? _filter() {
    final m = widget.mood;
    if (m == null) return null;
    if (!m.day) return _moodFilter(.8, .7);
    return switch (m.kind) {
      'rain' || 'storm' => _moodFilter(.75, .85),
      'fog' || 'cloudy' => _moodFilter(.85, 1),
      _ => null,
    };
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final still = MediaQuery.of(context).disableAnimations || widget.look.background == 'still';
    final mood = widget.mood;
    final fx = mood != null && widget.look.weatherFx && !MediaQuery.of(context).disableAnimations;
    return LayoutBuilder(builder: (context, box) {
      final side = math.min(900.0, box.maxWidth * 1.2);
      final home = widget.home;
      Widget bloom = still
          ? CustomPaint(painter: BloomPainter(0))
          : AnimatedBuilder(
              animation: _clock,
              builder: (context, _) => CustomPaint(painter: BloomPainter(_clock.value * 3600)),
            );
      final filter = _filter();
      if (filter != null) bloom = ColorFiltered(colorFilter: filter, child: bloom);
      if (!home) bloom = ImageFiltered(imageFilter: ImageFilter.blur(sigmaX: 3, sigmaY: 3), child: bloom);
      return Stack(children: [
        Positioned.fill(
          child: AnimatedContainer(
            duration: const Duration(seconds: 2),
            decoration: BoxDecoration(
              color: p.bg,
              gradient: RadialGradient(
                center: const Alignment(.7, -.1),
                radius: 1.1,
                colors: [_glow(p, dark), p.bg],
                stops: const [0, .62],
              ),
            ),
          ),
        ),
        if (widget.look.background != 'off')
          AnimatedPositioned(
            duration: const Duration(milliseconds: 1600),
            curve: Curves.easeOutCubic,
            left: box.maxWidth / 2 - (home ? side * .08 : 0),
            top: box.maxHeight / 2 - side * (home ? .46 : .38),
            width: side * (home ? 1 : 1.3),
            height: side * (home ? 1 : 1.3),
            child: AnimatedOpacity(
              duration: const Duration(milliseconds: 1600),
              opacity: home ? p.bloomHome : .12,
              child: IgnorePointer(child: bloom),
            ),
          ),
        if (fx)
          Positioned.fill(
            child: IgnorePointer(
              child: AnimatedBuilder(
                animation: _clock,
                builder: (context, _) => CustomPaint(painter: WeatherPainter(_clock.value * 3600, mood.kind, mood.day)),
              ),
            ),
          ),
        Positioned.fill(child: widget.child),
      ]);
    });
  }
}

/// Weather particles of bloom.js / style.css: sun, clouds, rain, snow, stars, flash.
class WeatherPainter extends CustomPainter {
  WeatherPainter(this.seconds, this.kind, this.day);

  final double seconds;
  final String kind;
  final bool day;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width, h = size.height;
    const rem = 16.0;
    if (kind == 'clear' && day) {
      // .sun: 56rem circle at top -16rem, right -12rem, glowing over 6 s.
      final k = .5 - .5 * math.cos(seconds / 6 * math.pi);
      final r = 28 * rem * (1 + .08 * k);
      final c = Offset(w + 12 * rem - 28 * rem, -16 * rem + 28 * rem);
      canvas.drawCircle(
        c,
        r,
        Paint()
          ..shader = RadialGradient(colors: [
            Color.fromRGBO(255, 196, 110, .40 * (1 - .4 * k)),
            Color.fromRGBO(255, 160, 90, .14 * (1 - .4 * k)),
            const Color(0x00FFA05A),
          ], stops: const [0, .4, .7])
              .createShader(Rect.fromCircle(center: c, radius: r)),
      );
    }
    if (const ['partly', 'cloudy', 'fog', 'rain', 'storm'].contains(kind)) {
      final n = kind == 'partly' ? 2 : 4;
      for (var i = 0; i < n; i++) {
        final period = 70.0 + i * 22;
        final t = ((seconds + i * 25) % period) / period;
        final x = -40 * rem + t * (w * 1.1 + 40 * rem);
        final rect = Rect.fromLTWH(x, h * (.06 + i * .18), 36 * rem, 11 * rem);
        canvas.drawOval(
          rect,
          Paint()
            ..shader = const RadialGradient(colors: [Color(0x1FC8C3DC), Color(0x00C8C3DC)], stops: [0, .7])
                .createShader(rect),
        );
      }
    }
    if (kind == 'rain' || kind == 'storm') {
      for (var i = 0; i < 70; i++) {
        final dur = .7 + (i % 5) * .13;
        final t = ((seconds + (i * .29) % 1.4) % dur) / dur;
        final len = (3 + i % 4) * rem;
        final x = w * ((i * 37) % 100) / 100;
        final y = -8 * rem + t * h * 1.2;
        final rect = Rect.fromLTWH(x, y, 1.5, len);
        canvas.drawRect(
          rect,
          Paint()
            ..shader = const LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [Color(0x0096AFE6), Color(0x8C96AFE6)],
            ).createShader(rect),
        );
      }
    }
    if (kind == 'storm') {
      final t = seconds % 7 / 7;
      final a = (t > .86 && t < .90) ? (t < .88 ? (t - .86) / .02 * .5 : (.90 - t) / .02 * .5)
          : (t > .90 && t < .94) ? (t < .92 ? (t - .90) / .02 * .28 : (.94 - t) / .02 * .28) : 0.0;
      if (a > 0) canvas.drawRect(Offset.zero & size, Paint()..color = Color.fromRGBO(233, 228, 255, a));
    }
    if (kind == 'snow') {
      final flake = Paint()..color = const Color(0xD9FFFFFF);
      for (var i = 0; i < 55; i++) {
        final dur = 9 + (i % 6) * 1.7;
        final t = ((seconds + (i * 1.3) % 14) % dur) / dur;
        final x0 = w * ((i * 41) % 100) / 100;
        final dx = t < .5 ? 1.8 * rem * t * 2 : 1.8 * rem - 2.4 * rem * (t - .5) * 2;
        final y = -8 * rem + (t < .5 ? h * .55 * t * 2 : h * .55 + h * .6 * (t - .5) * 2);
        canvas.drawCircle(Offset(x0 + dx, y), (.2 + (i % 4) * .1) * rem / 2, flake);
      }
    }
    if (!day && (kind == 'clear' || kind == 'partly')) {
      for (var i = 0; i < 45; i++) {
        final t = ((seconds + i % 3) % 6) / 3;
        final a = .15 + .75 * (t < 1 ? t : 2 - t);
        canvas.drawCircle(
          Offset(w * ((i * 29) % 100) / 100, h * ((i * 53) % 60) / 100),
          1,
          Paint()..color = Color.fromRGBO(255, 255, 255, a),
        );
      }
    }
  }

  @override
  bool shouldRepaint(WeatherPainter old) => old.seconds != seconds || old.kind != kind || old.day != day;
}

/// The small dahlia mark used as logo (same shape as favicon.svg).
class DahliaMark extends StatelessWidget {
  const DahliaMark({super.key, this.size = 28});

  final double size;

  @override
  Widget build(BuildContext context) =>
      SizedBox.square(dimension: size, child: CustomPaint(painter: _MarkPainter(context.palette.accent)));
}

class _MarkPainter extends CustomPainter {
  _MarkPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = color;
    canvas
      ..save()
      ..scale(size.width / 24)
      ..drawCircle(const Offset(12, 12), 2.6, paint);
    final petal = Path()
      ..moveTo(12, 3)
      ..cubicTo(14, 5.2, 14, 7.6, 12, 9.4)
      ..cubicTo(10, 7.6, 10, 5.2, 12, 3)
      ..close();
    final diag = Path()
      ..moveTo(5.6, 5.6)
      ..cubicTo(8.6, 5.4, 10.3, 7.1, 10.1, 10.1)
      ..cubicTo(7.1, 10.3, 5.4, 8.6, 5.6, 5.6)
      ..close();
    for (var i = 0; i < 4; i++) {
      canvas
        ..save()
        ..translate(12, 12)
        ..rotate(i * math.pi / 2)
        ..translate(-12, -12)
        ..drawPath(petal, paint)
        ..drawPath(diag, paint)
        ..restore();
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(_MarkPainter old) => old.color != color;
}
