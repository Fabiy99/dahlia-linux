// Disks area: pools, one card per physical disk with SMART, self-tests,
// LED, burn-in and its filesystems (add, remove, import, format), the
// encryption card and the storage settings. Same API and rules as the
// dashboard's storage page.

import 'dart:async';

import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/server.dart';
import '../src/storage.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class StoragePage extends StatefulWidget {
  const StoragePage(this.c, {super.key});

  final Controller c;

  @override
  State<StoragePage> createState() => _StoragePageState();
}

class _StoragePageState extends State<StoragePage> with AreaCalls {
  List<Map> disks = const [];
  List<Map> mounts = const [];

  /// SMART state per disk path; filled in later, SMART is slow on many disks.
  Map<String, Map> health = const {};
  Map<String, Map> burnin = const {};
  Map? import_;
  Map crypt = const {'auto_unlock': false, 'disks': []};
  bool loaded = false;
  bool _importWasRunning = false;

  /// Disk whose LED blinks and until when.
  String locating = '';
  DateTime locateUntil = DateTime(0);

  Timer? _timer;
  Timer? _importTimer;

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _importTimer?.cancel();
    super.dispose();
  }

  List<Map> _list(Object? v) => v is List ? v.whereType<Map>().toList() : <Map>[];

  Future<void> _load() async {
    final m = ScaffoldMessenger.of(context);
    final data = await call(() => s.get('/api/storage'), m);
    final stats = await call(() => s.get('/api/stats'));
    final tests = await call(() => s.get('/api/disks/burnin'));
    final enc = await call(() => s.get('/api/storage/encryption'));
    if (!mounted) return;
    setState(() {
      if (data is Map) disks = _list(data['disks']);
      if (stats is Map) mounts = _list(stats['filesystems']);
      if (tests is Map) burnin = {for (final t in _list(tests['tests'])) '${t['device']}': t};
      if (enc is Map) crypt = enc;
      loaded = data is Map || loaded;
    });
    unawaited(_pollImport());
    final hw = await call(() => s.get('/api/hardware'));
    if (hw is Map && mounted) setState(() => health = {for (final d in _list(hw['disks'])) '${d['path']}': d});
  }

  Future<void> _pollImport() async {
    _importTimer?.cancel();
    final r = await call(() => s.get('/api/storage/import'));
    if (!mounted || r is! Map) return;
    final job = r['import'] is Map ? r['import'] as Map : null;
    final running = job != null && importRunning.contains(job['phase']);
    setState(() => import_ = job);
    if (_importWasRunning && !running) {
      _importWasRunning = false;
      final done = job?['phase'] == 'done';
      say(ScaffoldMessenger.of(context), c.t(done ? 'import.finished' : 'import.failed_short'), error: !done);
      await _load();
      return;
    }
    _importWasRunning = running;
    if (running) _importTimer = Timer(const Duration(seconds: 3), _pollImport);
  }

  /// Runs a change, says [done] on success and reloads.
  Future<void> _change(Future<Object?> Function() f, [String? done]) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(f, m);
    if (r != null && done != null) say(m, done);
    await _load();
  }

  Future<void> _add(Map d, String role) async {
    final path = '${d['path']}';
    if (!await ask(c.t('storage.confirm_add', {'path': path, 'role': c.t('role.$role')}), 'storage.add')) return;
    await _change(() => s.post('/api/storage/add', {'uuid': d['uuid'], 'role': role}), c.t('storage.added', {'path': path}));
  }

  Future<void> _remove(Map d) async {
    final m = ScaffoldMessenger.of(context);
    final path = '${d['path']}';
    if (!await ask(c.t('storage.confirm_remove', {'path': path}), 'storage.remove', danger: true)) return;
    try {
      await s.post('/api/storage/remove', {'uuid': d['uuid']});
    } on ApiError catch (e) {
      // A cache that still holds files needs an explicit second yes.
      if (!needsForce(e.status, e.detail)) {
        if (e.status == 401) {
          await c.sessionEnded();
        } else if (mounted) {
          say(m, c.t(e.key), error: true);
        }
        return;
      }
      if (!mounted || !await ask(c.t('storage.confirm_force', {'detail': e.detail}), 'storage.remove', danger: true)) {
        return;
      }
      final r = await call(() => s.post('/api/storage/remove', {'uuid': d['uuid'], 'force': true}), m);
      if (r == null) return;
    } on VersionMismatch {
      return;
    }
    say(m, c.t('storage.removed', {'path': path}));
    await _load();
  }

  Future<void> _locate(String disk) async {
    final m = ScaffoldMessenger.of(context);
    if (locating == disk && DateTime.now().isBefore(locateUntil)) {
      await call(() => s.post('/api/disks/locate/stop'), m);
      setState(() => locating = '');
      return;
    }
    final r = await call(() => s.post('/api/disks/locate', {'device': disk}), m);
    if (r is! Map || !mounted) return;
    final seconds = (r['seconds'] as num?)?.toInt() ?? 120;
    setState(() {
      locating = disk;
      locateUntil = DateTime.now().add(Duration(seconds: seconds));
    });
    say(m, c.t('disks.locating', {'path': disk, 'n': (seconds / 60).round()}));
  }

  Future<void> _smart(String disk, String kind) => _change(
        () => s.post('/api/disks/smart-test', {'device': disk, 'kind': kind}),
        c.t(kind == 'abort' ? 'smart.test_aborted' : 'smart.test_started',
            {'path': disk, 'kind': tv(c, 'smart_kind', kind)}),
      );

  Future<void> _burnin(Map d) async {
    final disk = '${d['disk']}';
    final confirm = await showDialog<String>(
      context: context,
      builder: (context) => _TypeToConfirm(
        c: c,
        title: c.t('burnin.title', {'path': disk}),
        warning: c.t('burnin.warning', {'path': disk, 'model': d['model'] ?? '-', 'size': bytes(d['size'] as num? ?? 0, c.t)}),
        name: shortName(disk),
        action: c.t('burnin.start_now'),
      ),
    );
    if (confirm == null) return;
    await _change(() => s.post('/api/disks/burnin/start', {'device': disk, 'confirm': confirm}),
        c.t('burnin.started', {'path': disk}));
  }

  Future<void> _burninStop(String disk) async {
    if (!await ask(c.t('burnin.stop_confirm', {'path': disk}), 'burnin.stop', danger: true)) return;
    await _change(() => s.post('/api/disks/burnin/stop', {'device': disk}));
  }

  Future<void> _format(Map d) async {
    final disk = '${d['disk']}';
    final body = await showDialog<Map<String, Object>>(
      context: context,
      builder: (context) => _FormatDialog(c: c, d: d, firstEncrypted: _list(crypt['disks']).isEmpty),
    );
    if (body == null) return;
    await _change(() => s.post('/api/storage/format', {'device': disk, ...body}), c.t('storage.formatted', {'path': disk}));
  }

  Future<void> _import(Map d) async {
    final m = ScaffoldMessenger.of(context);
    final target = await showDialog<String>(context: context, builder: (context) => _ImportDialog(c: c, d: d));
    if (target == null) return;
    final r = await call(() => s.post('/api/storage/import', {'uuid': d['uuid'], 'target': target}), m);
    if (r == null) return;
    _importWasRunning = true;
    await _pollImport();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final width = MediaQuery.of(context).size.width;
    final grid = pageGrid(width);
    final groups = groupDisks(disks);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final cardWidth = grid.cols == 3 ? (full - 18) / 2 : full;
    final job = import_;
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('storage.title'), hint: c.t('storage.hint'))),
      const SizedBox(height: 28),
      Wrap(spacing: 18, runSpacing: 18, children: [
        for (final (i, (mp, name, hint)) in pools.indexed)
          SizedBox(width: grid.unit, child: Rise(delay: 60 * i, child: _pool(p, mp, name, hint))),
      ]),
      if (job != null) ...[const SizedBox(height: 18), _importBox(p, job)],
      if (_list(crypt['disks']).isNotEmpty) ...[
        const SizedBox(height: 18),
        _CryptCard(c: c, crypt: crypt, reload: _load, area: this),
      ],
      Heading(c.t('nav.storage'), side: [
        OutlinedButton(onPressed: _load, child: Text(c.t('common.refresh'))),
      ]),
      if (!loaded) Busy(c.t('common.loading')),
      if (loaded && groups.isEmpty) Text(c.t('storage.none'), style: TextStyle(color: p.muted)),
      Wrap(spacing: 18, runSpacing: 18, children: [
        for (final (disk, parts) in groups) SizedBox(width: cardWidth, child: _diskCard(p, disk, parts)),
      ]),
      Heading(c.t('storage.settings_title')),
      SizedBox(width: cardWidth, child: _SettingsCard(c: c, area: this)),
    ]);
  }

  Widget _pool(Palette p, String mp, String name, String hint) {
    final f = mounts.where((m) => m['mountpoint'] == mp).firstOrNull;
    final used = (f?['used'] as num?) ?? 0;
    final size = (f?['size'] as num?) ?? 0;
    final pct = percent(used, size);
    return Glass(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(c.t(name), style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
          const Spacer(),
          Text(mp, style: TextStyle(color: p.muted, fontSize: 12, fontFamily: 'monospace')),
        ]),
        const SizedBox(height: 14),
        if (f != null) ...[
          Meter(pct / 100, height: 10),
          const SizedBox(height: 8),
          Text(c.t('overview.used_of', {'used': bytes(used, c.t), 'size': bytes(size, c.t), 'pct': pct}),
              style: TextStyle(color: p.muted, fontSize: 13)),
        ] else
          Text(c.t('storage.pool_missing'), style: TextStyle(color: p.muted)),
        const SizedBox(height: 10),
        Text(c.t(hint), style: TextStyle(color: p.muted, fontSize: 12, height: 1.4)),
      ]),
    );
  }

  Widget _importBox(Palette p, Map job) {
    final running = importRunning.contains(job['phase']);
    final failed = job['phase'] == 'failed';
    final pct = (job['percent'] as num?) ?? 0;
    return Glass(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(c.t('import.${job['phase']}', {'path': job['device'], 'target': job['target']}),
            style: TextStyle(fontWeight: FontWeight.w600, color: failed ? p.danger : null)),
        if (running) ...[
          const SizedBox(height: 10),
          Meter(pct / 100),
          const SizedBox(height: 6),
          Text(
              c.t('import.progress', {
                'done': bytes((job['bytes'] as num?) ?? 0, c.t),
                'total': bytes((job['total'] as num?) ?? 0, c.t),
                'pct': pct,
              }),
              style: TextStyle(color: p.muted, fontSize: 13)),
        ],
        if (failed && job['error'] is String) ...[
          const SizedBox(height: 6),
          Text('${job['error']}', style: TextStyle(color: p.danger, fontSize: 13)),
        ],
      ]),
    );
  }

  Color _level(Palette p, String level) => switch (level) {
        'crit' => p.danger,
        'warn' => p.warn,
        'ok' => p.ok,
        _ => p.muted,
      };

  Widget _diskCard(Palette p, String disk, List<Map> parts) {
    final first = parts.first;
    final model = [
      if (first['model'] != null) '${first['model']}',
      if (first['serial'] != null) c.t('storage.serial', {'serial': first['serial']}),
    ].join(' · ');
    final status = diskStatus(parts);
    final h = health[disk];
    final temp = h?['temperature'] as num?;
    final hs = '${h?['health'] ?? ''}';
    final warnings = h?['warnings'] is Map
        ? (h!['warnings'] as Map).entries.map((e) => '${tv(c, 'smart', e.key)}: ${e.value}').join('\n')
        : '';
    final test = burnin[disk];
    return Glass(
      padding: const EdgeInsets.all(18),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(color: p.accentSoft, borderRadius: BorderRadius.circular(12)),
            child: Icon(Icons.storage_rounded, color: p.accent),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(shortName(disk), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
              Text(model.isEmpty ? c.t('storage.unknown_model') : model,
                  maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: p.muted, fontSize: 13)),
              const SizedBox(height: 6),
              Wrap(spacing: 6, runSpacing: 6, children: [
                if (status != null)
                  Tag(c.t('disk_status.$status'), switch (status) { 'registered' => p.ok, 'system' => p.accent, _ => p.muted }),
                if (temp != null)
                  Tag('${temp.round()} °C', _level(p, tempLevel(temp, h?['rotational'] == true))),
                if (h != null)
                  Tag(tv(c, 'health', hs), _level(p, switch (hs) { 'passed' => 'ok', 'warning' => 'warn', 'failed' => 'crit', _ => '' }),
                      tooltip: [
                        warnings,
                        if (h['power_on_hours'] != null) c.t('storage.power_on', {'h': h['power_on_hours']}),
                      ].where((x) => x.isNotEmpty).join('\n')),
              ]),
            ]),
          ),
        ]),
        if (h != null && hs != 'unavailable' && hs != 'unknown') _smartRow(p, disk, h),
        if (test != null)
          _burninRow(p, disk, test)
        else if (offerBurnin(disk, parts))
          _row(p, [Text(c.t('burnin.offer'), style: TextStyle(color: p.muted, fontSize: 13))], [
            OutlinedButton(
              style: OutlinedButton.styleFrom(foregroundColor: p.danger),
              onPressed: () => _burnin(first),
              child: Text(c.t('burnin.start')),
            ),
          ]),
        for (final d in parts) _part(p, d),
      ]),
    );
  }

  Widget _row(Palette p, List<Widget> text, List<Widget> ops) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(top: 12),
        padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
        decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
        child: Wrap(
          spacing: 10,
          runSpacing: 6,
          alignment: WrapAlignment.spaceBetween,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Wrap(spacing: 8, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: text),
            Wrap(spacing: 6, runSpacing: 6, children: ops),
          ],
        ),
      );

  Widget _small(String key, VoidCallback onPressed, {Color? color}) => TextButton(
        style: TextButton.styleFrom(foregroundColor: color, minimumSize: const Size(0, 36)),
        onPressed: onPressed,
        child: Text(c.t(key)),
      );

  Widget _smartRow(Palette p, String disk, Map h) {
    final st = h['self_test'] is Map ? h['self_test'] as Map : const {};
    final last = st['last'] is Map ? st['last'] as Map : null;
    final running = st['running'] == true;
    final text = running
        ? (st['remaining'] != null
            ? c.t('smart.test_running', {'n': st['remaining']})
            : c.t('smart.test_running_unknown'))
        : last != null
            ? c.t('smart.test_last', {'kind': tv(c, 'smart_kind', last['kind']), 'h': last['hours'] ?? '?'})
            : c.t('smart.test_none');
    final blinking = locating == disk && DateTime.now().isBefore(locateUntil);
    return _row(p, [
      Text(text, style: const TextStyle(fontSize: 13)),
      if (!running && last != null)
        Tag(tv(c, 'smart_result', last['result']),
            _level(p, switch ('${last['result']}') { 'passed' => 'ok', 'failed' => 'crit', 'aborted' => 'warn', _ => '' })),
    ], [
      if (running)
        _small('smart.test_abort', () => _smart(disk, 'abort'))
      else ...[
        _small('smart.test_short', () => _smart(disk, 'short')),
        _small('smart.test_long', () => _smart(disk, 'long')),
      ],
      _small(blinking ? 'disks.locate_stop' : 'disks.locate', () => _locate(disk)),
    ]);
  }

  Widget _burninRow(Palette p, String disk, Map test) {
    final result = '${test['result']}';
    final running = result == 'running';
    final parts = <String>[
      if (running)
        c.t('burnin.progress', {'pass': test['pass'], 'percent': ((test['percent'] as num?) ?? 0).toStringAsFixed(1)}),
      if (!running || (test['bad_blocks'] ?? 0) != 0) c.t('burnin.bad_blocks', {'n': test['bad_blocks'] ?? 0}),
      if (test['smart_new'] is Map && (test['smart_new'] as Map).isNotEmpty)
        c.t('burnin.smart_new', {
          'list': (test['smart_new'] as Map).entries.map((e) => '${tv(c, 'smart', e.key)}: ${e.value}').join(', '),
        }),
    ];
    return _row(p, [
      Tag(tv(c, 'burnin_result', result),
          switch (result) { 'running' => p.accent, 'passed' => p.ok, 'failed' => p.danger, 'stopped' => p.warn, _ => p.muted }),
      Text(parts.join(' · '), style: const TextStyle(fontSize: 13)),
    ], [
      if (running)
        _small('burnin.stop', () => _burninStop(disk), color: p.danger)
      else
        _small('burnin.clear', () => _change(() => s.post('/api/disks/burnin/clear', {'device': disk}))),
    ]);
  }

  Widget _part(Palette p, Map d) {
    final m = mountOf(d, mounts);
    final status = '${d['status']}';
    final kind = switch (status) {
      'registered' => p.ok,
      'candidate' || 'external-only' => p.accent,
      'system' => p.accent,
      'mounted' || 'foreign' || 'locked' => p.warn,
      _ => p.muted,
    };
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 12),
      padding: const EdgeInsets.only(top: 12),
      decoration: BoxDecoration(border: Border(top: BorderSide(color: p.line))),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(spacing: 10, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text('${d['path']}', style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w600, fontSize: 13)),
          Text('${d['fstype'] ?? c.t('storage.no_fs')}', style: TextStyle(color: p.muted, fontSize: 13)),
          Text(bytes((d['size'] as num?) ?? 0, c.t), style: TextStyle(color: p.muted, fontSize: 13)),
          Tag(tv(c, 'disk_status', status), kind, tooltip: '${d['reason'] ?? ''}'),
          if (d['imported'] == true) Tag(c.t('storage.imported'), p.ok),
          if (d['luks'] != null) Tag(c.t('crypt.badge'), p.accent),
        ]),
        if (m != null) ...[
          const SizedBox(height: 10),
          Text('${fsLabel('${m['mountpoint']}', c.t)} · ${m['mountpoint']}', style: const TextStyle(fontSize: 13)),
          const SizedBox(height: 6),
          Meter(percent((m['used'] as num?) ?? 0, (m['size'] as num?) ?? 0) / 100),
          const SizedBox(height: 4),
          Text(
              c.t('overview.used_of', {
                'used': bytes((m['used'] as num?) ?? 0, c.t),
                'size': bytes((m['size'] as num?) ?? 0, c.t),
                'pct': percent((m['used'] as num?) ?? 0, (m['size'] as num?) ?? 0),
              }),
              style: TextStyle(color: p.muted, fontSize: 12)),
        ],
        if (canAdd(d) || canRemove(d) || canImport(d) || canFormat(d)) ...[
          const SizedBox(height: 10),
          Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
            if (canAdd(d)) _AddRole(c: c, d: d, onAdd: (role) => _add(d, role)),
            if (canImport(d)) OutlinedButton(onPressed: () => _import(d), child: Text(c.t('storage.import'))),
            if (canFormat(d))
              OutlinedButton(
                style: OutlinedButton.styleFrom(foregroundColor: p.danger),
                onPressed: () => _format(d),
                child: Text(c.t('storage.format')),
              ),
            if (canRemove(d))
              OutlinedButton(
                style: OutlinedButton.styleFrom(foregroundColor: p.danger),
                onPressed: () => _remove(d),
                child: Text(c.t('storage.remove')),
              ),
          ]),
        ],
      ]),
    );
  }
}

