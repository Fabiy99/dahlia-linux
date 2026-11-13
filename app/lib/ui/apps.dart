// Apps area: installed apps with open, start, stop, update and remove, the
// catalog with search and categories, and the install dialog with location
// and folder picker. Same API and rules as the dashboard's apps page.

import 'dart:async';

import 'package:material_ui/material_ui.dart';
import 'package:url_launcher/url_launcher.dart';

import '../src/apps.dart';
import '../src/controller.dart';
import '../src/server.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

/// Text of [prefix].[value], the value itself when the key is unknown.
String _tv(Controller c, String prefix, Object? value) {
  final key = '$prefix.$value';
  return c.strings.has(key) ? c.t(key) : '${value ?? ''}';
}

class AppsPage extends StatefulWidget {
  const AppsPage(this.c, {super.key});

  final Controller c;

  @override
  State<AppsPage> createState() => _AppsPageState();
}

class _AppsPageState extends State<AppsPage> {
  List<Map> installed = const [];
  List<Map> catalog = const [];
  String? refreshed;
  bool gpu = false;
  bool loaded = false;
  bool refreshing = false;
  bool allRunning = false;
  String query = '';
  String category = '';

  /// Apps with an operation in progress; their buttons are replaced by a spinner.
  final busy = <String>{};
  Timer? _timer;

  Controller get c => widget.c;
  Server get s => c.server!;

