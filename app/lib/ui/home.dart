// Start page: greeting, health, apps, storage, system and the server update,
// laid out like the dashboard's start screen.

import 'dart:async';
import 'dart:math' as math;

import 'package:material_ui/material_ui.dart';
import 'package:url_launcher/url_launcher.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/server.dart';
import 'bloom.dart';
import 'theme.dart';
import 'widgets.dart';

class HomePage extends StatefulWidget {
  const HomePage(this.c, {super.key});

  final Controller c;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  Map? stats, system, health;
  List<Map> apps = const [];
  double? cpu;
  Map? _lastCpu;
  final _timers = <Timer>[];
  DateTime now = DateTime.now();

  // Server update
  String updateState = 'idle'; // idle, checking, available, none, running, done, failed
  int updateCount = 0;
  int updatePercent = 0;

  Server get s => widget.c.server!;
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _loadStats();
    _loadApps();
    _once();
    _timers
      ..add(Timer.periodic(const Duration(seconds: 5), (_) => _loadStats()))
      ..add(Timer.periodic(const Duration(seconds: 20), (_) => _loadApps()))
      ..add(Timer.periodic(const Duration(seconds: 30), (_) => setState(() => now = DateTime.now())));
  }

  @override
  void dispose() {
    for (final t in _timers) {
      t.cancel();
    }
    super.dispose();
  }

  /// Runs [f]; a lost session leads back to the login, a version change to
  /// the update page (Controller), anything else keeps the old values.
  Future<T?> _call<T>(Future<T> Function() f) async {
    try {
      return await f();
    } on VersionMismatch {
      return null;
    } on ApiError catch (e) {
      if (e.status == 401 && mounted) await c.sessionEnded();
      return null;
    }
  }

  Future<void> _loadStats() async {
    final r = await _call(() => s.get('/api/stats'));
    if (r is! Map || !mounted) return;
    final now = r['cpu'];
    double? pct;
    final last = _lastCpu;
    if (now is Map && last != null) {
      final total = (now['total'] as num) - (last['total'] as num);
      final idle = (now['idle'] as num) - (last['idle'] as num);
      if (total > 0) pct = (1 - idle / total).clamp(0, 1).toDouble();
    }
    setState(() {
      stats = r;
      _lastCpu = now is Map ? now : null;
      cpu = pct ?? cpu;
    });
  }

  Future<void> _loadApps() async {
    final r = await _call(() => s.get('/api/apps?catalog=false'));
    if (r is! Map || !mounted) return;
    final list = r['installed'];
    setState(() => apps = list is List ? list.whereType<Map>().toList() : const []);
  }

  Future<void> _once() async {
    final sys = await _call(() => s.get('/api/system'));
    if (mounted && sys is Map) setState(() => system = sys);
    final h = await _call(() => s.get('/api/health'));
    if (mounted && h is Map) setState(() => health = h);
  }

  Future<void> _checkUpdates() async {
    setState(() => updateState = 'checking');
    final r = await _call(() => s.post('/api/updates/check'));
    if (!mounted) return;
    final pkgs = r is Map && r['packages'] is List ? r['packages'] as List : null;
    setState(() {
      updateCount = pkgs?.length ?? 0;
      updateState = pkgs == null ? 'failed' : (pkgs.isEmpty ? 'none' : 'available');
    });
  }

  /// Starts the server update and follows it. The dashboard restarts during
  /// the update, so unreachable phases are expected; once the new version
  /// answers, the Controller moves to the app update by itself.
  Future<void> _applyUpdates() async {
    setState(() {
      updateState = 'running';
      updatePercent = 0;
    });
    final started = await _call(() => s.post('/api/updates/apply'));
    if (started == null) {
      if (mounted) setState(() => updateState = 'failed');
      return;
    }
    while (mounted && updateState == 'running') {
      await Future<void>.delayed(const Duration(seconds: 2));
      try {
        final p = await s.get('/api/updates/progress');
        if (p is! Map || !mounted) continue;
        setState(() {
          updatePercent = (p['percent'] as num?)?.toInt() ?? updatePercent;
          if (p['state'] == 'done') updateState = 'done';
          if (p['state'] == 'failed') updateState = 'failed';
        });
      } on VersionMismatch {
        return;
      } on ApiError catch (e) {
        if (e.status == 401) {
          await c.sessionEnded();
          return;
        }
        // 0 or 502 while the dashboard restarts: keep waiting.
      }
    }
  }

  String _greeting() {
    final h = now.hour;
    final key = h < 5
        ? 'home.greeting_night'
        : h < 11
            ? 'home.greeting_morning'
            : h < 18
                ? 'home.greeting_day'
                : h < 22
                    ? 'home.greeting_evening'
                    : 'home.greeting_night';
    return c.t(key, {'name': displayName(c.user ?? '')});
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final width = MediaQuery.of(context).size.width;
    final pad = math.max(16.0, math.min(48.0, width * .04));
    final findings = health?['findings'];
    final attention = findings is List
        ? findings.whereType<Map>().where((f) => f['level'] == 'critical' || f['level'] == 'warning').length
        : 0;
    return SingleChildScrollView(
      padding: EdgeInsets.fromLTRB(pad, 16, pad, 56),
      child: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1240),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            _TopBar(c),
            const SizedBox(height: 56),
            Rise(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(longDate(now, c.settings.lang), style: TextStyle(color: p.muted, fontSize: 15)),
                const SizedBox(height: 6),
                Text(_greeting(),
                    style: const TextStyle(fontSize: 44, fontWeight: FontWeight.w700, letterSpacing: -1, height: 1.1)),
                const SizedBox(height: 10),
                if (health != null)
                  Row(children: [
                    Icon(attention == 0 ? Icons.local_florist_rounded : Icons.error_outline_rounded,
                        size: 18, color: attention == 0 ? p.ok : p.warn),
                    const SizedBox(width: 8),
                    Text(attention == 0 ? c.t('home.all_ok') : c.t('home.attention', {'n': attention}),
                        style: TextStyle(color: p.muted, fontSize: 15)),
                  ]),
              ]),
            ),
            const SizedBox(height: 34),
            Wrap(spacing: 18, runSpacing: 18, children: [
              _tile(0, width, wide: true, child: _apps(p)),
              _tile(1, width, child: _storage(p)),
              _tile(2, width, child: _system(p)),
              _tile(3, width, child: _serverUpdate(p)),
            ]),
          ]),
        ),
      ),
    );
  }

  Widget _tile(int i, double width, {required Widget child, bool wide = false}) {
    final content = math.min(1240.0, width - 2 * math.max(16.0, math.min(48.0, width * .04)));
    final cols = content > 980 ? 3 : (content > 640 ? 2 : 1);
    final unit = (content - 18 * (cols - 1)) / cols;
    final w = wide && cols > 1 ? unit * 2 + 18 : unit;
    return SizedBox(width: w, child: Rise(delay: 60 * i, child: Glass(child: child)));
  }

  Widget _title(String text, [String? side]) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Row(children: [
          Text(text, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
          const Spacer(),
          if (side != null) Text(side, style: TextStyle(color: context.palette.muted, fontSize: 13)),
        ]),
      );

  Widget _apps(Palette p) {
    final running = apps.where((a) => a['status'] == 'running').length;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _title(c.t('home.apps'), apps.isEmpty ? null : c.t('home.apps_running', {'n': running, 'total': apps.length})),
      if (apps.isEmpty) Text(c.t('home.apps_none'), style: TextStyle(color: p.muted)),
      Wrap(spacing: 10, runSpacing: 10, children: [for (final a in apps) _AppTile(c, a)]),
    ]);
  }

  Map? _pool() {
    final fs = stats?['filesystems'];
    if (fs is! List) return null;
    final list = fs.whereType<Map>();
    return list.where((f) => f['mountpoint'] == '/mnt/user').firstOrNull ??
        list.where((f) => f['mountpoint'] == '/').firstOrNull;
  }

  Widget _storage(Palette p) {
    final fs = _pool();
    final used = (fs?['used'] as num?)?.toDouble() ?? 0;
    final size = (fs?['size'] as num?)?.toDouble() ?? 0;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _title(c.t('home.storage')),
      if (fs != null) ...[
        Text(size > 0 ? '${(used / size * 100).round()} %' : '–',
            style: const TextStyle(fontSize: 34, fontWeight: FontWeight.w700, letterSpacing: -.5)),
        const SizedBox(height: 4),
        Text(c.t('home.of_size', {'used': bytes(used, c.t), 'size': bytes(size, c.t)}),
            style: TextStyle(color: p.muted)),
        const SizedBox(height: 14),
        Meter(size > 0 ? used / size : 0, height: 10),
      ] else
        const Busy(''),
    ]);
  }

  Widget _system(Palette p) {
    final mem = stats?['memory'];
    final total = mem is Map ? (mem['total'] as num?)?.toDouble() : null;
    final avail = mem is Map ? (mem['available'] as num?)?.toDouble() : null;
    final memUse = total != null && avail != null && total > 0 ? (total - avail) / total : null;
    final up = (stats?['uptime_seconds'] as num?)?.toInt();
    final version = system?['version'] ?? s.serverVersion;
    Widget row(String label, double? v, String value) => Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [Text(label), const Spacer(), Text(value, style: TextStyle(color: p.muted))]),
            const SizedBox(height: 8),
            Meter(v ?? 0),
          ]),
        );
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _title(c.t('home.system'), version == null ? null : c.t('home.version', {'version': version})),
      row(c.t('home.cpu'), cpu, cpu == null ? '–' : '${(cpu! * 100).round()} %'),
      row(c.t('home.memory'), memUse,
          total == null || avail == null ? '–' : c.t('home.of_size', {'used': bytes(total - avail, c.t), 'size': bytes(total, c.t)})),
      if (up != null) Text(c.t('home.uptime', {'time': duration(up, c.t)}), style: TextStyle(color: p.muted)),
    ]);
  }

  Widget _serverUpdate(Palette p) {
    final version = s.serverVersion ?? '';
    final Widget status = switch (updateState) {
      'checking' => Busy(c.t('server_update.checking')),
      'available' => Text(c.t('server_update.available', {'n': updateCount})),
      'none' => Text(c.t('server_update.none', {'version': version})),
      'running' => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c.t('server_update.running', {'percent': updatePercent})),
          const SizedBox(height: 10),
          Meter(updatePercent / 100),
        ]),
      'done' => Text(c.t('server_update.done')),
      'failed' => Text(c.t('server_update.failed'), style: TextStyle(color: p.danger)),
      _ => Text(c.t('home.version', {'version': version}), style: TextStyle(color: p.muted)),
    };
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _title(c.t('server_update.title')),
      status,
      const SizedBox(height: 10),
      Text(c.t('server_update.then_app'), style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
      const SizedBox(height: 14),
      if (updateState == 'available')
        FilledButton(onPressed: _applyUpdates, child: Text(c.t('server_update.apply')))
      else if (updateState != 'running' && updateState != 'checking')
        OutlinedButton(onPressed: _checkUpdates, child: Text(c.t('server_update.check'))),
    ]);
  }
}