/// Role picker with the add button.
class _AddRole extends StatefulWidget {
  const _AddRole({required this.c, required this.d, required this.onAdd});

  final Controller c;
  final Map d;
  final ValueChanged<String> onAdd;

  @override
  State<_AddRole> createState() => _AddRoleState();
}

class _AddRoleState extends State<_AddRole> {
  late String role = rolesFor(widget.d).first;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final c = widget.c;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Container(
        height: 46,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12), border: Border.all(color: p.line)),
        child: DropdownButtonHideUnderline(
          child: DropdownButton<String>(
            value: role,
            borderRadius: BorderRadius.circular(12),
            items: [for (final r in rolesFor(widget.d)) DropdownMenuItem(value: r, child: Text(c.t('role.$r')))],
            onChanged: (v) => setState(() => role = v ?? role),
          ),
        ),
      ),
      const SizedBox(width: 8),
      FilledButton(onPressed: () => widget.onAdd(role), child: Text(c.t('storage.add'))),
    ]);
  }
}

/// Warning plus "type the disk name"; pops with the typed name.
class _TypeToConfirm extends StatefulWidget {
  const _TypeToConfirm({required this.c, required this.title, required this.warning, required this.name, required this.action});

  final Controller c;
  final String title, warning, name, action;

  @override
  State<_TypeToConfirm> createState() => _TypeToConfirmState();
}