  @override
  void initState() {
    super.initState();
    _load(catalog: true);
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _say(ScaffoldMessengerState m, String text, {bool error = false}) {
    m
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(text),
        backgroundColor: error ? context.palette.danger : null,
      ));
  }

  /// Runs [f]; errors are shown as a message, a lost session leads back to
  /// the login, a version change to the update page (Controller).
  Future<T?> _call<T>(Future<T> Function() f, [ScaffoldMessengerState? m]) async {
    try {
      return await f();
    } on VersionMismatch {
      return null;
    } on ApiError catch (e) {
      if (e.status == 401) {
        await c.sessionEnded();
      } else if (m != null) {
        _say(m, c.t(e.key), error: true);
      }
      return null;
    }
  }

  /// The catalog changes only with a refresh; otherwise only the installed
  /// apps are fetched and the "installed" marks of the catalog follow them.
  Future<void> _load({bool catalog = false}) async {
    final r = await _call(() => s.get(catalog ? '/api/apps' : '/api/apps?catalog=false'));
    if (r is! Map || !mounted) return;
    final list = r['installed'] is List ? (r['installed'] as List).whereType<Map>().toList() : <Map>[];
    setState(() {
      installed = list;
      loaded = true;
      if (catalog) {
        this.catalog = r['catalog'] is List ? (r['catalog'] as List).whereType<Map>().toList() : <Map>[];
        refreshed = r['catalog_refreshed'] as String?;
        gpu = r['gpu_available'] == true;
      } else {
        final have = {for (final a in list) a['id']};
        this.catalog = [for (final a in this.catalog) {...a, 'installed': have.contains(a['id'])}];
      }
    });
  }

  Future<void> _refreshCatalog() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => refreshing = true);
    await _call(() => s.post('/api/apps/catalog/refresh'), m);
    await _load(catalog: true);
    if (mounted) setState(() => refreshing = false);
  }

  /// Install or update in the background on the server, followed until done.
  /// The message at the end also shows when the user went to another area.
  Future<void> _job(Map app, String op, [Map<String, Object>? body]) async {
    final m = ScaffoldMessenger.of(context);
    final id = '${app['id']}';
    final name = '${app['name'] ?? id}';
    setState(() => busy.add(id));
    try {
      final started = await _call(() => s.post('/api/apps/$id/$op', body), m);
      if (started == null) return;
      _say(m, c.t('apps.${op}_running', {'name': name}));
      await _load();
      final job = await _call(() => waitForJob(s, id, tick: () => mounted ? _load() : null), m);
      final (key, vars) = jobResult(job, op, id, name);
      _say(m, c.t(key, vars), error: key != 'apps.${op}_done');
    } finally {
      if (mounted) setState(() => busy.remove(id));
      if (mounted) await _load();
    }
  }

  Future<void> _startStop(Map app, String op) async {
    final m = ScaffoldMessenger.of(context);
    final id = '${app['id']}';
    setState(() => busy.add(id));
    await _call(() => s.post('/api/apps/$id/$op'), m);
    await _load();
    if (mounted) setState(() => busy.remove(id));
  }

  Future<bool> _ask(String text, String action, {bool danger = false}) async {
    final p = context.palette;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        content: Text(text, style: const TextStyle(height: 1.45)),
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

  /// One dialog: confirm, and whether the app data goes too (kept unless ticked).
  Future<void> _remove(Map app) async {
    final m = ScaffoldMessenger.of(context);
    final id = '${app['id']}';
    final name = '${app['name'] ?? id}';
    final path = '${app['location'] ?? arrayRoot}/appdata/$id';
    final deleteData = await showDialog<bool>(
      context: context,
      builder: (context) => _RemoveDialog(c: c, name: name, path: path),
    );
    if (deleteData == null || !mounted) return;
    setState(() => busy.add(id));
    final r = await _call(() => s.post('/api/apps/$id/remove', {'delete_data': deleteData}), m);
    if (r != null) _say(m, c.t('apps.removed', {'name': name}));
    if (mounted) setState(() => busy.remove(id));
    await _load();
  }

  Future<void> _install(Map app) async {
    final body = await showDialog<Map<String, Object>>(
      context: context,
      builder: (context) => _InstallDialog(c: c, app: app),
    );
    if (body != null && mounted) await _job(app, 'install', body);
  }

  Future<void> _updateAll(List<Map> apps) async {
    for (final a in apps) {
      if (!mounted) return;
      await _job(a, 'update');
    }
  }

  Future<void> _all(String op) async {
    final m = ScaffoldMessenger.of(context);
    if (op == 'stop' && !await _ask(c.t('apps.stop_all_confirm'), 'apps.stop_all', danger: true)) return;
    if (!mounted) return;
    setState(() => allRunning = true);
    final r = await _call(() => s.post('/api/apps-all/$op'), m);
    if (r != null) {
      _say(m, c.t(op == 'start' ? 'apps.start_all_started' : 'apps.stop_all_started'));
      while (mounted) {
        await Future<void>.delayed(const Duration(seconds: 2));
        final job = await _call(() => s.get('/api/apps-all'), m);
        if (jobRunning(job)) continue;
        final error = job is Map ? job['error'] : null;
        if (error is String && error.isNotEmpty) _say(m, c.t('apps.all_failed', {'error': error}), error: true);
        break;
      }
    }
    if (mounted) setState(() => allRunning = false);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final width = MediaQuery.of(context).size.width;
    final grid = pageGrid(width);
    final pending = installed.where((a) => a['update'] != null).toList();
    final canStart = installed.any((a) => a['enabled'] != false && a['status'] != 'running');
    final canStop = installed.any((a) => a['status'] == 'running');
    final shown = filterCatalog(catalog, query, category, c.settings.lang);
    final cats = categories(catalog).entries.toList()
      ..sort((x, y) => _tv(c, 'category', x.key).compareTo(_tv(c, 'category', y.key)));
    final when = refreshed == null ? null : DateTime.tryParse(refreshed!)?.toLocal();

    Widget heading(String text, [List<Widget> side = const []]) => Padding(
          padding: const EdgeInsets.only(top: 34, bottom: 14),
          child: Wrap(spacing: 10, runSpacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
            Text(text, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, letterSpacing: -.3)),
            const SizedBox(width: 6),
            ...side,
          ]),
        );

    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c.t('apps.title'), style: const TextStyle(fontSize: 40, fontWeight: FontWeight.w700, letterSpacing: -1)),
          const SizedBox(height: 8),
          Text(c.t('apps.hint'), style: TextStyle(color: p.muted, height: 1.45)),
        ]),
      ),
      heading(c.t('apps.installed'), [
        if (pending.isNotEmpty)
          FilledButton(
            onPressed: busy.isEmpty ? () => _updateAll(pending) : null,
            child: Text(c.t('apps.update_all', {'count': pending.length})),
          ),
        if (canStart)
          OutlinedButton(onPressed: allRunning ? null : () => _all('start'), child: Text(c.t('apps.start_all'))),
        if (canStop)
          OutlinedButton(onPressed: allRunning ? null : () => _all('stop'), child: Text(c.t('apps.stop_all'))),
      ]),
      if (!loaded) Busy(c.t('common.loading')),
      if (loaded && installed.isEmpty) Text(c.t('apps.none_installed'), style: TextStyle(color: p.muted)),
      Wrap(spacing: 18, runSpacing: 18, children: [
        for (final (i, a) in installed.indexed)
          SizedBox(
            width: grid.unit,
            child: Rise(
              delay: 40 * (i % 6),
              child: _InstalledCard(
                c: c,
                app: a,
                gpu: gpu,
                busy: busy.contains('${a['id']}'),
                onStart: () => _startStop(a, 'start'),
                onStop: () => _startStop(a, 'stop'),
                onUpdate: () => _job(a, 'update'),
                onRemove: () => _remove(a),
              ),
            ),
          ),
      ]),
      heading(c.t('apps.catalog'), [
        OutlinedButton(onPressed: refreshing ? null : _refreshCatalog, child: Text(c.t('apps.refresh'))),
        if (refreshing) const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2)),
      ]),
      if (when != null)
        Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: Text(c.t('apps.refreshed', {'when': MaterialLocalizations.of(context).formatShortDate(when)}),
              style: TextStyle(color: p.muted, fontSize: 13)),
        ),
      SizedBox(
        width: grid.cols == 1 ? double.infinity : grid.unit,
        child: TextField(
          decoration: InputDecoration(hintText: c.t('apps.search'), prefixIcon: const Icon(Icons.search_rounded)),
          onChanged: (v) => setState(() => query = v),
        ),
      ),
      const SizedBox(height: 12),
      Wrap(spacing: 8, runSpacing: 8, children: [
        _Chip(label: c.t('apps.all_categories'), on: category.isEmpty, onTap: () => setState(() => category = '')),
        for (final e in cats)
          _Chip(
            label: '${_tv(c, 'category', e.key)} (${e.value})',
            on: category == e.key,
            onTap: () => setState(() => category = category == e.key ? '' : e.key),
          ),
      ]),
      const SizedBox(height: 18),
      if (loaded && shown.isEmpty)
        Text(c.t(catalog.isEmpty ? 'apps.catalog_empty' : 'apps.no_match'), style: TextStyle(color: p.muted)),
      Wrap(spacing: 18, runSpacing: 18, children: [
        for (final a in shown)
          SizedBox(
            width: grid.unit,
            child: _CatalogCard(
              c: c,
              app: a,
              gpu: gpu,
              busy: busy.contains('${a['id']}'),
              onInstall: () => _install(a),
            ),
          ),
      ]),
    ]);
  }
}

