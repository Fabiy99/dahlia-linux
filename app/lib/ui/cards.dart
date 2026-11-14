// Pieces shared by the settings sections: a card with a title, hints,
// fields, check boxes, selects and the load-and-save pattern of a card.

import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';

import 'area.dart';
import 'theme.dart';
import 'widgets.dart';

class SettingsCard extends StatelessWidget {
  const SettingsCard(this.title, this.children, {super.key});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 14),
          ...children,
        ]),
      );
}

Widget cardHint(BuildContext context, String text) => Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Text(text, style: TextStyle(color: context.palette.muted, fontSize: 13, height: 1.4)),
    );

Widget cardField(TextEditingController t, String label, {String? hint, List<TextInputFormatter>? formatters, ValueChanged<String>? onChanged, bool obscure = false}) => Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: t,
        autocorrect: false,
        obscureText: obscure,
        enableSuggestions: !obscure,
        inputFormatters: formatters ?? [LengthLimitingTextInputFormatter(255)],
        // A kept secret's hint stays visible while the field is empty.
        decoration: InputDecoration(labelText: label, hintText: hint, floatingLabelBehavior: obscure && hint != null ? FloatingLabelBehavior.always : null),
        onChanged: onChanged,
      ),
    );

Widget cardCheck(String label, bool value, ValueChanged<bool>? onChanged) => CheckboxListTile(
      value: value,
      dense: true,
      contentPadding: EdgeInsets.zero,
      controlAffinity: ListTileControlAffinity.leading,
      title: Text(label),
      onChanged: onChanged == null ? null : (v) => onChanged(v ?? false),
    );

Widget cardSelect<T>(String label, T? value, List<(T, String)> items, ValueChanged<T>? onChanged) => Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: DropdownButtonFormField<T>(
        key: ValueKey('$label ${items.map((e) => e.$1).join(',')} $value'),
        initialValue: items.any((e) => e.$1 == value) ? value : null,
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        borderRadius: BorderRadius.circular(12),
        items: [for (final (v, text) in items) DropdownMenuItem(value: v, child: Text(text, overflow: TextOverflow.ellipsis))],
        onChanged: onChanged == null ? null : (v) => v == null ? null : onChanged(v),
      ),
    );

/// Shared by the cards: the first load, then reloads that keep the form.
mixin CardLoad<T extends StatefulWidget> on State<T>, AreaCalls<T> {
  Map? d;
  bool busy = false;

  String get path;

  /// Takes the server's values into the form fields.
  void fill(Map data);

  @override
  void initState() {
    super.initState();
    load(fill: true);
  }

  Future<void> load({bool fill = false}) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.get(path), m);
    if (r is! Map || !mounted) return;
    setState(() {
      d = r;
      if (fill) this.fill(r);
    });
  }

  /// Runs a change with the save button disabled, then loads again.
  Future<void> save(Future<Object?> Function() f, String done) async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(f, m);
    if (r != null && done.isNotEmpty) say(m, c.t(done));
    if (mounted) setState(() => busy = false);
    if (r != null) await load(fill: true);
  }
}
