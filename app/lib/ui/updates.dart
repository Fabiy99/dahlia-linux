// Updates section of the settings: package updates with progress, automatic
// security updates with the restart settings, and the system snapshots
// (snapper) with rollback. Same API and rules as the dashboard's page.

import 'dart:async';

import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/server.dart';
import '../src/updates.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class UpdatesSection extends StatefulWidget {
  const UpdatesSection(this.c, {super.key});

  final Controller c;

  @override
  State<UpdatesSection> createState() => _UpdatesSectionState();
}

class _UpdatesSectionState extends State<UpdatesSection> with AreaCalls {
  /// Result of the last check: null before one, the packages after.
  List<Map>? packages;
  bool checking = false;
  Map? progress;
  Map? auto;
  Map? snaps;
  bool busy = false;
  Timer? _timer;

  bool autoEnabled = false;
  bool autoReboot = false;
  String rebootTime = '04:00';
  final _description = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _pending();
    _progress();
    _auto(fill: true);
    _snapshots();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _description.dispose();
    super.dispose();
  }

  bool get _running => progress?['state'] == 'running';

  /// Follows an update run. Dahlia's own packages restart the dashboard, so
  /// an unreachable server is expected while it runs; once the new version
  /// answers, the Controller moves on to the app update.
  Future<void> _progress({bool following = false}) async {
    _timer?.cancel();
    Object? r;
    try {
      r = await s.get('/api/updates/progress');
    } on VersionMismatch {
      return;
    } on ApiError catch (e) {
      if (e.status == 401) {
        await c.sessionEnded();
        return;
      }
      if (following && mounted) _timer = Timer(const Duration(seconds: 3), () => _progress(following: true));
      return;
    }
    if (r is! Map || !mounted) return;
    final wasRunning = following || _running;
    setState(() => progress = r as Map);
    if (_running) {
      _timer = Timer(const Duration(seconds: 2), () => _progress(following: true));
    } else if (wasRunning) {
      unawaited(c.checkPendingUpdates());
      final m = ScaffoldMessenger.of(context);
      say(m, c.t(r['state'] == 'done' ? 'updates.finished' : 'updates.failed'), error: r['state'] != 'done');
    }
  }

  /// What the package lists already offer, so installing needs no check first.
  Future<void> _pending() async {
    final r = await call(() => s.get('/api/updates/pending'));
    if (r is! Map || r['running'] == true || !mounted || packages != null) return;
    setState(() => packages = r['packages'] is List ? (r['packages'] as List).whereType<Map>().toList() : <Map>[]);
  }

  Future<void> _check() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => checking = true);
    final r = await call(() => s.post('/api/updates/check', null, const Duration(minutes: 3)), m);
    if (!mounted) return;
    setState(() {
      checking = false;
      if (r is Map) {
        packages = r['packages'] is List ? (r['packages'] as List).whereType<Map>().toList() : <Map>[];
        // A new check replaces the box of the previous run.
        progress = null;
      }
    });
    // The lists are fresh now; the banner follows them.
    if (r is Map) unawaited(c.checkPendingUpdates());
  }

  Future<void> _apply() async {
    if (!await ask(c.t('updates.confirm'), 'updates.apply')) return;
    if (!mounted) return;
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.post('/api/updates/apply'), m);
    if (r == null || !mounted) return;
    setState(() => packages = null);
    await _progress(following: true);
  }

  Future<void> _auto({bool fill = false}) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.get('/api/updates/auto'), m);
    if (r is! Map || !mounted) return;
    setState(() {
      auto = r;
      if (fill) {
        autoEnabled = r['enabled'] == true;
        autoReboot = r['reboot'] == true;
        rebootTime = '${r['reboot_time'] ?? '04:00'}';
      }
    });
  }

  Future<void> _saveAuto() async {
    final m = ScaffoldMessenger.of(context);
    final body = autoBody(enabled: autoEnabled, reboot: autoReboot, time: rebootTime);
    if (body == null) return;
    setState(() => busy = true);
    final r = await call(() => s.post('/api/updates/auto', body), m);
    if (r != null) say(m, c.t('auto.saved'));
    if (mounted) setState(() => busy = false);
    await _auto(fill: true);
  }

  Future<void> _reboot() async {
    final m = ScaffoldMessenger.of(context);
    if (!await ask(c.t('overview.confirm_reboot'), 'overview.reboot', danger: true)) return;
    final r = await call(() => s.post('/api/system/reboot'), m);
    if (r != null) say(m, c.t('overview.reboot_started'));
  }

  Future<void> _snapshots() async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.get('/api/snapshots'), m);
    if (r is! Map || !mounted) return;
    setState(() => snaps = r);
  }

  Future<void> _createSnapshot() async {
    final m = ScaffoldMessenger.of(context);
    final text = _description.text.trim();
    if (!validDescription(text)) return;
    setState(() => busy = true);
    final r = await call(() => s.post('/api/snapshots', {'description': text}, const Duration(minutes: 2)), m);
    if (r is Map) {
      _description.clear();
      say(m, c.t('snap.created', {'n': r['number']}));
    }
    if (mounted) setState(() => busy = false);
    await _snapshots();
  }

  Future<void> _rollback(Map snap, List<Map> all) async {
    final m = ScaffoldMessenger.of(context);
    final pre = preOf(snap, all);
    var text = c.t('snap.rollback_confirm', {'n': snap['number'], 'date': _date(snap['date'])});
    if (pre != null) text += '\n\n${c.t('snap.rollback_post_hint', {'pre': pre['number']})}';
    if (!await ask(text, 'snap.rollback_now', danger: true)) return;
    final r = await call(() => s.post('/api/snapshots/${snap['number']}/rollback'), m);
    if (r != null) say(m, c.t('snap.rollback_started'));
    await _snapshots();
  }

  Future<void> _deleteSnapshot(Map snap) async {
    final m = ScaffoldMessenger.of(context);
    if (!await ask(c.t('snap.delete_confirm', {'n': snap['number']}), 'snap.delete', danger: true)) return;
    await call(() => s.post('/api/snapshots/${snap['number']}/delete'), m);
    await _snapshots();
  }

  Future<void> _deleteOldRoot(String name) async {
    final m = ScaffoldMessenger.of(context);
    if (!await ask(c.t('snap.old_delete_confirm', {'name': name}), 'snap.delete', danger: true)) return;
    await call(() => s.post('/api/snapshots/old-root/delete', {'name': name}, const Duration(minutes: 2)), m);
    await _snapshots();
  }

  String _date(Object? v) {
    final t = v is num ? DateTime.fromMillisecondsSinceEpoch(v.toInt()) : localTime(v);
    return t == null ? '' : DateFormat.yMd(c.settings.lang).add_Hm().format(t);
  }

  Color _level(Palette p, String level) => switch (level) {
        'ok' => p.ok,
        'warn' => p.warn,
        'crit' => p.danger,
        'info' || 'accent' => p.accent,
        _ => p.muted,
      };

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final half = grid.cols == 1 ? full : (full - 18) / 2;
    final sn = snaps;
    return Wrap(spacing: 18, runSpacing: 18, children: [
      SizedBox(width: half, child: _updatesCard(p)),
      SizedBox(width: half, child: _autoCard(p)),
      if (sn != null && sn['available'] == true) SizedBox(width: full, child: _snapCard(p, sn)),
    ]);
  }

  Widget _card(String title, List<Widget> children) => Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 14),
          ...children,
        ]),
      );

  Widget _hint(Palette p, String text) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Text(text, style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
      );

  Widget _notice(Palette p, Color color, List<Widget> children) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 14),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: color.withValues(alpha: .08),
          border: Border.all(color: color.withValues(alpha: .35)),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
      );

  Widget _updatesCard(Palette p) {
    final list = packages;
    final pr = progress;
    final head = TextStyle(color: p.muted, fontSize: 12, fontWeight: FontWeight.w600);
    return _card(c.t('updates.title'), [
      _hint(p, c.t('updates.hint')),
      Wrap(spacing: 10, runSpacing: 10, children: [
        OutlinedButton(onPressed: checking || _running ? null : _check, child: Text(c.t('updates.check'))),
        FilledButton(onPressed: list != null && list.isNotEmpty && !_running ? _apply : null, child: Text(c.t('updates.apply'))),
      ]),
      const SizedBox(height: 14),
      if (checking) Busy(c.t('updates.checking')),
      if (pr != null && pr['state'] != 'none') _progressBox(p, pr),
      if (list != null && !checking) ...[
        Text(list.isEmpty ? c.t('updates.none') : c.t('updates.available', {'n': list.length})),
        if (list.isNotEmpty)
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              headingRowHeight: 36,
              dataRowMinHeight: 34,
              dataRowMaxHeight: 40,
              horizontalMargin: 0,
              columnSpacing: 20,
              columns: [for (final k in ['package', 'current', 'new', 'origin']) DataColumn(label: Text(c.t('updates.$k'), style: head))],
              rows: [
                for (final pkg in list)
                  DataRow(cells: [
                    DataCell(Text('${pkg['name']}', style: const TextStyle(fontWeight: FontWeight.w600))),
                    DataCell(Text('${pkg['current'] ?? ''}'.isEmpty ? '-' : '${pkg['current']}')),
                    DataCell(Text('${pkg['new'] ?? ''}')),
                    DataCell(Text('${pkg['origin'] ?? ''}', style: TextStyle(color: p.muted))),
                  ]),
              ],
            ),
          ),
      ],
    ]);
  }

  Widget _progressBox(Palette p, Map pr) {
    final (open, done) = splitPackages(pr);
    final color = _level(p, progressLevel(pr['state']));
    final percent = ((pr['percent'] as num?) ?? 0).clamp(0, 100);
    Widget names(String title, List<Map> pkgs, bool withStep) => Padding(
          padding: const EdgeInsets.only(top: 10),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('$title (${pkgs.length})', style: const TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text(
              [for (final x in pkgs) withStep ? '${x['name']} · ${tv(c, 'updates', 'step_${x['step']}')}' : '${x['name']}'].join('\n'),
              style: TextStyle(color: p.muted, fontSize: 12.5, height: 1.45),
            ),
          ]),
        );
    return _notice(p, color, [
      Text(tv(c, 'updates', 'state_${pr['state']}'), style: TextStyle(fontWeight: FontWeight.w600, color: color)),
      const SizedBox(height: 10),
      ClipRRect(
        borderRadius: BorderRadius.circular(99),
        child: LinearProgressIndicator(value: percent / 100, minHeight: 6, color: color, backgroundColor: p.track),
      ),
      const SizedBox(height: 6),
      Text(c.t('updates.progress', {'pct': percent, 'done': done.length, 'total': done.length + open.length}),
          style: TextStyle(color: p.muted, fontSize: 12.5)),
      if (_running && '${pr['last'] ?? ''}'.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Text('${pr['last']}',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12, color: p.muted)),
        ),
      if (open.isNotEmpty) names(c.t('updates.open'), open, true),
      if (done.isNotEmpty) names(c.t('updates.done'), done, false),
    ]);
  }

  Widget _autoCard(Palette p) {
    final a = auto;
    if (a == null) return _card(c.t('auto.title'), [Busy(c.t('common.loading'))]);
    final notice = rebootNotice(a['reboot_required'], c.t);
    final last = a['last'] is Map ? a['last'] as Map : null;
    Widget fact(String key, Widget value) => Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(width: 130, child: Text(c.t(key), style: TextStyle(color: p.muted))),
            Expanded(child: value),
          ]),
        );
    return _card(c.t('auto.title'), [
      if (notice != null)
        _notice(p, p.warn, [
          Text(notice, style: const TextStyle(height: 1.4)),
          const SizedBox(height: 10),
          OutlinedButton(onPressed: _reboot, child: Text(c.t('overview.reboot'))),
        ]),
      CheckboxListTile(
        value: autoEnabled,
        dense: true,
        contentPadding: EdgeInsets.zero,
        controlAffinity: ListTileControlAffinity.leading,
        title: Text(c.t('auto.enabled')),
        onChanged: (v) => setState(() => autoEnabled = v ?? false),
      ),
      _hint(p, c.t('auto.hint')),
      CheckboxListTile(
        value: autoReboot,
        dense: true,
        contentPadding: EdgeInsets.zero,
        controlAffinity: ListTileControlAffinity.leading,
        title: Text(c.t('auto.reboot')),
        onChanged: autoEnabled ? (v) => setState(() => autoReboot = v ?? false) : null,
      ),
      const SizedBox(height: 6),
      SizedBox(
        width: 200,
        child: DropdownButtonFormField<String>(
          key: ValueKey(rebootTime),
          initialValue: rebootTime,
          decoration: InputDecoration(labelText: c.t('auto.reboot_time')),
          borderRadius: BorderRadius.circular(12),
          menuMaxHeight: 320,
          items: [for (final v in rebootTimes(rebootTime)) DropdownMenuItem(value: v, child: Text(v))],
          onChanged: autoEnabled && autoReboot ? (v) => setState(() => rebootTime = v ?? rebootTime) : null,
        ),
      ),
      const SizedBox(height: 10),
      _hint(p, c.t('auto.reboot_hint')),
      FilledButton(onPressed: busy ? null : _saveAuto, child: Text(c.t('common.save'))),
      const SizedBox(height: 18),
      if (last == null)
        fact('auto.last', Text(c.t('auto.never')))
      else ...[
        fact(
          'auto.last',
          Wrap(spacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
            Tag(tv(c, 'auto.result', last['result']), _level(p, autoResultLevel(last['result']))),
            Text(_date(last['time'])),
          ]),
        ),
        if (last['packages'] is List && (last['packages'] as List).isNotEmpty)
          fact('auto.packages', SelectableText((last['packages'] as List).join(', '))),
        if (last['errors'] is List && (last['errors'] as List).isNotEmpty)
          fact('auto.errors', SelectableText((last['errors'] as List).join(' '), style: TextStyle(color: p.danger))),
      ],
    ]);
  }

  Widget _snapCard(Palette p, Map sn) {
    final list = snapshotsNewestFirst(sn);
    final olds = oldRoots(sn);
    final job = sn['job'];
    final rolling = job is Map && const ['active', 'activating'].contains(job['active']);
    final rollback = sn['rollback'] is Map ? sn['rollback'] as Map : null;
    final head = TextStyle(color: p.muted, fontSize: 12, fontWeight: FontWeight.w600);
    final ok = validDescription(_description.text);
    return _card(c.t('snap.title'), [
      _hint(p, c.t('snap.hint')),
      if (rolling)
        _notice(p, p.warn, [Text(c.t('snap.running'))])
      else if (rollback != null)
        _notice(p, p.accent, [
          Text(c.t('snap.rolled_back', {'n': rollback['snapshot'], 'time': _date(rollback['time'])})),
        ]),
      Wrap(spacing: 10, runSpacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
        SizedBox(
          width: 360,
          child: TextField(
            controller: _description,
            inputFormatters: [LengthLimitingTextInputFormatter(80)],
            decoration: InputDecoration(labelText: c.t('snap.description')),
            onChanged: (_) => setState(() {}),
            onSubmitted: (_) => ok && !busy ? _createSnapshot() : null,
          ),
        ),
        FilledButton(onPressed: ok && !busy ? _createSnapshot : null, child: Text(c.t('snap.create'))),
      ]),
      const SizedBox(height: 14),
      if (list.isEmpty)
        Text(c.t('snap.none'), style: TextStyle(color: p.muted))
      else
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: DataTable(
            headingRowHeight: 36,
            dataRowMinHeight: 44,
            dataRowMaxHeight: 60,
            horizontalMargin: 0,
            columnSpacing: 24,
            columns: [
              for (final k in ['number', 'date', 'description']) DataColumn(label: Text(c.t('snap.$k'), style: head)),
              const DataColumn(label: SizedBox()),
            ],
            rows: [
              for (final snap in list)
                DataRow(cells: [
                  DataCell(Text('${snap['number']}')),
                  DataCell(Text(_date(snap['date']))),
                  DataCell(ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 520),
                    child: Wrap(spacing: 8, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [
                      Text('${snap['description'] ?? ''}'),
                      for (final (key, vars, level) in snapshotTags(snap)) Tag(c.t(key, vars), _level(p, level)),
                    ]),
                  )),
                  DataCell(Row(mainAxisSize: MainAxisSize.min, children: [
                    IconButton(
                      tooltip: c.t('snap.rollback'),
                      onPressed: rolling ? null : () => _rollback(snap, list),
                      icon: const Icon(Icons.settings_backup_restore_rounded),
                    ),
                    IconButton(
                      tooltip: c.t('snap.delete'),
                      color: p.danger,
                      onPressed: rolling ? null : () => _deleteSnapshot(snap),
                      icon: const Icon(Icons.delete_outline_rounded),
                    ),
                  ])),
                ]),
            ],
          ),
        ),
      if (olds.isNotEmpty) ...[
        const SizedBox(height: 18),
        Text(c.t('snap.old_title'), style: const TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 6),
        _hint(p, c.t('snap.old_hint')),
        for (final name in olds)
          Row(children: [
            Expanded(
              child: SelectableText(name, style: const TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 13)),
            ),
            IconButton(
              tooltip: c.t('snap.delete'),
              color: p.danger,
              onPressed: () => _deleteOldRoot(name),
              icon: const Icon(Icons.delete_outline_rounded),
            ),
          ]),
      ],
    ]);
  }
}