class _TypeToConfirmState extends State<_TypeToConfirm> {
  final _confirm = TextEditingController();

  @override
  void dispose() {
    _confirm.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final c = widget.c;
    final ok = _confirm.text.trim() == widget.name;
    return AlertDialog(
      title: Text(widget.title),
      content: SizedBox(
        width: 480,
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(widget.warning, style: TextStyle(color: p.muted, height: 1.45)),
          const SizedBox(height: 16),
          TextField(
            controller: _confirm,
            autofocus: true,
            decoration: InputDecoration(labelText: c.t('storage.format_confirm', {'name': widget.name})),
            onChanged: (_) => setState(() {}),
          ),
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: p.danger, foregroundColor: p.accentFg),
          onPressed: ok ? () => Navigator.pop(context, _confirm.text.trim()) : null,
          child: Text(widget.action),
        ),
      ],
    );
  }
}

/// Filesystem, optional encryption and confirmation; pops with the body
/// fields beside the device.
class _FormatDialog extends StatefulWidget {
  const _FormatDialog({required this.c, required this.d, required this.firstEncrypted});

  final Controller c;
  final Map d;

  /// No encrypted disk yet: the passphrase is new and asked twice.
  final bool firstEncrypted;

  @override
  State<_FormatDialog> createState() => _FormatDialogState();
}