/// Pops with whether to delete the data, or null on cancel.
class _RemoveDialog extends StatefulWidget {
  const _RemoveDialog({required this.c, required this.name, required this.path});

  final Controller c;
  final String name, path;

  @override
  State<_RemoveDialog> createState() => _RemoveDialogState();
}

class _RemoveDialogState extends State<_RemoveDialog> {
  bool deleteData = false;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final c = widget.c;
    return AlertDialog(
      title: Text(c.t('apps.confirm_remove', {'name': widget.name})),
      content: SizedBox(
        width: 420,
        child: CheckboxListTile(
          value: deleteData,
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          title: Text(c.t('apps.delete_data')),
          subtitle: Text(widget.path, style: TextStyle(color: p.muted, fontFamily: 'monospace', fontSize: 12)),
          onChanged: (v) => setState(() => deleteData = v ?? false),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          autofocus: true,
          style: FilledButton.styleFrom(backgroundColor: p.danger, foregroundColor: p.accentFg),
          onPressed: () => Navigator.pop(context, deleteData),
          child: Text(c.t('apps.remove')),
        ),
      ],
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({required this.label, required this.on, required this.onTap});

  final String label;
  final bool on;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Material(
      color: on ? p.navActive : p.sub,
      borderRadius: BorderRadius.circular(999),
      child: InkWell(
        borderRadius: BorderRadius.circular(999),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          child: Text(label, style: TextStyle(color: on ? p.navActiveFg : p.fg, fontSize: 13, fontWeight: FontWeight.w500)),
        ),
      ),
    );
  }
}

