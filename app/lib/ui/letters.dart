// Weekly letters and year reviews: the dialog behind "All letters" on the
// start screen tile, like #letter-dialog in the dashboard.

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/letters.dart';
import '../src/server.dart';
import 'theme.dart';

const letterIcons = {
  'storage': Icons.storage_rounded,
  'backup': Icons.backup_rounded,
  'updates': Icons.system_update_alt_rounded,
  'apps': Icons.apps_rounded,
  'photos': Icons.photo_library_rounded,
  'network': Icons.swap_vert_rounded,
  'cpu': Icons.memory_rounded,
  'uptime': Icons.power_settings_new_rounded,
  'energy': Icons.bolt_rounded,
  'notices': Icons.notifications_none_rounded,
};

/// The sentences of a letter, one per row with its icon.
class LetterLines extends StatelessWidget {
  const LetterLines(this.c, this.letter, {super.key, this.size = 14});

  final Controller c;
  final Map letter;
  final double size;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final (kind, text) in letterLines(letter, c.t, c.settings.lang))
          Padding(
            padding: const EdgeInsets.only(bottom: 9),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(letterIcons[kind] ?? Icons.circle_outlined, size: size + 4, color: p.accent),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(text, style: TextStyle(fontSize: size, height: 1.4)),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class LetterDialog extends StatefulWidget {
  const LetterDialog(this.c, {super.key, required this.saved, required this.first});

  final Controller c;

  /// Saved letters (id, kind, from, to), newest first.
  final List<Map> saved;

  /// Id shown first: the newest weekly letter, or "week".
  final String first;

  @override
  State<LetterDialog> createState() => _LetterDialogState();
}

class _LetterDialogState extends State<LetterDialog> {
  late String id = widget.first;
  Map? letter;

  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _show(id);
  }

  Future<void> _show(String next) async {
    setState(() {
      id = next;
      letter = null;
    });
    try {
      final r = await c.server!.get('/api/letters/$next');
      if (mounted && r is Map && id == next) setState(() => letter = r);
    } on ApiError {
      if (mounted) setState(() => letter = const {});
    } on VersionMismatch {
      return;
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final lang = c.settings.lang;
    final names = <String, String>{
      'week': c.t('letter.week_now'),
      'year': c.t('letter.year_now', {'year': DateTime.now().year}),
      for (final l in widget.saved) '${l['id']}': letterName(l, c.t, lang),
    };
    final l = letter;
    return AlertDialog(
      title: Row(
        children: [
          Expanded(child: Text(l == null || l.isEmpty ? c.t('letter.title') : letterTitle(l, c.t))),
          DropdownButton<String>(
            value: id,
            underline: const SizedBox(),
            borderRadius: BorderRadius.circular(12),
            items: [for (final e in names.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
            onChanged: (v) => v == null ? null : _show(v),
          ),
        ],
      ),
      content: SizedBox(
        width: 620,
        child: l == null
            ? const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: CircularProgressIndicator()),
              )
            : SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (l.isNotEmpty) ...[
                      Text(letterWhen(l, c.t, lang), style: TextStyle(color: p.muted)),
                      const SizedBox(height: 16),
                      LetterLines(c, l, size: 15),
                    ],
                  ],
                ),
              ),
      ),
      actions: [FilledButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.close')))],
    );
  }
}