class _FormatDialogState extends State<_FormatDialog> {
  String fs = filesystems.first;
  bool encrypt = false;
  final _pass = TextEditingController();
  final _repeat = TextEditingController();
  final _confirm = TextEditingController();

  @override
  void dispose() {
    _pass.dispose();
    _repeat.dispose();
    _confirm.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final c = widget.c;
    final d = widget.d;
    final disk = '${d['disk']}';
    final name = shortName(disk);
    final passOk = !encrypt || (_pass.text.length >= 8 && (!widget.firstEncrypted || _pass.text == _repeat.text));
    final mismatch = encrypt && widget.firstEncrypted && _repeat.text.isNotEmpty && _pass.text != _repeat.text;
    final ok = _confirm.text.trim() == name && passOk;
    return AlertDialog(
      title: Text(c.t('storage.format_title', {'path': disk})),
      content: SizedBox(
        width: 500,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(
                c.t(d['imported'] == true ? 'storage.format_warning_imported' : 'storage.format_warning',
                    {'path': disk, 'model': d['model'] ?? '-', 'size': bytes((d['size'] as num?) ?? 0, c.t)}),
                style: TextStyle(color: p.muted, height: 1.45)),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              initialValue: fs,
              decoration: InputDecoration(labelText: c.t('storage.format_fs')),
              borderRadius: BorderRadius.circular(12),
              items: [for (final f in filesystems) DropdownMenuItem(value: f, child: Text(c.t('storage.fs_$f')))],
              onChanged: (v) => setState(() => fs = v ?? fs),
            ),
            CheckboxListTile(
              value: encrypt,
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              title: Text(c.t('crypt.format_encrypt')),
              onChanged: (v) => setState(() => encrypt = v ?? false),
            ),
            if (encrypt) ...[
              Text(c.t(widget.firstEncrypted ? 'crypt.format_hint_first' : 'crypt.format_hint_more'),
                  style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
              const SizedBox(height: 10),
              TextField(
                controller: _pass,
                obscureText: true,
                inputFormatters: [LengthLimitingTextInputFormatter(512)],
                decoration: InputDecoration(labelText: c.t('crypt.passphrase')),
                onChanged: (_) => setState(() {}),
              ),
              if (widget.firstEncrypted) ...[
                const SizedBox(height: 10),
                TextField(
                  controller: _repeat,
                  obscureText: true,
                  inputFormatters: [LengthLimitingTextInputFormatter(512)],
                  decoration: InputDecoration(
                    labelText: c.t('crypt.repeat'),
                    errorText: mismatch ? c.t('crypt.mismatch') : null,
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ],
            ],
            const SizedBox(height: 14),
            TextField(
              controller: _confirm,
              decoration: InputDecoration(labelText: c.t('storage.format_confirm', {'name': name})),
              onChanged: (_) => setState(() {}),
            ),
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: p.danger, foregroundColor: p.accentFg),
          onPressed: ok
              ? () => Navigator.pop<Map<String, Object>>(context, {
                    'filesystem': fs,
                    'confirm': _confirm.text.trim(),
                    if (encrypt) 'encrypt': true,
                    if (encrypt) 'passphrase': _pass.text,
                  })
              : null,
          child: Text(c.t('storage.format_now')),
        ),
      ],
    );
  }
}