class _AppTile extends StatelessWidget {
  const _AppTile(this.c, this.app);

  final Controller c;
  final Map app;

  Uri? get url {
    final port = app['public_port'];
    if (port == null) return null;
    final urls = app['urls'];
    final mdns = urls is Map ? urls['mdns'] : null;
    if (mdns is String) return Uri.tryParse(mdns);
    final host = c.settings.host!.replaceFirst(RegExp(r':\d+$'), '');
    return Uri.parse('http://$host:$port');
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final id = '${app['id'] ?? ''}';
    final name = '${app['name'] ?? id}';
    final status = '${app['status'] ?? ''}';
    final link = url;
    final hue = id.codeUnits.fold<int>(0, (h, ch) => (h * 31 + ch) % 360).toDouble();
    final state = app['update'] == true
        ? c.t('app.update')
        : c.strings.has('app.$status')
            ? c.t('app.$status')
            : status;
    final color = app['update'] == true ? p.warn : (status == 'running' ? p.ok : p.muted);
    return Material(
      color: p.sub,
      borderRadius: BorderRadius.circular(18),
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: link != null && status == 'running' ? () => launchUrl(link) : null,
        child: SizedBox(
          width: 118,
          height: 118,
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Container(
              width: 44,
              height: 44,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: HSLColor.fromAHSL(1, hue, .45, .45).toColor(),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(name.isEmpty ? '?' : name.substring(0, 1).toUpperCase(),
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 18)),
            ),
            const SizedBox(height: 10),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w500)),
            ),
            Text(state, style: TextStyle(color: color, fontSize: 12)),
          ]),
        ),
      ),
    );
  }
}