/// Small rounded label, like the dashboard's badge().
class _Badge extends StatelessWidget {
  const _Badge(this.text, this.color);

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
        decoration: BoxDecoration(color: color.withValues(alpha: .14), borderRadius: BorderRadius.circular(999)),
        child: Text(text, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
      );
}

Color _statusColor(Palette p, String status) => switch (status) {
      'running' => p.ok,
      'partial' || 'restarting' => p.warn,
      'dead' || 'missing' => p.danger,
      'busy' => p.accent,
      _ => p.muted,
    };

/// Avatar, name and "category · version".
class _Head extends StatelessWidget {
  const _Head({required this.c, required this.app, this.badges = const []});

  final Controller c;
  final Map app;
  final List<Widget> badges;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      AppAvatar(c, app),
      const SizedBox(width: 12),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${app['name'] ?? app['id']}',
              maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
          Text('${_tv(c, 'category', app['category'])} · ${app['version'] ?? ''}',
              maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: p.muted, fontSize: 13)),
          if (badges.isNotEmpty) ...[
            const SizedBox(height: 6),
            Wrap(spacing: 6, runSpacing: 6, children: badges),
          ],
        ]),
      ),
    ]);
  }
}

class _InstalledCard extends StatelessWidget {
  const _InstalledCard({
    required this.c,
    required this.app,
    required this.gpu,
    required this.busy,
    required this.onStart,
    required this.onStop,
    required this.onUpdate,
    required this.onRemove,
  });

  final Controller c;
  final Map app;
  final bool gpu, busy;
  final VoidCallback onStart, onStop, onUpdate, onRemove;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final status = '${app['status'] ?? ''}';
    final update = app['update'];
    final notes = localized(app['notes'], c.settings.lang);
    final link = appUrl(c, app);
    final login = app['login'];
    final running = status == 'running';
    return Glass(
      padding: const EdgeInsets.all(18),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _Head(c: c, app: app, badges: [
          _Badge(_tv(c, 'app_status', status), _statusColor(p, status)),
          if (app['gpu'] == true && gpu) _Badge('GPU', p.accent),
          if (update is Map) _Badge(c.t('apps.update_available', {'version': update['version']}), p.warn),
        ]),
        if (notes.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text(notes, style: TextStyle(color: p.muted, fontSize: 13, height: 1.45)),
        ],
        if (login is Map) _Login(c: c, login: login),
        const SizedBox(height: 14),
        Row(children: [
          if (busy)
            const Padding(
              padding: EdgeInsets.all(12),
              child: SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else ...[
            if (link != null)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: FilledButton.icon(
                  onPressed: running ? () => launchUrl(link) : null,
                  icon: const Icon(Icons.open_in_new_rounded, size: 18),
                  label: Text(c.t('apps.open')),
                ),
              ),
            IconButton(
              tooltip: c.t(running ? 'apps.stop' : 'apps.start'),
              onPressed: running ? onStop : onStart,
              icon: Icon(running ? Icons.stop_rounded : Icons.play_arrow_rounded),
            ),
            IconButton(
              tooltip: c.t('apps.update'),
              onPressed: onUpdate,
              color: update != null ? p.accent : null,
              icon: const Icon(Icons.system_update_alt_rounded),
            ),
            const Spacer(),
            IconButton(
              tooltip: c.t('apps.remove'),
              onPressed: onRemove,
              color: p.danger,
              icon: const Icon(Icons.delete_outline_rounded),
            ),
          ],
        ]),
      ]),
    );
  }
}

/// Default login of an app, hidden until asked for.
class _Login extends StatefulWidget {
  const _Login({required this.c, required this.login});

  final Controller c;
  final Map login;

  @override
  State<_Login> createState() => _LoginState();
}

