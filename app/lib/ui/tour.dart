// Welcome tour on the first start of the app: a few steps that point at the
// parts of the top bar and the start screen. Shown once per app; "Skip" ends
// it as well.

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import 'theme.dart';

/// Parts the tour points at; the keys are only attached while it runs, so the
/// pages of an area change never hold the same key twice.
final _keys = {
  for (final k in ['customize', 'apps', 'storage', 'files', 'more', 'bell', 'phone']) k: GlobalKey(),
};

Key? tourKey(Controller c, String name) => c.tourActive && c.area == Area.home ? _keys[name] : null;

const tourSteps = [
  (null, 'tour.welcome'),
  ('customize', 'tour.customize'),
  ('apps', 'tour.apps'),
  ('storage', 'tour.storage'),
  ('files', 'tour.files'),
  ('more', 'tour.more'),
  ('bell', 'tour.bell'),
  ('phone', 'tour.phone'),
  (null, 'tour.done'),
];

class TourOverlay extends StatefulWidget {
  const TourOverlay(this.c, {super.key});

  final Controller c;

  @override
  State<TourOverlay> createState() => _TourOverlayState();
}

class _TourOverlayState extends State<TourOverlay> {
  int step = 0;
  Rect? hole;

  @override
  void initState() {
    super.initState();
    _measure();
  }

  /// Finds the target after the frame; steps without a visible target are skipped.
  void _measure() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final name = tourSteps[step].$1;
      Rect? r;
      if (name != null) {
        final box = _keys[name]!.currentContext?.findRenderObject();
        final self = context.findRenderObject();
        if (box is RenderBox && box.hasSize && self is RenderBox) {
          r = box.localToGlobal(Offset.zero, ancestor: self) & box.size;
        }
        if (r == null) {
          _go(step + 1);
          return;
        }
      }
      setState(() => hole = r?.inflate(6));
    });
  }

  void _go(int next) {
    if (next >= tourSteps.length) {
      widget.c.finishTour();
      return;
    }
    setState(() {
      step = next;
      hole = null;
    });
    _measure();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final p = context.palette;
    final (target, key) = tourSteps[step];
    final last = step == tourSteps.length - 1;
    final card = Material(
      color: p.card,
      elevation: 12,
      borderRadius: BorderRadius.circular(18),
      child: Container(
        width: 380,
        padding: const EdgeInsets.fromLTRB(22, 20, 22, 14),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(c.t('$key.title'), style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Text(c.t('$key.text'), style: TextStyle(color: p.muted, height: 1.45)),
            const SizedBox(height: 16),
            Row(
              children: [
                for (var i = 0; i < tourSteps.length; i++)
                  Container(
                    width: i == step ? 16 : 6,
                    height: 6,
                    margin: const EdgeInsets.only(right: 4),
                    decoration: BoxDecoration(
                      color: i == step ? p.accent : p.muted.withValues(alpha: .35),
                      borderRadius: BorderRadius.circular(3),
                    ),
                  ),
                const Spacer(),
                if (!last) TextButton(onPressed: c.finishTour, child: Text(c.t('tour.skip'))),
                const SizedBox(width: 6),
                FilledButton(
                  autofocus: true,
                  onPressed: () => _go(step + 1),
                  child: Text(c.t(last ? 'tour.finish' : (step == 0 ? 'tour.start' : 'tour.next'))),
                ),
              ],
            ),
          ],
        ),
      ),
    );
    return LayoutBuilder(
      builder: (context, box) {
        final h = hole;
        Widget placed;
        if (target == null || h == null) {
          placed = Center(child: card);
        } else {
          final below = h.center.dy < box.maxHeight / 2;
          final left = (h.center.dx - 190).clamp(16.0, box.maxWidth - 396);
          placed = Positioned(
            left: left,
            top: below ? h.bottom + 14 : null,
            bottom: below ? null : box.maxHeight - h.top + 14,
            child: card,
          );
        }
        return Stack(
          children: [
            Positioned.fill(
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () {},
                child: CustomPaint(painter: _Dim(h, Colors.black.withValues(alpha: .55))),
              ),
            ),
            placed,
          ],
        );
      },
    );
  }
}

class _Dim extends CustomPainter {
  _Dim(this.hole, this.color);

  final Rect? hole;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final all = Path()..addRect(Offset.zero & size);
    final h = hole;
    final path = h == null
        ? all
        : Path.combine(
            PathOperation.difference,
            all,
            Path()..addRRect(RRect.fromRectAndRadius(h, const Radius.circular(14))),
          );
    canvas.drawPath(path, Paint()..color = color);
    if (h != null) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(h, const Radius.circular(14)),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2
          ..color = Colors.white.withValues(alpha: .8),
      );
    }
  }

  @override
  bool shouldRepaint(_Dim old) => old.hole != hole || old.color != color;
}
