// Files area: the file manager of the shares, app data and remote shares
// with search, copyable path, upload and download through the app, new
// folder, rename, and copy, move and delete as server jobs. Same API and
// rules as the dashboard's file manager.

import 'dart:async';
import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../src/apps.dart';
import '../src/controller.dart';
import '../src/files.dart';
import '../src/format.dart';
import '../src/server.dart';
import 'apps.dart' show FolderPicker;
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class FilesPage extends StatefulWidget {
  const FilesPage(this.c, {super.key});

  final Controller c;

  @override
  State<FilesPage> createState() => _FilesPageState();
}

class _FilesPageState extends State<FilesPage> with AreaCalls {
  String root = arrayRoot;
  String path = '';
  Map? data;
  List<Map> entries = const [];
  final selected = <String>{};

  /// Hits of the running search, null without one.
  List<Map>? hits;
  String searchText = '';
  bool searching = false;
  final _query = TextEditingController();

  /// Progress of an upload, download or server job: text, done, total.
  (String, num, num)? progress;
  bool jobRunning = false;
  Timer? _jobTimer;

  @override
  Controller get c => widget.c;

  bool get writable => data?['writable'] == true;

  @override
  void initState() {
    super.initState();
    _show(root, path).then((_) => _watchJob(null));
  }

  @override
  void dispose() {
    _jobTimer?.cancel();
    _query.dispose();
    super.dispose();
  }

  Future<void> _show(String toRoot, String toPath) async {
    final m = ScaffoldMessenger.of(context);
    try {
      final r = await s.get('/api/files/list?${fmQuery({'root': toRoot, 'path': toPath})}');
      if (r is! Map || !mounted) return;
      setState(() {
        root = toRoot;
        path = toPath;
        data = r;
        entries = r['entries'] is List ? (r['entries'] as List).whereType<Map>().toList() : <Map>[];
        selected.clear();
        hits = null;
      });
    } on ApiError catch (e) {
      // The folder is gone (deleted elsewhere): back to the top of the location.
      if (toPath.isNotEmpty && e.status != 401) return _show(toRoot, '');
      if (e.status == 401) {
        await c.sessionEnded();
      } else if (mounted) {
        say(m, c.t(e.key), error: true);
      }
    } on VersionMismatch {
      return;
    }
  }

  Future<void> _search() async {
    final q = _query.text.trim();
    if (q.isEmpty) return;
    final m = ScaffoldMessenger.of(context);
    setState(() => searching = true);
    final r = await call(() => s.get('/api/files/search?${fmQuery({'root': root, 'path': path, 'query': q})}'), m);
    if (!mounted) return;
    setState(() {
      searching = false;
      if (r is! Map) return;
      hits = r['entries'] is List ? (r['entries'] as List).whereType<Map>().toList() : <Map>[];
      selected.clear();
      searchText = c.t('fm.search_hits', {'n': hits!.length, 'query': q, 'path': fullPath(root, path)});
      if (r['truncated'] == true) searchText += ' ${c.t('fm.search_truncated', {'n': hits!.length})}';
    });
  }

  Future<void> _openHit(Map e) async {
    final p = '${e['path']}';
    if (e['kind'] == 'dir') return _show(root, p);
    await _show(root, parentOf(p));
    if (mounted && entries.any((x) => x['name'] == e['name'])) setState(() => selected.add('${e['name']}'));
  }

  Future<void> _copyPath() async {
    await Clipboard.setData(ClipboardData(text: fullPath(root, path)));
    if (mounted) say(ScaffoldMessenger.of(context), c.t('fm.path_copied'));
  }

  Future<String?> _askName(String label, String value) => showDialog<String>(
        context: context,
        builder: (context) => _NameDialog(c: c, label: label, value: value),
      );

  Future<void> _mkdir() async {
    final m = ScaffoldMessenger.of(context);
    final name = await _askName(c.t('fm.folder_name'), '');
    if (name == null) return;
    await call(() => s.post('/api/files/dir', {'root': root, 'path': path, 'name': name}), m);
    await _show(root, path);
  }

  Future<void> _rename(String old) async {
    final m = ScaffoldMessenger.of(context);
    final name = await _askName(c.t('fm.new_name'), old);
    if (name == null || name == old) return;
    await call(() => s.post('/api/files/rename', {'root': root, 'path': joinPath(path, old), 'name': name}), m);
    await _show(root, path);
  }

