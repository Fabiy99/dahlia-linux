// Pieces shared by the areas after sign-in: server calls with messages,
// confirmation dialogs, labels and section headings.

import 'package:material_ui/material_ui.dart';

import '../src/apps.dart';
import '../src/controller.dart';
import '../src/server.dart';
import 'theme.dart';

/// Text of [prefix].[value], the value itself when the key is unknown.
String tv(Controller c, String prefix, Object? value) {
  final key = '$prefix.$value';
  return c.strings.has(key) ? c.t(key) : '${value ?? ''}';
}

/// Calls to the server from an area page and the messages about them.
mixin AreaCalls<T extends StatefulWidget> on State<T> {
  Controller get c;

  Server get s => c.server!;

  void say(ScaffoldMessengerState m, String text, {bool error = false}) {
    m
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(text),
        backgroundColor: error ? context.palette.danger : null,
      ));
  }

  /// Runs [f]; errors are shown as a message on [m], a lost session leads
  /// back to the login, a version change to the update page (Controller).
  Future<R?> call<R>(Future<R> Function() f, [ScaffoldMessengerState? m]) async {
    try {
      return await f();
    } on VersionMismatch {
      return null;
    } on ApiError catch (e) {
      if (e.status == 401) {
        await c.sessionEnded();
      } else if (m != null && mounted) {
        say(m, c.t(e.key), error: true);
      }
      return null;
    }
  }

  /// Yes/no question; true when [action] was pressed.
  Future<bool> ask(String text, String action, {bool danger = false}) async {
    final p = context.palette;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        content: SizedBox(width: 420, child: Text(text, style: const TextStyle(height: 1.45))),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text(c.t('common.cancel'))),
          FilledButton(
            autofocus: true,
            style: danger ? FilledButton.styleFrom(backgroundColor: p.danger, foregroundColor: p.accentFg) : null,
            onPressed: () => Navigator.pop(context, true),
            child: Text(c.t(action)),
          ),
        ],
      ),
    );
    return ok ?? false;
  }
}

/// Small rounded label, like the dashboard's badge().
class Tag extends StatelessWidget {
  const Tag(this.text, this.color, {super.key, this.tooltip});

  final String text;
  final Color color;
  final String? tooltip;

  @override
  Widget build(BuildContext context) {
    final tag = Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(color: color.withValues(alpha: .14), borderRadius: BorderRadius.circular(999)),
      child: Text(text, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
    );
    final t = tooltip;
    return t == null || t.isEmpty ? tag : Tooltip(message: t, child: tag);
  }
}

/// Large page title with a hint below.
class AreaTitle extends StatelessWidget {
  const AreaTitle(this.title, {super.key, this.hint = ''});

  final String title;
  final String hint;

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: const TextStyle(fontSize: 40, fontWeight: FontWeight.w700, letterSpacing: -1)),
        if (hint.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(hint, style: TextStyle(color: context.palette.muted, height: 1.45)),
        ],
      ]);
}

/// Section heading with optional buttons beside it.
class Heading extends StatelessWidget {
  const Heading(this.text, {super.key, this.side = const []});

  final String text;
  final List<Widget> side;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 34, bottom: 14),
        child: Wrap(spacing: 10, runSpacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text(text, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, letterSpacing: -.3)),
          const SizedBox(width: 6),
          ...side,
        ]),
      );
}

/// One button per location; the chosen one is highlighted.
class Places extends StatelessWidget {
  const Places({super.key, required this.c, required this.current, required this.available, required this.onPick, this.remotes = false, this.arrayFirst = false});

  final Controller c;
  final String current;
  final Map<String, bool> available;
  final ValueChanged<String> onPick;
  final bool remotes;

  /// The file manager lists the array first, the install dialog the SSD.
  final bool arrayFirst;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final roots = [
      if (arrayFirst) ('array', arrayRoot),
      ('system', systemRoot),
      if (!arrayFirst) ('array', arrayRoot),
      if (remotes) ('remotes', remotesRoot),
    ];
    return Wrap(spacing: 10, runSpacing: 10, children: [
      for (final (name, root) in roots)
        Material(
          color: root == current ? p.navActive : p.sub,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
            side: BorderSide(color: root == current ? p.accent : p.line),
          ),
          child: InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: available[root] == true ? () => onPick(root) : null,
            child: Opacity(
              opacity: available[root] == true ? 1 : .45,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(c.t('location.$name'), style: const TextStyle(fontWeight: FontWeight.w600)),
                  Text(root, style: TextStyle(color: p.muted, fontSize: 12, fontFamily: 'monospace')),
                ]),
              ),
            ),
          ),
        ),
    ]);
  }
}

