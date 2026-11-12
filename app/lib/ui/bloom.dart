// The big dahlia behind every page, as in the dashboard (index.html .bloom):
// four rings of petals turning slowly in opposite directions, breathing.
// Large and clear on the start page, small and faint elsewhere.

import 'dart:math' as math;

import 'package:material_ui/material_ui.dart';

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
  const Backdrop({super.key, required this.home, required this.child});

  final bool home;
  final Widget child;

  @override
  State<Backdrop> createState() => _BackdropState();
}

class _BackdropState extends State<Backdrop> with SingleTickerProviderStateMixin {
  late final AnimationController _clock =
      AnimationController(vsync: this, duration: const Duration(hours: 1))..repeat();

  @override
  void dispose() {
    _clock.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final still = MediaQuery.of(context).disableAnimations;
    return LayoutBuilder(builder: (context, box) {
      final side = math.min(900.0, box.maxWidth * 1.2);
      final home = widget.home;
      return Stack(children: [
        Positioned.fill(
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: p.bg,
              gradient: RadialGradient(
                center: const Alignment(.7, -.1),
                radius: 1.1,
                colors: [p.glow, p.bg],
                stops: const [0, .62],
              ),
            ),
          ),
        ),
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
            child: IgnorePointer(
              child: still
                  ? CustomPaint(painter: BloomPainter(0))
                  : AnimatedBuilder(
                      animation: _clock,
                      builder: (context, _) =>
                          CustomPaint(painter: BloomPainter(_clock.value * 3600)),
                    ),
            ),
          ),
        ),
        Positioned.fill(child: widget.child),
      ]);
    });
  }
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
