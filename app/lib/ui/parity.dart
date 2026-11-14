// Parity area: SnapRAID state, sync and scrub, the disks, restoring a data
// disk, settings and the run history. Same API and rules as the dashboard's
// parity page.

import 'dart:async';

import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/parity.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class ParityPage extends StatefulWidget {
  const ParityPage(this.c, {super.key});

  final Controller c;

  @override
  State<ParityPage> createState() => _ParityPageState();
}

class _ParityPageState extends State<ParityPage> with AreaCalls {
  Map? d;
  String target = '';
  bool busy = false;
  Timer? _timer;
  final _fields = {for (final k in paritySettings) k: TextEditingController()};

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
    for (final t in _fields.values) {
      t.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    _timer?.cancel();
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.get('/api/parity'), m);
    if (r is! Map || !mounted) return;
    final first = d == null;
    setState(() {
      d = r;
      final targets = restoreTargets(r);
      if (!targets.contains(target)) target = targets.isEmpty ? '' : targets.first;
      // Fill the settings once; later loads keep what is being typed.
      if (first && r['settings'] is Map) {
        for (final e in _fields.entries) {
          e.value.text = '${(r['settings'] as Map)[e.key] ?? ''}';
        }
      }
    });
    // A running sync, scrub or repair can take hours: keep the page live.
    if (runningJob(r).isNotEmpty) _timer = Timer(const Duration(seconds: 5), _load);
  }

  /// Runs a start request, shows [done] and reloads.
  Future<void> _start(Future<Object?> Function() f, String done) async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(f, m);
    if (r != null) say(m, done);
    if (mounted) setState(() => busy = false);
    await _load();
  }

  Future<void> _sync({bool force = false}) => _start(() => s.post('/api/parity/sync', {if (force) 'force': true}), c.t('parity.sync_started'));

  Future<void> _scrub() => _start(() => s.post('/api/jobs/snapraid-scrub/start'), c.t('parity.scrub_started'));

  Future<void> _repair(Object? n) async {
    if (!await ask(c.t('parity.repair_confirm', {'n': n}), 'parity.repair')) return;
    await _start(() => s.post('/api/parity/fix', {'what': 'bad'}), c.t('parity.repair_started'));
  }

  Future<void> _force(Object? n) async {
    if (!await ask(c.t('parity.force_confirm', {'n': n}), 'parity.force', danger: true)) return;
    await _sync(force: true);
  }

  Future<void> _restore() async {
    final disk = '/mnt/$target';
    if (!await ask(c.t('parity.restore_confirm', {'disk': disk}), 'parity.restore', danger: true)) return;
    await _start(() => s.post('/api/parity/fix', {'what': target}), c.t('parity.restore_started', {'disk': disk}));
  }

  Future<void> _save() async {
    final m = ScaffoldMessenger.of(context);
    final body = settingsBody({for (final e in _fields.entries) e.key: e.value.text});
    if (body == null) return;
    final r = await call(() => s.post('/api/parity/settings', body), m);
    if (r != null) say(m, c.t('parity.saved'));
    await _load();
  }

  String _time(num? sec) => sec == null || sec == 0
      ? '-'
      : DateFormat.yMd(c.settings.lang).add_Hm().format(DateTime.fromMillisecondsSinceEpoch((sec * 1000).round()));

  Color _level(Palette p, String level) => switch (level) {
        'ok' => p.ok,
        'warn' => p.warn,
        'crit' => p.danger,
        _ => p.muted,
      };

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final half = grid.cols == 1 ? full : (full - 18) / 2;
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('nav.parity'), hint: c.t('parity.what'))),
      const SizedBox(height: 28),
      if (data == null)
        Busy(c.t('common.loading'))
      else ...[
        ..._notices(p, data),
        Wrap(spacing: 18, runSpacing: 18, children: [
          SizedBox(width: half, child: _stateCard(p, data)),
          SizedBox(width: half, child: _disksCard(p, data)),
          SizedBox(width: half, child: _restoreCard(p, data)),
          SizedBox(width: half, child: _settingsCard(p)),
          SizedBox(width: full, child: _historyCard(p, data)),
        ]),
      ],
    ]);
  }

  List<Widget> _notices(Palette p, Map data) {
    final list = notices(data);
    if (list.isEmpty) return const [];
    return [
      Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          for (final (i, (key, vars, action)) in list.indexed) ...[
            if (i > 0) const SizedBox(height: 12),
            Text(c.t(key, vars), style: const TextStyle(height: 1.45)),
            if (action.isNotEmpty) ...[
              const SizedBox(height: 8),
              Align(alignment: Alignment.centerLeft, child: switch (action) {
                'disks' => OutlinedButton(onPressed: () => c.go(Area.storage), child: Text(c.t('parity.to_disks'))),
                'repair' => FilledButton(onPressed: busy ? null : () => _repair(vars['n']), child: Text(c.t('parity.repair'))),
                _ => FilledButton(
                    style: FilledButton.styleFrom(backgroundColor: p.danger, foregroundColor: p.accentFg),
                    onPressed: busy ? null : () => _force(vars['n']),
                    child: Text(c.t('parity.force')),
                  ),
              }),
            ],
          ],
        ]),
      ),
      const SizedBox(height: 18),
    ];
  }

  Widget _card(String title, List<Widget> children) => Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 14),
          ...children,
        ]),
      );

  Widget _fact(Palette p, String key, Widget value) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 170, child: Text(c.t(key), style: TextStyle(color: p.muted))),
          Expanded(child: value),
        ]),
      );

  Widget _hint(Palette p, String text) => Text(text, style: TextStyle(color: p.muted, fontSize: 13, height: 1.4));

  Widget _stateCard(Palette p, Map data) {
    final status = data['status'] is Map ? data['status'] as Map : null;
    final runs = data['runs'] is Map ? data['runs'] as Map : const {};
    final (stateKey, level) = parityState(data);
    final checked = checkedPercent(status);
    final fix = runs['fix'];
    Widget text(String v) => Text(v, style: const TextStyle(height: 1.35));
    return _card(c.t('parity.state_title'), [
      _fact(p, 'parity.state', Align(alignment: Alignment.centerLeft, child: Tag(c.t('parity.$stateKey'), _level(p, level)))),
      if (status != null)
        _fact(p, 'parity.protected',
            text(c.t('parity.protected_value', {'n': status['files'], 'size': bytes((status['size'] as num?) ?? 0, c.t)}))),
      _fact(p, 'parity.last_sync', text(runText(runs['sync'], c.t, _time))),
      _fact(p, 'parity.last_scrub', text(runText(runs['scrub'], c.t, _time))),
      if (checked != null) ...[
        _fact(p, 'parity.checked', text(c.t('parity.checked_value', {'pct': checked}))),
        _fact(p, 'parity.oldest', text(_time(status!['scrub_oldest'] as num?))),
      ],
      if (status != null) _fact(p, 'parity.bad_blocks', text('${status['bad'] ?? 0}')),
      if (fix is Map) _fact(p, 'parity.last_fix', text('${fixTarget(fix['what'], c.t)}, ${runText(fix, c.t, _time)}')),
      const SizedBox(height: 6),
      _hint(p, c.t('parity.schedule_hint')),
      const SizedBox(height: 14),
      Wrap(spacing: 10, runSpacing: 10, children: [
        FilledButton(onPressed: !busy && canSync(data) ? _sync : null, child: Text(c.t('parity.sync'))),
        OutlinedButton(onPressed: !busy && canScrub(data) ? _scrub : null, child: Text(c.t('parity.scrub'))),
        OutlinedButton(onPressed: _load, child: Text(c.t('common.refresh'))),
      ]),
    ]);
  }

  Widget _disksCard(Palette p, Map data) {
    final rows = diskRows(data);
    return _card(c.t('parity.disks_title'), [
      if (rows.isEmpty) _hint(p, c.t('parity.no_disks')),
      for (final (key, vars, x, files) in rows)
        Container(
          width: double.infinity,
          margin: const EdgeInsets.only(bottom: 8),
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
          child: Wrap(spacing: 10, runSpacing: 6, alignment: WrapAlignment.spaceBetween, crossAxisAlignment: WrapCrossAlignment.center, children: [
            Wrap(spacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
              Text(c.t(key, vars), style: const TextStyle(fontWeight: FontWeight.w600)),
              Text('${x['mountpoint']}', style: TextStyle(color: p.muted, fontSize: 12, fontFamily: monoFont, fontFamilyFallback: monoFallback)),
            ]),
            Wrap(spacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
              Text(bytes((x['size'] as num?) ?? 0, c.t), style: TextStyle(color: p.muted, fontSize: 13)),
              if (files != '-') Text('${c.t('parity.files')}: $files', style: TextStyle(color: p.muted, fontSize: 13)),
              x['mounted'] == true ? Tag(c.t('parity.mounted'), p.ok) : Tag(c.t('parity.not_mounted'), p.danger),
            ]),
          ]),
        ),
    ]);
  }

  Widget _restoreCard(Palette p, Map data) {
    final targets = restoreTargets(data);
    return _card(c.t('parity.restore_title'), [
      for (final (i, key) in ['parity.restore_step1', 'parity.restore_step2', 'parity.restore_step3'].indexed)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(width: 22, child: Text('${i + 1}.', style: TextStyle(color: p.muted))),
            Expanded(child: Text(c.t(key), style: const TextStyle(height: 1.4))),
          ]),
        ),
      const SizedBox(height: 8),
      Row(children: [
        Expanded(
          child: DropdownButtonFormField<String>(
            key: ValueKey(targets.join(',')),
            initialValue: targets.contains(target) ? target : null,
            decoration: InputDecoration(labelText: c.t('parity.disk')),
            borderRadius: BorderRadius.circular(12),
            items: [for (final t in targets) DropdownMenuItem(value: t, child: Text('/mnt/$t'))],
            onChanged: (v) => setState(() => target = v ?? target),
          ),
        ),
        const SizedBox(width: 10),
        OutlinedButton(
          style: OutlinedButton.styleFrom(foregroundColor: p.danger),
          onPressed: !busy && canRestore(data) && target.isNotEmpty ? _restore : null,
          child: Text(c.t('parity.restore')),
        ),
      ]),
    ]);
  }

  Widget _settingsCard(Palette p) {
    final digits = [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(6)];
    final ok = settingsBody({for (final e in _fields.entries) e.key: e.value.text}) != null;
    Widget field(String key, String label) => Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: TextField(
            controller: _fields[key],
            inputFormatters: digits,
            decoration: InputDecoration(labelText: c.t(label)),
            onChanged: (_) => setState(() {}),
          ),
        );
    return _card(c.t('parity.settings_title'), [
      field('snapraid_delete_threshold', 'parity.threshold'),
      _hint(p, c.t('parity.threshold_hint')),
      const SizedBox(height: 14),
      field('scrub_percent', 'parity.scrub_percent'),
      field('scrub_older_than_days', 'parity.scrub_age'),
      FilledButton(onPressed: ok ? _save : null, child: Text(c.t('common.save'))),
    ]);
  }

  Widget _historyCard(Palette p, Map data) {
    final history = maps(data['history']).reversed.toList();
    TextStyle head = TextStyle(color: p.muted, fontSize: 12, fontWeight: FontWeight.w600);
    return _card(c.t('parity.history_title'), [
      if (history.isEmpty)
        _hint(p, c.t('parity.history_none'))
      else
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: DataTable(
            headingRowHeight: 36,
            dataRowMinHeight: 40,
            dataRowMaxHeight: 64,
            horizontalMargin: 0,
            columnSpacing: 24,
            columns: [
              for (final k in ['history_when', 'history_kind', 'history_duration', 'history_result', 'history_detail'])
                DataColumn(label: Text(c.t('parity.$k'), style: head)),
            ],
            rows: [
              for (final run in history)
                DataRow(cells: [
                  DataCell(Text(_time((run['end'] ?? run['start']) as num?))),
                  DataCell(Text(historyKind(run, c.t))),
                  DataCell(Text(run['end'] is num && run['start'] is num ? runDuration((run['end'] as num) - (run['start'] as num)) : '-')),
                  DataCell(Tag(tv(c, 'parity_result', run['result']), _level(p, resultLevel(run['result'])))),
                  DataCell(ConstrainedBox(constraints: const BoxConstraints(maxWidth: 420), child: Text(historyDetail(run, c.t)))),
                ]),
            ],
          ),
        ),
    ]);
  }
}