/// Name of the new share for the imported data; pops with it.
class _ImportDialog extends StatefulWidget {
  const _ImportDialog({required this.c, required this.d});

  final Controller c;
  final Map d;

  @override
  State<_ImportDialog> createState() => _ImportDialogState();
}

class _ImportDialogState extends State<_ImportDialog> {
  late final _target = TextEditingController(text: importTarget(widget.d));

  @override
  void dispose() {
    _target.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final c = widget.c;
    final d = widget.d;
    final ok = shareNameRe.hasMatch(_target.text.trim());
    return AlertDialog(
      title: Text(c.t('storage.import_title', {'path': d['path']})),
      content: SizedBox(
        width: 500,
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c.t('storage.import_text', {'path': d['path'], 'fs': d['fstype'], 'size': bytes((d['size'] as num?) ?? 0, c.t)}),
              style: TextStyle(color: p.muted, height: 1.45)),
          const SizedBox(height: 16),
          TextField(
            controller: _target,
            autofocus: true,
            decoration: InputDecoration(
              labelText: c.t('storage.import_target'),
              prefixText: c.t('storage.import_prefix'),
            ),
            onChanged: (_) => setState(() {}),
          ),
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          onPressed: ok ? () => Navigator.pop(context, _target.text.trim()) : null,
          child: Text(c.t('storage.import_start')),
        ),
      ],
    );
  }
}

