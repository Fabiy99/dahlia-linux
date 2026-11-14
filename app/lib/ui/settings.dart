// Settings area: one page with sections, chosen by buttons below the title.
// Sections join here as they are built natively.

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import 'shell.dart';
import 'theme.dart';
import 'updates.dart';
import 'widgets.dart';
import 'area.dart';

enum SettingsSection { updates }

const _sections = [
  (SettingsSection.updates, 'updates.title'),
];

class SettingsPage extends StatefulWidget {
  const SettingsPage(this.c, {super.key});

  final Controller c;

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  SettingsSection section = SettingsSection.updates;

  Controller get c => widget.c;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('nav.account'))),
      if (_sections.length > 1) ...[
        const SizedBox(height: 20),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final (s, key) in _sections)
            ChoiceChip(
              label: Text(c.t(key)),
              selected: s == section,
              selectedColor: p.navActive,
              onSelected: (_) => setState(() => section = s),
            ),
        ]),
      ],
      const SizedBox(height: 28),
      switch (section) {
        SettingsSection.updates => UpdatesSection(c, key: const ValueKey(SettingsSection.updates)),
      },
    ]);
  }
}