class _LoginState extends State<_Login> {
  bool open = false;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = [widget.login['user'], widget.login['password']].where((v) => v is String && v.isNotEmpty).join(' / ');
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        TextButton.icon(
          style: TextButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 8), minimumSize: const Size(0, 34)),
          onPressed: () => setState(() => open = !open),
          icon: Icon(open ? Icons.expand_less_rounded : Icons.expand_more_rounded, size: 18),
          label: Text(widget.c.t('apps.login')),
        ),
        if (open)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(8)),
            child: SelectableText(text, style: const TextStyle(fontFamily: 'monospace', fontSize: 13)),
          ),
      ]),
    );
  }
}

class _CatalogCard extends StatelessWidget {
  const _CatalogCard({required this.c, required this.app, required this.gpu, required this.busy, required this.onInstall});

  final Controller c;
  final Map app;
  final bool gpu, busy;
  final VoidCallback onInstall;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final extra = helpers(app);
    final site = app['website'] is String ? Uri.tryParse(app['website'] as String) : null;
    return Glass(
      padding: const EdgeInsets.all(18),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _Head(c: c, app: app),
        const SizedBox(height: 12),
        Text(localized(app['description'], c.settings.lang),
            maxLines: 4, overflow: TextOverflow.ellipsis, style: TextStyle(color: p.muted, fontSize: 13, height: 1.45)),
        if (extra.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(c.t('apps.with_services', {'services': extra}),
              maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(color: p.muted, fontSize: 12)),
        ],
        if (app['gpu'] == true) ...[
          const SizedBox(height: 8),
          Text(c.t(gpu ? 'apps.gpu_used' : 'apps.gpu_none'), style: TextStyle(color: p.muted, fontSize: 12)),
        ],
        const SizedBox(height: 14),
        Row(children: [
          if (site != null) TextButton(onPressed: () => launchUrl(site), child: Text(c.t('apps.website'))),
          const Spacer(),
          if (busy)
            const SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))
          else if (app['installed'] == true)
            _Badge(c.t('apps.is_installed'), p.ok)
          else
            FilledButton.icon(
              onPressed: onInstall,
              icon: const Icon(Icons.download_rounded, size: 18),
              label: Text(c.t('apps.install')),
            ),
        ]),
      ]),
    );
  }
}

/// One button per location; the chosen one is highlighted.
class _Places extends StatelessWidget {
  const _Places({required this.c, required this.current, required this.available, required this.onPick, this.remotes = false});

  final Controller c;
  final String current;
  final Map<String, bool> available;
  final ValueChanged<String> onPick;
  final bool remotes;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final roots = [('system', systemRoot), ('array', arrayRoot), if (remotes) ('remotes', remotesRoot)];
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

/// What the app needs and creates, where its data goes and its folders.
/// Pops with the install body ({location, folders}) or null.
class _InstallDialog extends StatefulWidget {
  const _InstallDialog({required this.c, required this.app});

  final Controller c;
  final Map app;

  @override
  State<_InstallDialog> createState() => _InstallDialogState();
}

class _InstallDialogState extends State<_InstallDialog> {
  /// Whether the array is mounted; null while asking the server.
  bool? pool;
  String location = systemRoot;

  /// Share name -> folder, only for shares the user changed.
  final chosen = <String, Place>{};

  Controller get c => widget.c;
  Map get app => widget.app;

  @override
  void initState() {
    super.initState();
    _pool();
  }

  Future<void> _pool() async {
    bool on = false;
    try {
      final r = await c.server!.get('/api/files?root=${Uri.encodeQueryComponent(arrayRoot)}&path=');
      on = r is Map && r['mounted'] == true;
    } on ApiError {
      on = false;
    } on VersionMismatch {
      on = false;
    }
    if (!mounted) return;
    setState(() {
      pool = on;
      location = defaultLocation(on);
    });
  }

  Place _place(String share) => chosen[share] ?? defaultPlace(location, share);