/// Password field dialog; pops with what was typed.
Future<String?> _askPassphrase(BuildContext context, Controller c, String action, {String? title}) => showDialog<String>(
      context: context,
      builder: (context) {
        final field = TextEditingController();
        return AlertDialog(
          title: title == null ? null : Text(title),
          content: SizedBox(
            width: 420,
            child: TextField(
              controller: field,
              autofocus: true,
              obscureText: true,
              decoration: InputDecoration(labelText: c.t('crypt.passphrase')),
              onSubmitted: (v) => Navigator.pop(context, v),
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
            FilledButton(onPressed: () => Navigator.pop(context, field.text), child: Text(c.t(action))),
          ],
        );
      },
    );

/// Encrypted disks: state, unlock, unlock at boot, change the passphrase.
class _CryptCard extends StatelessWidget {
  const _CryptCard({required this.c, required this.crypt, required this.reload, required this.area});

  final Controller c;
  final Map crypt;
  final Future<void> Function() reload;
  final AreaCalls area;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final list = crypt['disks'] is List ? (crypt['disks'] as List).whereType<Map>().toList() : <Map>[];
    final locked = list.any((d) => d['present'] == true && d['open'] != true);
    final auto = crypt['auto_unlock'] == true;
    final s = area.s;

    Future<void> unlock() async {
      final m = ScaffoldMessenger.of(context);
      final pass = await _askPassphrase(context, c, 'crypt.unlock', title: c.t('crypt.unlock'));
      if (pass == null || pass.isEmpty) return;
      final r = await area.call(() => s.post('/api/storage/unlock', {'passphrase': pass}), m);
      if (r is Map) area.say(m, c.t('crypt.unlocked', {'n': (r['opened'] as List?)?.length ?? 0}));
      await reload();
    }

    Future<void> toggleAuto(bool on) async {
      final m = ScaffoldMessenger.of(context);
      if (on) {
        final pass = await _askPassphrase(context, c, 'crypt.auto_on', title: c.t('crypt.auto'));
        if (pass == null || pass.isEmpty) return;
        final r = await area.call(() => s.post('/api/storage/auto-unlock', {'enabled': true, 'passphrase': pass}), m);
        if (r != null) area.say(m, c.t('crypt.auto_on_done'));
      } else {
        if (!await area.ask(c.t('crypt.auto_off_confirm'), 'crypt.auto_off', danger: true)) return;
        final r = await area.call(() => s.post('/api/storage/auto-unlock', {'enabled': false}), m);
        if (r != null) area.say(m, c.t('crypt.auto_off_done'));
      }
      await reload();
    }

    Future<void> change() async {
      final m = ScaffoldMessenger.of(context);
      final body = await showDialog<Map<String, String>>(context: context, builder: (context) => _PassphraseDialog(c: c));
      if (body == null) return;
      final r = await area.call(() => s.post('/api/storage/passphrase', body), m);
      if (r != null) area.say(m, c.t('crypt.changed'));
    }

    return Glass(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(c.t('crypt.title'), style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
        const SizedBox(height: 12),
        for (final d in list)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Wrap(spacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
              Text('${d['mountpoint']}', style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w600)),
              Text(tv(c, 'role', d['role']), style: TextStyle(color: p.muted, fontSize: 13)),
              if (d['present'] != true)
                Tag(c.t('crypt.missing'), p.warn)
              else if (d['open'] == true)
                Tag(c.t(d['mounted'] == true ? 'crypt.open' : 'crypt.open_unmounted'), p.ok)
              else
                Tag(c.t('crypt.locked'), p.warn),
            ]),
          ),
        if (locked) ...[
          const SizedBox(height: 6),
          FilledButton(onPressed: unlock, child: Text(c.t('crypt.unlock'))),
        ],
        const SizedBox(height: 10),
        SwitchListTile(
          value: auto,
          contentPadding: EdgeInsets.zero,
          title: Text(c.t('crypt.auto')),
          subtitle: Text(c.t('crypt.auto_hint'), style: TextStyle(color: p.muted, fontSize: 12, height: 1.4)),
          onChanged: toggleAuto,
        ),
        TextButton(onPressed: change, child: Text(c.t('crypt.change'))),
      ]),
    );
  }
}

