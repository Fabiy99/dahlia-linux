// Small building blocks shared by the pages.

import 'dart:ui';

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import 'bloom.dart';
import 'theme.dart';

/// Frosted card like the dashboard's tiles.
class Glass extends StatelessWidget {
  const Glass({super.key, required this.child, this.padding = const EdgeInsets.all(22), this.radius = 20});

  final Widget child;
  final EdgeInsets padding;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
        child: Container(
          padding: padding,
          decoration: BoxDecoration(
            color: p.glass,
            borderRadius: BorderRadius.circular(radius),
            border: Border.all(color: p.glassLine),
          ),
          child: child,
        ),
      ),
    );
  }
}

/// Fades and lifts its child in once, like the dashboard's "rise".
class Rise extends StatelessWidget {
  const Rise({super.key, required this.child, this.delay = 0});

  final Widget child;
  final int delay;

  @override
  Widget build(BuildContext context) => TweenAnimationBuilder<double>(
        tween: Tween(begin: 0, end: 1),
        duration: Duration(milliseconds: 450 + delay),
        curve: Interval(delay / (450 + delay), 1, curve: Curves.easeOutCubic),
        builder: (context, v, child) => Opacity(
          opacity: v,
          child: Transform.translate(offset: Offset(0, 12 * (1 - v)), child: child),
        ),
        child: child,
      );
}

class ErrorLine extends StatelessWidget {
  const ErrorLine(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) {
    final e = c.error;
    if (e == null) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: Text(c.t(e, c.errorVars), style: TextStyle(color: context.palette.danger)),
    );
  }
}

class LanguagePicker extends StatelessWidget {
  const LanguagePicker(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(Icons.translate_rounded, size: 18, color: p.muted),
      const SizedBox(width: 8),
      DropdownButton<String>(
        value: c.settings.lang,
        underline: const SizedBox.shrink(),
        borderRadius: BorderRadius.circular(12),
        items: const [
          DropdownMenuItem(value: 'en', child: Text('English')),
          DropdownMenuItem(value: 'de', child: Text('Deutsch')),
        ],
        onChanged: (v) => c.setLanguage(v ?? 'en'),
      ),
    ]);
  }
}

/// Centered card with logo and title, used before the home page.
class Panel extends StatelessWidget {
  const Panel({super.key, required this.c, required this.title, required this.children, this.width = 480});

  final Controller c;
  final String title;
  final List<Widget> children;
  final double width;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: width),
          child: Rise(
            child: Glass(
              padding: const EdgeInsets.fromLTRB(28, 26, 28, 28),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Row(children: [
                  const DahliaMark(size: 30),
                  const SizedBox(width: 12),
                  const Text('Dahlia', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                  const Spacer(),
                  LanguagePicker(c),
                ]),
                const SizedBox(height: 22),
                Text(title, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.4)),
                const SizedBox(height: 10),
                ...children,
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class Busy extends StatelessWidget {
  const Busy(this.text, {super.key});

  final String text;

  @override
  Widget build(BuildContext context) => Row(children: [
        const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2)),
        const SizedBox(width: 12),
        Expanded(child: Text(text, style: TextStyle(color: context.palette.muted))),
      ]);
}

/// Thin progress bar with rounded ends.
class Meter extends StatelessWidget {
  const Meter(this.value, {super.key, this.height = 8});

  final double value;
  final double height;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final v = value.clamp(0.0, 1.0);
    return ClipRRect(
      borderRadius: BorderRadius.circular(height),
      child: LinearProgressIndicator(
        value: v,
        minHeight: height,
        backgroundColor: p.track,
        color: v >= .9 ? p.warn : p.accent,
      ),
    );
  }
}