  Future<void> _change(String share) async {
    final picked = await showDialog<Place>(
      context: context,
      builder: (context) => FolderPicker(c: c, start: _place(share)),
    );
    if (picked != null && mounted) setState(() => chosen[share] = picked);
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final pool = this.pool;
    final shares = app['shares'] is List ? (app['shares'] as List).whereType<Map>().toList() : <Map>[];
    final notes = localized(app['notes'], c.settings.lang);
    final lan = lanPorts(app);
    final extra = helpers(app);
    Widget check(bool ok, String text) => Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(ok ? Icons.check_rounded : Icons.info_outline_rounded, size: 18, color: ok ? p.ok : p.accent),
            const SizedBox(width: 10),
            Expanded(child: Text(text, style: const TextStyle(height: 1.4))),
          ]),
        );
    Widget label(String text) => Padding(
          padding: const EdgeInsets.only(top: 18, bottom: 8),
          child: Text(text, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
        );
    return AlertDialog(
      title: Text(c.t('install.title', {'name': app['name'] ?? app['id']})),
      content: SizedBox(
        width: 560,
        child: pool == null
            ? Busy(c.t('common.loading'))
            : SingleChildScrollView(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                  Text(localized(app['description'], c.settings.lang), style: TextStyle(color: p.muted, height: 1.45)),
                  const SizedBox(height: 14),
                  check(pool, c.t(pool ? 'install.pool_ok' : 'install.pool_missing')),
                  if (app['public_port'] != null) check(true, c.t('install.web', {'port': app['public_port']})),
                  if (lan.isNotEmpty) check(true, c.t('apps.confirm_lan', {'ports': lan})),
                  if (extra.isNotEmpty) check(true, c.t('apps.confirm_services', {'services': extra})),
                  check(true, c.t('install.download')),
                  label(c.t('install.location')),
                  _Places(
                    c: c,
                    current: location,
                    available: {systemRoot: true, arrayRoot: pool},
                    onPick: (r) => setState(() => location = r),
                  ),
                  const SizedBox(height: 8),
                  Text(c.t('install.appdata', {'path': fullPath(location, 'appdata/${app['id']}')}),
                      style: TextStyle(color: p.muted, fontSize: 13)),
                  if (shares.isNotEmpty) ...[
                    label(c.t('install.folders')),
                    Text(c.t('install.folders_hint'), style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
                    const SizedBox(height: 8),
                    for (final share in shares) _shareRow(p, share),
                  ],
                  if (notes.isNotEmpty) ...[
                    const SizedBox(height: 14),
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(color: p.accentSoft, borderRadius: BorderRadius.circular(12)),
                      child: Text(notes, style: const TextStyle(height: 1.45)),
                    ),
                  ],
                ]),
              ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          onPressed: pool == null ? null : () => Navigator.pop(context, installBody(location, chosen)),
          child: Text(c.t('apps.install')),
        ),
      ],
    );
  }

  Widget _shareRow(Palette p, Map share) {
    final name = '${share['name']}';
    final containers = share['containers'] is List ? (share['containers'] as List).join(', ') : name;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
      decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Flexible(child: Text(containers, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600))),
              const SizedBox(width: 8),
              Text(c.t(share['readonly'] == true ? 'install.readonly' : 'install.readwrite'),
                  style: TextStyle(color: p.muted, fontSize: 12)),
            ]),
            const SizedBox(height: 2),
            Text(_place(name).full, style: const TextStyle(fontFamily: 'monospace', fontSize: 13)),
          ]),
        ),
        TextButton(onPressed: () => _change(name), child: Text(c.t('install.change'))),
      ]),
    );
  }
}

/// Folder tree of one location; pops with the chosen folder or null.
/// Files are listed greyed out for orientation, a folder is what gets picked.
class FolderPicker extends StatefulWidget {
  const FolderPicker({super.key, required this.c, required this.start});

  final Controller c;
  final Place start;

  @override
  State<FolderPicker> createState() => _FolderPickerState();
}