class _PassphraseDialog extends StatefulWidget {
  const _PassphraseDialog({required this.c});

  final Controller c;

  @override
  State<_PassphraseDialog> createState() => _PassphraseDialogState();
}

class _PassphraseDialogState extends State<_PassphraseDialog> {
  final _old = TextEditingController();
  final _new = TextEditingController();
  final _repeat = TextEditingController();

  @override
  void dispose() {
    _old.dispose();
    _new.dispose();
    _repeat.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final mismatch = _repeat.text.isNotEmpty && _new.text != _repeat.text;
    final ok = _old.text.length >= 8 && _new.text.length >= 8 && _new.text == _repeat.text;
    Widget field(TextEditingController t, String key, {String? error}) => Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: TextField(
            controller: t,
            obscureText: true,
            inputFormatters: [LengthLimitingTextInputFormatter(512)],
            decoration: InputDecoration(labelText: c.t(key), errorText: error),
            onChanged: (_) => setState(() {}),
          ),
        );
    return AlertDialog(
      title: Text(c.t('crypt.change')),
      content: SizedBox(
        width: 420,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          field(_old, 'crypt.old'),
          field(_new, 'crypt.new'),
          field(_repeat, 'crypt.repeat', error: mismatch ? c.t('crypt.mismatch') : null),
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          onPressed: ok ? () => Navigator.pop(context, {'old': _old.text, 'new': _new.text}) : null,
          child: Text(c.t('crypt.change_now')),
        ),
      ],
    );
  }
}

