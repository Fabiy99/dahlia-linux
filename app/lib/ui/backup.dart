// Backup area: the target (a disk or SFTP), the last run, starting a
// backup, the repository password and restoring the configuration or an app
// from a snapshot. Same API and rules as the dashboard's backup page.

import 'dart:async';

import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../src/backup.dart';
import '../src/controller.dart';
import '../src/format.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class BackupPage extends StatefulWidget {
  const BackupPage(this.c, {super.key});

  final Controller c;

  @override
  State<BackupPage> createState() => _BackupPageState();
}

class _BackupPageState extends State<BackupPage> with AreaCalls {
  Map? d;
  List<Map> apps = const [];

  /// Snapshots once listed, newest first.
  List<Map>? snapshots;
  final restoreWhat = <String, String>{};
  bool busy = false;
  Timer? _timer;

  String type = 'folder';
  String folder = '';
  bool stopApps = true;
  final _host = TextEditingController();
  final _port = TextEditingController(text: '22');
  final _user = TextEditingController();
  final _path = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load(fill: true);
  }

  @override
  void dispose() {
    _timer?.cancel();
    for (final t in [_host, _port, _user, _path]) {
      t.dispose();
    }
    super.dispose();
  }

  Future<void> _load({bool fill = false}) async {
    _timer?.cancel();
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => Future.wait([s.get('/api/backup'), s.get('/api/apps?catalog=false')]), m);
    if (r == null || !mounted) return;
    final [data, list] = r;
    if (data is! Map) return;
    setState(() {
      d = data;
      apps = list is Map && list['installed'] is List ? (list['installed'] as List).whereType<Map>().toList() : <Map>[];
      // The form is filled from the server once; later loads keep what is being typed.
      if (fill) {
        final target = data['target'];
        type = target is Map && target['type'] == 'sftp' ? 'sftp' : 'folder';
        final choices = folderChoices(data);
        folder = target is Map && type == 'folder' ? '${target['path'] ?? ''}' : '';
        if (folder.isEmpty && choices.isNotEmpty) folder = '${choices.first['path']}';
        if (target is Map && type == 'sftp') {
          _host.text = '${target['host'] ?? ''}';
          _port.text = '${target['port'] ?? 22}';
          _user.text = '${target['user'] ?? ''}';
          _path.text = '${target['path'] ?? ''}';
        }
        stopApps = data['stop_apps'] != false;
      }
    });
    // A backup or restore runs for minutes: follow it.
    if (unitActive(data['job']) || unitActive(data['restore'])) _timer = Timer(const Duration(seconds: 4), _load);
  }

  Map<String, Object>? get _body => backupBody(
        type: type,
        folder: folder,
        host: _host.text,
        port: _port.text,
        user: _user.text,
        path: _path.text,
        stopApps: stopApps,
        folders: d?['folders'] is List ? d!['folders'] as List : const [],
      );

  Future<void> _save() async {
    final m = ScaffoldMessenger.of(context);
    final body = _body;
    if (body == null) {
      say(m, c.t('backup.choose_target'), error: true);
      return;
    }
    setState(() => busy = true);
    final r = await call(() => s.post('/api/backup', body), m);
    if (r != null) say(m, c.t('backup.saved'));
    if (mounted) setState(() => busy = false);
    await _load();
  }

  Future<void> _run() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/jobs/backup/start'), m);
    if (r != null) say(m, c.t('backup.started'));
    if (mounted) setState(() => busy = false);
    await _load();
  }

  Future<void> _key() async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.post('/api/backup/key'), m);
    if (r is! Map || !mounted) return;
    final password = '${r['password'] ?? ''}';
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        content: SizedBox(
          width: 460,
          child: SelectableText(c.t('backup.key_text', {'password': password}), style: const TextStyle(height: 1.45)),
        ),
        actions: [
          TextButton(
            onPressed: () => Clipboard.setData(ClipboardData(text: password)),
            child: Text(c.t('backup.copy_key')),
          ),
          FilledButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.ok'))),
        ],
      ),
    );
  }

  Future<void> _list() async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.get('/api/backup/snapshots'), m);
    if (r is! Map || !mounted) return;
    setState(() => snapshots = (r['snapshots'] is List ? (r['snapshots'] as List).whereType<Map>().toList() : <Map>[]).reversed.toList());
  }

  Future<void> _restore(Map snap) async {
    final m = ScaffoldMessenger.of(context);
    final id = '${snap['id']}';
    final choices = restoreChoices(apps, c.t);
    final what = restoreWhat[id] ?? choices.first.$1;
    final name = choices.firstWhere((e) => e.$1 == what, orElse: () => choices.first).$2;
    if (!await ask(c.t('backup.restore_confirm', {'what': name, 'time': _time(snap['time'])}), 'backup.restore', danger: true)) return;
    final r = await call(() => s.post('/api/backup/restore', {'snapshot': id, 'what': what}), m);
    if (r != null) say(m, c.t('backup.restore_started', {'what': name}));
    await _load();
  }

  String _time(Object? v) {
    final t = parseTime(v);
    return t == null ? '-' : DateFormat.yMd(c.settings.lang).add_Hm().format(t);
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final half = grid.cols == 1 ? full : (full - 18) / 2;
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('nav.backup'), hint: c.t('backup.what'))),
      const SizedBox(height: 28),
      if (data == null)
        Busy(c.t('common.loading'))
      else
        Wrap(spacing: 18, runSpacing: 18, children: [
          SizedBox(width: half, child: _targetCard(p, data)),
          SizedBox(width: half, child: _stateCard(p, data)),
          if (snapshots != null) SizedBox(width: full, child: _snapshotsCard(p)),
        ]),
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

  Widget _field(TextEditingController t, String label, {String? hint, List<TextInputFormatter>? formatters}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextField(
          controller: t,
          autocorrect: false,
          inputFormatters: formatters ?? [LengthLimitingTextInputFormatter(255)],
          decoration: InputDecoration(labelText: c.t(label), hintText: hint),
          onChanged: (_) => setState(() {}),
        ),
      );

  Widget _targetCard(Palette p, Map data) {
    final choices = folderChoices(data);
    final key = '${data['public_key'] ?? ''}';
    return _card(c.t('backup.target_title'), [
      DropdownButtonFormField<String>(
        initialValue: type,
        decoration: InputDecoration(labelText: c.t('backup.type')),
        borderRadius: BorderRadius.circular(12),
        items: [for (final v in ['folder', 'sftp']) DropdownMenuItem(value: v, child: Text(c.t('backup.type_$v')))],
        onChanged: (v) => setState(() => type = v ?? type),
      ),
      const SizedBox(height: 12),
      if (type == 'folder') ...[
        DropdownButtonFormField<String>(
          key: ValueKey(choices.map((e) => e['path']).join(',')),
          initialValue: choices.any((e) => e['path'] == folder) ? folder : null,
          isExpanded: true,
          decoration: InputDecoration(labelText: c.t('backup.disk')),
          borderRadius: BorderRadius.circular(12),
          items: [
            for (final ch in choices) DropdownMenuItem(value: '${ch['path']}', child: Text(choiceLabel(ch, c.t), overflow: TextOverflow.ellipsis)),
          ],
          onChanged: (v) => setState(() => folder = v ?? folder),
        ),
        const SizedBox(height: 8),
        _hint(p, c.t('backup.disk_hint')),
      ] else ...[
        _field(_host, 'backup.host', hint: 'nas.fritz.box'),
        _field(_port, 'backup.port', formatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(5)]),
        _field(_user, 'backup.user'),
        _field(_path, 'backup.path', hint: 'dahlia-backup'),
        _hint(p, c.t('backup.sftp_hint')),
        if (key.isNotEmpty)
          Container(
            width: double.infinity,
            margin: const EdgeInsets.only(bottom: 12),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
            child: SelectableText(key, style: const TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12)),
          ),
      ],
      CheckboxListTile(
        value: stopApps,
        dense: true,
        contentPadding: EdgeInsets.zero,
        controlAffinity: ListTileControlAffinity.leading,
        title: Text(c.t('backup.stop_apps')),
        onChanged: (v) => setState(() => stopApps = v ?? true),
      ),
      const SizedBox(height: 10),
      Wrap(spacing: 10, runSpacing: 10, children: [
        FilledButton(onPressed: !busy && _body != null ? _save : null, child: Text(c.t('common.save'))),
        OutlinedButton(onPressed: data['target'] is Map ? _key : null, child: Text(c.t('backup.show_key'))),
      ]),
    ]);
  }

  Widget _stateCard(Palette p, Map data) {
    final last = data['last'] is Map ? data['last'] as Map : const {};
    final running = unitActive(data['job']);
    final restoring = unitActive(data['restore']);
    final hasTarget = data['target'] is Map;
    Widget fact(String key, String value, {Color? color}) => Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(width: 150, child: Text(c.t(key), style: TextStyle(color: p.muted))),
            Expanded(child: SelectableText(value, style: TextStyle(color: color, height: 1.35))),
          ]),
        );
    final ok = last['result'] == 'success';
    return _card(c.t('backup.state_title'), [
      fact('backup.target', hasTarget ? targetText(data['target']) : c.t('backup.none')),
      fact('backup.last', last['started'] == null ? c.t('jobs.never') : _time(last['started'])),
      if (last['result'] != null)
        fact('jobs.result', tv(c, 'job_result', ok ? 'success' : 'exit-code'), color: ok ? p.ok : p.danger),
      if ('${last['snapshot'] ?? ''}'.isNotEmpty) fact('backup.id', '${last['snapshot']}'.substring(0, '${last['snapshot']}'.length.clamp(0, 8))),
      if (last['added'] is num) fact('backup.added', bytes(last['added'] as num, c.t)),
      if ('${last['error'] ?? ''}'.isNotEmpty) fact('backup.error', '${last['error']}', color: p.danger),
      if ('${last['warning'] ?? ''}'.isNotEmpty) fact('backup.error', '${last['warning']}', color: p.warn),
      if (running) fact('jobs.state', c.t('backup.running')),
      if (restoring) fact('jobs.state', c.t('backup.restoring')),
      if (running || restoring) const Padding(padding: EdgeInsets.only(bottom: 12), child: LinearProgressIndicator()),
      _hint(p, c.t('backup.schedule_hint')),
      Wrap(spacing: 10, runSpacing: 10, children: [
        FilledButton(onPressed: !busy && hasTarget && !running ? _run : null, child: Text(c.t('backup.run'))),
        OutlinedButton(onPressed: hasTarget ? _list : null, child: Text(c.t('backup.list'))),
      ]),
    ]);
  }

  Widget _snapshotsCard(Palette p) {
    final list = snapshots!;
    final choices = restoreChoices(apps, c.t);
    final restoring = unitActive(d?['restore']);
    return _card(c.t('backup.snapshots_title'), [
      _hint(p, c.t('backup.restore_hint')),
      if (list.isEmpty) Text(c.t('backup.no_snapshots'), style: TextStyle(color: p.muted)),
      for (final snap in list)
        Container(
          width: double.infinity,
          margin: const EdgeInsets.only(bottom: 8),
          padding: const EdgeInsets.fromLTRB(14, 8, 8, 8),
          decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
          child: Wrap(spacing: 12, runSpacing: 8, alignment: WrapAlignment.spaceBetween, crossAxisAlignment: WrapCrossAlignment.center, children: [
            Wrap(spacing: 12, crossAxisAlignment: WrapCrossAlignment.center, children: [
              Text(_time(snap['time']), style: const TextStyle(fontWeight: FontWeight.w600)),
              Text('${snap['id']}', style: TextStyle(color: p.muted, fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 13)),
            ]),
            Row(mainAxisSize: MainAxisSize.min, children: [
              SizedBox(
                width: 320,
                child: DropdownButtonFormField<String>(
                  initialValue: restoreWhat['${snap['id']}'] ?? choices.first.$1,
                  isDense: true,
                  isExpanded: true,
                  borderRadius: BorderRadius.circular(12),
                  items: [for (final (v, label) in choices) DropdownMenuItem(value: v, child: Text(label, overflow: TextOverflow.ellipsis))],
                  onChanged: (v) => setState(() => restoreWhat['${snap['id']}'] = v ?? 'config'),
                ),
              ),
              const SizedBox(width: 8),
              OutlinedButton(onPressed: restoring ? null : () => _restore(snap), child: Text(c.t('backup.restore'))),
            ]),
          ]),
        ),
    ]);
  }
}