  Future<void> _upload() async {
    final m = ScaffoldMessenger.of(context);
    final files = await openFiles();
    if (files.isEmpty || !mounted) return;
    var done = 0;
    try {
      for (final x in files) {
        final name = x.name;
        var overwrite = false;
        switch (uploadCase(entries, name)) {
          case 'dir':
            say(m, c.t('fm.exists_dir', {'name': name}), error: true);
            continue;
          case 'overwrite':
            if (!await ask(c.t('fm.confirm_overwrite', {'name': name}), 'fm.overwrite', danger: true)) continue;
            overwrite = true;
        }
        final q = fmQuery({'root': root, 'path': path, 'name': name, 'overwrite': '$overwrite'});
        final r = await call(
            () => s.upload('/api/files/upload?$q', File(x.path), progress: (sent, total) {
                  if (!mounted) return;
                  setState(() => progress = (
                        c.t('fm.uploading', {'name': name, 'done': bytes(sent, c.t), 'total': bytes(total, c.t)}),
                        sent,
                        total
                      ));
                }),
            m);
        if (r == null) break;
        done++;
      }
      if (done > 0) say(m, c.t('fm.uploaded', {'n': done}));
    } finally {
      if (mounted) setState(() => progress = null);
      await _show(root, path);
    }
  }

  Future<void> _download(String name) async {
    final m = ScaffoldMessenger.of(context);
    final where = await getSaveLocation(suggestedName: name);
    if (where == null || !mounted) return;
    final q = fmQuery({'root': root, 'path': joinPath(path, name)});
    final r = await call(() async {
      await s.download('/api/files/download?$q', File(where.path), progress: (got, total) {
        if (!mounted) return;
        setState(() => progress = (
              c.t('fm.downloading', {'name': name, 'done': bytes(got, c.t), 'total': bytes(total < 0 ? got : total, c.t)}),
              got,
              total < 0 ? 0 : total
            ));
      });
      return true;
    }, m);
    if (mounted) setState(() => progress = null);
    if (r == true) say(m, c.t('fm.downloaded', {'path': where.path}));
  }