/// Free space per disk, mover age and parity delete threshold.
class _SettingsCard extends StatefulWidget {
  const _SettingsCard({required this.c, required this.area});

  final Controller c;
  final AreaCalls area;

  @override
  State<_SettingsCard> createState() => _SettingsCardState();
}

class _SettingsCardState extends State<_SettingsCard> {
  final _free = TextEditingController();
  final _threshold = TextEditingController();
  String unit = 'G';
  int age = 0;
  bool loaded = false;
  bool saving = false;

  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _free.dispose();
    _threshold.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final r = await widget.area.call(() => widget.area.s.get('/api/storage/settings'));
    if (r is! Map || r['settings'] is! Map || !mounted) return;
    final st = r['settings'] as Map;
    final (value, u) = parseFree('${st['minfreespace'] ?? ''}');
    setState(() {
      _free.text = '$value';
      unit = u;
      age = (st['mover_min_age_minutes'] as num?)?.toInt() ?? 0;
      _threshold.text = '${st['snapraid_delete_threshold'] ?? 0}';
      loaded = true;
    });
  }

  Future<void> _save() async {
    final m = ScaffoldMessenger.of(context);
    final free = int.tryParse(_free.text.trim());
    final threshold = int.tryParse(_threshold.text.trim());
    if (free == null || free < 1 || threshold == null || threshold < 0) return;
    setState(() => saving = true);
    final r = await widget.area.call(
        () => widget.area.s.post('/api/storage/settings', {
              'minfreespace': '$free$unit',
              'mover_min_age_minutes': age,
              'snapraid_delete_threshold': threshold,
            }),
        m);
    if (r != null) widget.area.say(m, c.t('storage.settings_saved'));
    if (mounted) setState(() => saving = false);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    Widget hint(String key) => Padding(
          padding: const EdgeInsets.only(top: 6, bottom: 16),
          child: Text(c.t(key), style: TextStyle(color: p.muted, fontSize: 12, height: 1.4)),
        );
    final digits = [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(6)];
    return Glass(
      child: !loaded
          ? Busy(c.t('common.loading'))
          : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: _free,
                    inputFormatters: digits,
                    decoration: InputDecoration(labelText: c.t('storage.minfreespace')),
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  width: 110,
                  child: DropdownButtonFormField<String>(
                    initialValue: unit,
                    borderRadius: BorderRadius.circular(12),
                    items: [for (final u in ['M', 'G', 'T']) DropdownMenuItem(value: u, child: Text(c.t('storage.unit_$u')))],
                    onChanged: (v) => setState(() => unit = v ?? unit),
                  ),
                ),
              ]),
              hint('storage.minfreespace_hint'),
              DropdownButtonFormField<int>(
                initialValue: age,
                decoration: InputDecoration(labelText: c.t('storage.mover_age')),
                borderRadius: BorderRadius.circular(12),
                items: [for (final a in moverAgesWith(age)) DropdownMenuItem(value: a, child: Text(moverAgeLabel(a, c.t)))],
                onChanged: (v) => setState(() => age = v ?? age),
              ),
              hint('storage.mover_age_hint'),
              TextField(
                controller: _threshold,
                inputFormatters: digits,
                decoration: InputDecoration(labelText: c.t('parity.threshold')),
              ),
              hint('parity.threshold_hint'),
              FilledButton(onPressed: saving ? null : _save, child: Text(c.t('common.save'))),
            ]),
    );
  }
}