class _FolderPickerState extends State<FolderPicker> {
  late Place at = Place(widget.start.root, widget.start.parent);
  Map? data;
  String? error;
  final _name = TextEditingController();

  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _open(at).then((ok) {
      if (!ok && mounted) _open(Place(at.root, ''));
    });
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<bool> _open(Place to) async {
    try {
      final r = await c.server!.get('/api/files?root=${Uri.encodeQueryComponent(to.root)}'
          '&path=${Uri.encodeQueryComponent(to.path)}');
      if (r is! Map || !mounted) return false;
      setState(() {
        at = to;
        data = r;
        error = null;
      });
      return true;
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.key);
      return false;
    } on VersionMismatch {
      return false;
    }
  }

  Future<void> _mkdir() async {
    final name = _name.text.trim();
    if (name.isEmpty) return;
    final path = joinPath(at.path, name);
    try {
      await c.server!.post('/api/files/mkdir', {'root': at.root, 'path': path});
      _name.clear();
      await _open(Place(at.root, path));
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.key);
    } on VersionMismatch {
      return;
    }
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
    final dirs = d == null ? const <String>[] : pickableDirs(d['dirs'] is List ? d['dirs'] as List : const [], at.path);
    final files = d == null || d['files'] is! List ? const <String>[] : (d['files'] as List).whereType<String>().toList();
    final parts = at.path.isEmpty ? const <String>[] : at.path.split('/');
    final canCreate = d != null && d['mounted'] == true && !(at.root == remotesRoot && at.path.isEmpty);
    return AlertDialog(
      title: Text(c.t('folder.title')),
      content: SizedBox(
        width: 520,
        height: 460,
        child: d == null
            ? (error == null ? Busy(c.t('common.loading')) : Text(c.t(error!), style: TextStyle(color: p.danger)))
            : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                _Places(
                  c: c,
                  current: at.root,
                  available: available,
                  remotes: available[remotesRoot] == true,
                  onPick: (r) => _open(Place(r, '')),
                ),
                const SizedBox(height: 12),
                Wrap(crossAxisAlignment: WrapCrossAlignment.center, children: [
                  _crumb(at.root, Place(at.root, '')),
                  for (final (i, part) in parts.indexed) ...[
                    Text('/', style: TextStyle(color: p.muted)),
                    _crumb(part, Place(at.root, parts.sublist(0, i + 1).join('/'))),
                  ],
                ]),
                const SizedBox(height: 6),
                Expanded(
                  child: Container(
                    decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
                    child: ListView(padding: const EdgeInsets.symmetric(vertical: 4), children: [
                      if (d['exists'] == false)
                        _hint(p, c.t('folder.missing'))
                      else if (dirs.isEmpty && files.isEmpty)
                        _hint(p, c.t('folder.empty')),
                      for (final name in dirs)
                        ListTile(
                          dense: true,
                          leading: Icon(Icons.folder_rounded, color: p.accent),
                          title: Text(name),
                          onTap: () => _open(Place(at.root, joinPath(at.path, name))),
                        ),
                      for (final name in files)
                        ListTile(
                          dense: true,
                          enabled: false,
                          leading: Icon(Icons.description_outlined, color: p.muted),
                          title: Text(name, style: TextStyle(color: p.muted)),
                        ),
                    ]),
                  ),
                ),
                const SizedBox(height: 10),
                Row(children: [
                  Expanded(
                    child: TextField(
                      controller: _name,
                      enabled: canCreate,
                      decoration: InputDecoration(hintText: c.t('folder.new_placeholder'), isDense: true),
                      onSubmitted: (_) => _mkdir(),
                    ),
                  ),
                  const SizedBox(width: 10),
                  OutlinedButton(onPressed: canCreate ? _mkdir : null, child: Text(c.t('folder.new'))),
                ]),
                if (error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(c.t(error!), style: TextStyle(color: p.danger)),
                  ),
              ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          onPressed: d != null && at.path.isNotEmpty ? () => Navigator.pop(context, at) : null,
          child: Text(c.t('folder.choose')),
        ),
      ],
    );
  }

  Widget _crumb(String text, Place to) => TextButton(
        style: TextButton.styleFrom(minimumSize: const Size(0, 34), padding: const EdgeInsets.symmetric(horizontal: 6)),
        onPressed: () => _open(to),
        child: Text(text),
      );

  Widget _hint(Palette p, String text) => Padding(
        padding: const EdgeInsets.all(14),
        child: Text(text, style: TextStyle(color: p.muted)),
      );
}
