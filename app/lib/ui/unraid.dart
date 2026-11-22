// Unraid takeover card on the disks page: the plan the migration helper
// brought, the version for "latest" images, the parity rebuild and the
// running import. Same API and words as the dashboard's renderUnraid.

import 'dart:async';

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/unraid.dart';
import 'area.dart';
import 'theme.dart';
import 'widgets.dart';

class UnraidCard extends StatefulWidget {
  const UnraidCard(this.c, {super.key});

  final Controller c;

  @override
  State<UnraidCard> createState() => _UnraidCardState();
}

class _UnraidCardState extends State<UnraidCard> with AreaCalls {
  Map? data;
  bool parity = false;
  final Map<String, TextEditingController> tags = {};
  Timer? _timer;

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _timer?.cancel();
    for (final t in tags.values) {
      t.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    _timer?.cancel();
    final r = await call(() => s.get('/api/unraid'));
    if (!mounted || r is! Map) return;
    setState(() => data = r);
    final job = r['job'];
    if (job is Map && job['state'] == 'running') _timer = Timer(const Duration(seconds: 3), _load);
  }

  Future<void> _start() async {
    final m = ScaffoldMessenger.of(context);
    final versions = {for (final e in tags.entries) e.key: e.value.text.trim()};
    if (versions.values.any((v) => !validTag(v))) {
      say(m, c.t('unraid.tag_placeholder'), error: true);
      return;
    }
    if (!await ask(c.t(parity ? 'unraid.ask_parity' : 'unraid.ask'), 'unraid.start')) return;
    final r = await call(() => s.post('/api/unraid/import', {'parity': parity, 'tags': versions}), m);
    if (r != null) say(m, c.t('unraid.started'));
    await _load();
  }

  Future<void> _dismiss() async {
    final m = ScaffoldMessenger.of(context);
    await call(() => s.post('/api/unraid/dismiss', {'dismissed': true}), m);
    await _load();
  }

  Widget _line(Palette p, String mark, String text, [Widget? extra]) {
    final color = switch (mark) { 'ok' => p.ok, 'bad' => p.danger, _ => p.accent };
    final sign = switch (mark) { 'ok' => '✓', 'bad' => '✗', _ => 'i' };
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 20, child: Text(sign, style: TextStyle(color: color, fontWeight: FontWeight.w700))),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(text, style: TextStyle(fontSize: 13.5, height: 1.4, color: mark == 'bad' ? p.danger : null)),
            if (extra != null) Padding(padding: const EdgeInsets.only(top: 4), child: extra),
          ]),
        ),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final d = data;
    if (!showUnraid(d)) return const SizedBox.shrink();
    final p = context.palette;
    final job = d!['job'] is Map ? d['job'] as Map : null;
    final plan = d['plan'] is Map ? d['plan'] as Map : null;
    final running = job?['state'] == 'running';
    final children = <Widget>[
      Text(c.t('unraid.title'), style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
      const SizedBox(height: 6),
      Text(c.t('unraid.intro'), style: TextStyle(color: p.muted, fontSize: 13, height: 1.45)),
      const SizedBox(height: 12),
    ];
    if (job != null) {
      children.add(Text(c.t('unraid.job_${job['state']}', {'step': c.t('unraid.step_${job['step']}')}),
          style: TextStyle(fontWeight: FontWeight.w600, color: job['state'] == 'failed' ? p.danger : null)));
      children.add(const SizedBox(height: 8));
      for (final i in (job['items'] as List? ?? const []).whereType<Map>()) {
        final err = i['error'] != null ? ' (${i['error']})' : '';
        children.add(_line(p, i['state'] == 'done' ? 'ok' : 'bad', '${i['name']}: ${c.t('unraid.item_${i['state']}')}$err'));
      }
    }
    if (plan != null) {
      for (final section in unraidSections) {
        final items = unraidItems(plan, section);
        if (items.isEmpty) continue;
        children.add(Padding(
          padding: const EdgeInsets.only(top: 10, bottom: 6),
          child: Text(c.t('unraid.section_$section'), style: const TextStyle(fontWeight: FontWeight.w600)),
        ));
        for (final item in items) {
          Widget? extra;
          if (item['state'] == 'needs_tag') {
            final ctl = tags.putIfAbsent('${item['name']}', TextEditingController.new);
            extra = SizedBox(
              width: 320,
              child: TextField(
                controller: ctl,
                decoration: InputDecoration(
                  labelText: c.t('unraid.tag', {'repo': item['repo']}),
                  hintText: c.t('unraid.tag_placeholder'),
                  isDense: true,
                ),
              ),
            );
          }
          children.add(_line(p, unraidMark('${item['state']}'), unraidLine(section, item, c.t), extra));
        }
      }
      final disks = unraidItems(plan, 'disks');
      if (disks.any((x) => x['state'] == 'confirm')) {
        children.add(CheckboxListTile(
          value: parity,
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          title: Text(c.t('unraid.parity_confirm')),
          subtitle: Text(c.t('unraid.parity_hint'), style: TextStyle(color: p.muted, fontSize: 12, height: 1.4)),
          onChanged: (v) => setState(() => parity = v ?? false),
        ));
      }
      final notTaken = (plan['not_taken'] as List? ?? const []);
      if (notTaken.isNotEmpty) {
        children.add(Padding(
          padding: const EdgeInsets.only(top: 10, bottom: 6),
          child: Text(c.t('unraid.section_not_taken'), style: const TextStyle(fontWeight: FontWeight.w600)),
        ));
        for (final k in notTaken) {
          children.add(_line(p, 'bad', c.t('unraid.not_$k')));
        }
      }
    }
    if (!running) {
      final again = job != null && job['state'] != 'running';
      children.add(const SizedBox(height: 12));
      children.add(Wrap(spacing: 10, runSpacing: 8, children: [
        FilledButton(onPressed: _start, child: Text(c.t(again ? 'unraid.again' : 'unraid.start'))),
        OutlinedButton(onPressed: _dismiss, child: Text(c.t('unraid.dismiss'))),
      ]));
    }
    return Padding(
      padding: const EdgeInsets.only(top: 18),
      child: Glass(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children)),
    );
  }
}