  Future<void> _start(String op, List<String> names, [Place? dest]) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.post('/api/files/start', startBody(op, root, path, names, dest)), m);
    if (r != null) await _watchJob(op);
  }

  Future<void> _copyMove(String op, List<String> names) async {
    // The picker opens the parent of the path it gets: start in this folder.
    final dest = await showDialog<Place>(
      context: context,
      builder: (context) => FolderPicker(c: c, start: Place(root, joinPath(path, '.'))),
    );
    if (dest != null) await _start(op, names, dest);
  }

  Future<void> _delete(List<String> names) async {
    final what = names.length == 1 ? names.first : c.t('fm.selected', {'n': names.length});
    if (!await ask(c.t('fm.confirm_delete', {'what': what}), 'fm.delete', danger: true)) return;
    await _start('delete', names);
  }

  /// Shows a running delete, copy or move until it ends, then reports (when
  /// [op] is set) and reloads. op is null when the page opens.
  Future<void> _watchJob(String? op) async {
    _jobTimer?.cancel();
    if (!mounted) return;
    final m = ScaffoldMessenger.of(context);
    final job = await call(() => s.get('/api/files/job'));
    if (job is! Map || !mounted) return;
    if (job['state'] == 'running') {
      setState(() {
        jobRunning = true;
        progress = (jobText(job, c.t), (job['done'] as num?) ?? 0, (job['total'] as num?) ?? 0);
      });
      _jobTimer = Timer(const Duration(seconds: 1), () => _watchJob('${job['op']}'));
      return;
    }
    final was = jobRunning;
    setState(() {
      jobRunning = false;
      progress = null;
    });
    if (op == null && !was) return;
    final kind = op ?? '${job['op']}';
    if (job['state'] == 'failed') {
      say(m, '${c.t('fm.failed')} (${job['error']})', error: true);
    } else {
      say(m, c.t('fm.done_$kind'));
    }
    await _show(root, path);
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final d = data;
    final available = <String, bool>{};
    if (d != null && d['locations'] is List) {
      for (final l in (d['locations'] as List).whereType<Map>()) {
        available['${l['path']}'] = l['available'] == true;
      }
    }
    final parts = path.isEmpty ? const <String>[] : path.split('/');
    final names = hits == null ? selectedInOrder(entries, selected) : const <String>[];
    final can = allowed(entries, names, writable);
    final hintKey = d == null ? '' : folderHint(d, path);
    final busy = progress != null;
    final narrow = MediaQuery.of(context).size.width < 760;

    final pathBox = Row(children: [
      Expanded(
        child: Container(
          height: 46,
          alignment: Alignment.centerLeft,
          padding: const EdgeInsets.symmetric(horizontal: 14),
          decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12), border: Border.all(color: p.line)),
          child: SelectableText(fullPath(root, path), maxLines: 1, style: const TextStyle(fontFamily: 'monospace', fontSize: 13)),
        ),
      ),
      const SizedBox(width: 8),
      OutlinedButton(onPressed: _copyPath, child: Text(c.t('fm.copy_path'))),
    ]);
    final searchBox = Row(children: [
      Expanded(
        child: TextField(
          controller: _query,
          inputFormatters: [LengthLimitingTextInputFormatter(255)],
          decoration: InputDecoration(hintText: c.t('fm.search_placeholder'), prefixIcon: const Icon(Icons.search_rounded), isDense: true),
          onSubmitted: (_) => _search(),
        ),
      ),
      const SizedBox(width: 8),
      OutlinedButton(onPressed: searching ? null : _search, child: Text(c.t('fm.search'))),
    ]);

    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('nav.files'))),
      const SizedBox(height: 24),
      Glass(
        child: d == null
            ? Busy(c.t('common.loading'))
            : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Places(
                  c: c,
                  current: root,
                  available: available,
                  remotes: available[remotesRoot] == true,
                  arrayFirst: true,
                  onPick: (r) => _show(r, ''),
                ),
                const SizedBox(height: 14),
                SizedBox(
                  width: double.infinity,
                  child: Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  alignment: WrapAlignment.spaceBetween,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Wrap(crossAxisAlignment: WrapCrossAlignment.center, children: [
                      _crumb(root, () => _show(root, '')),
                      for (final (i, part) in parts.indexed) ...[
                        Text('/', style: TextStyle(color: p.muted)),
                        _crumb(part, () => _show(root, parts.sublist(0, i + 1).join('/'))),
                      ],
                    ]),
                    Wrap(spacing: 8, runSpacing: 8, children: [
                      OutlinedButton(onPressed: () => _show(root, path), child: Text(c.t('common.refresh'))),
                      OutlinedButton(onPressed: writable && !busy ? _mkdir : null, child: Text(c.t('fm.new_folder'))),
                      FilledButton(onPressed: writable && !busy ? _upload : null, child: Text(c.t('fm.upload'))),
                    ]),
                  ],
                  ),
                ),
                const SizedBox(height: 12),
                if (narrow) ...[pathBox, const SizedBox(height: 10), searchBox]
                else
                  Row(children: [Expanded(child: pathBox), const SizedBox(width: 18), Expanded(child: searchBox)]),
                if (searching || hits != null) ...[
                  const SizedBox(height: 10),
                  Row(children: [
                    Expanded(
                      child: Text(searching ? c.t('fm.searching', {'query': _query.text.trim()}) : searchText,
                          style: TextStyle(color: p.muted, fontSize: 13)),
                    ),
                    if (hits != null)
                      TextButton(
                        onPressed: () {
                          _query.clear();
                          setState(() => hits = null);
                        },
                        child: Text(c.t('fm.search_end')),
                      ),
                  ]),
                ],
                if (progress case (final text, final done, final total)) ...[
                  const SizedBox(height: 12),
                  Container(
                    padding: const EdgeInsets.fromLTRB(14, 10, 8, 10),
                    decoration: BoxDecoration(color: p.accentSoft, borderRadius: BorderRadius.circular(12)),
                    child: Row(children: [
                      Expanded(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Text(text, style: const TextStyle(fontSize: 13)),
                          const SizedBox(height: 8),
                          if (total > 0) Meter(done / total, height: 6) else const LinearProgressIndicator(minHeight: 6),
                        ]),
                      ),
                      if (jobRunning)
                        TextButton(
                          onPressed: () => call(() => s.post('/api/files/cancel'), ScaffoldMessenger.of(context)),
                          child: Text(c.t('common.cancel')),
                        ),
                    ]),
                  ),
                ],
                if (names.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
                    Text(c.t('fm.selected', {'n': names.length}), style: TextStyle(color: p.muted)),
                    const SizedBox(width: 4),
                    OutlinedButton(onPressed: can.download && !busy ? () => _download(names.first) : null, child: Text(c.t('fm.download'))),
                    OutlinedButton(onPressed: can.rename && !busy ? () => _rename(names.first) : null, child: Text(c.t('fm.rename'))),
                    OutlinedButton(onPressed: can.copy && !busy ? () => _copyMove('copy', names) : null, child: Text(c.t('fm.copy'))),
                    OutlinedButton(onPressed: can.move && !busy ? () => _copyMove('move', names) : null, child: Text(c.t('fm.move'))),
                    OutlinedButton(
                      style: OutlinedButton.styleFrom(foregroundColor: p.danger),
                      onPressed: can.delete && !busy ? () => _delete(names) : null,
                      child: Text(c.t('fm.delete')),
                    ),
                  ]),
                ],
                if (hintKey.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  Text(c.t(hintKey, {'n': entries.length}), style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
                ],
                const SizedBox(height: 12),
                _table(p),
              ]),
      ),
    ]);
  }

  Widget _crumb(String text, VoidCallback onTap) => TextButton(
        style: TextButton.styleFrom(minimumSize: const Size(0, 36), padding: const EdgeInsets.symmetric(horizontal: 6)),
        onPressed: onTap,
        child: Text(text),
      );

  Widget _table(Palette p) {
    final lang = c.settings.lang;
    final when = DateFormat.yMd(lang).add_Hm();
    final list = hits ?? entries;
    final allOn = entries.isNotEmpty && selected.length == entries.length;
    Widget row({required Widget pick, required Widget name, required String size, required String time, VoidCallback? onTap, bool on = false}) =>
        Material(
          color: on ? p.navActive : Colors.transparent,
          borderRadius: BorderRadius.circular(10),
          child: InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(children: [
                SizedBox(width: 44, child: pick),
                Expanded(child: name),
                SizedBox(width: 100, child: Text(size, textAlign: TextAlign.right, style: TextStyle(color: p.muted, fontSize: 13))),
                const SizedBox(width: 24),
                SizedBox(width: 150, child: Text(time, style: TextStyle(color: p.muted, fontSize: 13))),
              ]),
            ),
          ),
        );
    String size(Map e) => e['size'] == null ? '' : bytes(e['size'] as num, c.t);
    String time(Map e) => e['mtime'] is num
        ? when.format(DateTime.fromMillisecondsSinceEpoch(((e['mtime'] as num) * 1000).round()))
        : '';
    Widget nameOf(Map e, {String? where}) {
      final dir = e['kind'] == 'dir';
      return Row(children: [
        Icon(dir ? Icons.folder_rounded : Icons.description_outlined, size: 20, color: dir ? p.accent : p.muted),
        const SizedBox(width: 10),
        Flexible(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${e['name']}', overflow: TextOverflow.ellipsis, style: TextStyle(fontWeight: dir ? FontWeight.w500 : null)),
            if (where != null) Text(where, overflow: TextOverflow.ellipsis, style: TextStyle(color: p.muted, fontSize: 12)),
          ]),
        ),
        if (e['kind'] == 'link') ...[const SizedBox(width: 8), Tag(c.t('fm.link'), p.muted)],
      ]);
    }

    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Container(
        decoration: BoxDecoration(border: Border(bottom: BorderSide(color: p.line))),
        child: row(
          pick: hits != null
              ? const SizedBox.shrink()
              : Checkbox(
                  value: allOn ? true : (selected.isEmpty ? false : null),
                  tristate: true,
                  onChanged: entries.isEmpty
                      ? null
                      : (_) => setState(() {
                            if (allOn) {
                              selected.clear();
                            } else {
                              selected.addAll(entries.map((e) => '${e['name']}'));
                            }
                          }),
                ),
          name: Text(c.t('fm.name'), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
          size: c.t('fm.size'),
          time: c.t('fm.modified'),
        ),
      ),
      if (list.isEmpty)
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(c.t(hits != null ? 'fm.no_hits' : 'fm.empty'), style: TextStyle(color: p.muted)),
        ),
      if (hits != null)
        for (final e in hits!)
          row(
            pick: const SizedBox.shrink(),
            name: nameOf(e, where: fullPath(root, parentOf('${e['path']}'))),
            size: size(e),
            time: time(e),
            onTap: () => _openHit(e),
          )
      else
        for (final e in entries)
          row(
            on: selected.contains(e['name']),
            pick: Checkbox(
              value: selected.contains(e['name']),
              onChanged: (v) => setState(() => v == true ? selected.add('${e['name']}') : selected.remove(e['name'])),
            ),
            name: nameOf(e),
            size: size(e),
            time: time(e),
            onTap: e['kind'] == 'dir'
                ? () => _show(root, joinPath(path, '${e['name']}'))
                : () => setState(() => selected.contains(e['name']) ? selected.remove(e['name']) : selected.add('${e['name']}')),
          ),
    ]);
  }
}

/// One name, for a new folder or a rename; pops with the trimmed name.
class _NameDialog extends StatefulWidget {
  const _NameDialog({required this.c, required this.label, required this.value});

  final Controller c;
  final String label, value;

  @override
  State<_NameDialog> createState() => _NameDialogState();
}

class _NameDialogState extends State<_NameDialog> {
  late final _name = TextEditingController(text: widget.value)
    ..selection = TextSelection(baseOffset: 0, extentOffset: widget.value.length);

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  void _ok() {
    final v = _name.text.trim();
    if (v.isNotEmpty) Navigator.pop(context, v);
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    return AlertDialog(
      content: SizedBox(
        width: 420,
        child: TextField(
          controller: _name,
          autofocus: true,
          inputFormatters: [LengthLimitingTextInputFormatter(255)],
          decoration: InputDecoration(labelText: widget.label),
          onSubmitted: (_) => _ok(),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(onPressed: _ok, child: Text(c.t('common.ok'))),
      ],
    );
  }
}