class _TopBar extends StatelessWidget {
  const _TopBar(this.c);

  final Controller c;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Glass(
      radius: 18,
      padding: const EdgeInsets.fromLTRB(18, 8, 10, 8),
      child: Row(children: [
        const DahliaMark(size: 28),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            const Text('Dahlia', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
            Text(c.settings.host ?? '', overflow: TextOverflow.ellipsis, style: TextStyle(color: p.muted, fontSize: 12)),
          ]),
        ),
        Container(
          height: 44,
          padding: const EdgeInsets.symmetric(horizontal: 18),
          alignment: Alignment.center,
          decoration: BoxDecoration(color: p.navActive, borderRadius: BorderRadius.circular(12)),
          child: Text(c.t('nav.overview'), style: TextStyle(color: p.navActiveFg, fontWeight: FontWeight.w500, fontSize: 14)),
        ),
        const SizedBox(width: 4),
        PopupMenuButton<String>(
          tooltip: '',
          position: PopupMenuPosition.under,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          icon: Icon(Icons.account_circle_rounded, color: p.navActiveFg),
          onSelected: (v) {
            switch (v) {
              case 'en' || 'de':
                c.setLanguage(v);
              case 'server':
                c.otherServer();
              case 'logout':
                c.logout();
            }
          },
          itemBuilder: (context) => [
            PopupMenuItem(enabled: false, child: Text(displayName(c.user ?? ''), style: const TextStyle(fontWeight: FontWeight.w600))),
            const PopupMenuDivider(),
            CheckedPopupMenuItem(value: 'en', checked: c.settings.lang == 'en', child: const Text('English')),
            CheckedPopupMenuItem(value: 'de', checked: c.settings.lang == 'de', child: const Text('Deutsch')),
            const PopupMenuDivider(),
            PopupMenuItem(value: 'server', child: Text(c.t('menu.other_server'))),
            PopupMenuItem(value: 'logout', child: Text(c.t('menu.logout'), style: TextStyle(color: p.danger))),
          ],
        ),
      ]),
    );
  }
}
