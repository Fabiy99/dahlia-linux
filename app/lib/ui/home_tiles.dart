// The tiles of the start page, drawn like the dashboard's .home-tiles
// (index.html, style.css): weather, apps, storage, system, recently done,
// network, disk temperatures, health check, processor, memory and history.

import 'dart:math' as math;

import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';
import 'package:url_launcher/url_launcher.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/letters.dart';
import 'app_tiles.dart';
import 'area.dart';
import 'letters.dart';
import 'shell.dart';
import 'theme.dart';

/// Everything the tiles show; the page loads it and rebuilds the tiles.
class HomeData {
  Map? stats, lastStats, system, health, hardware, jobs, history;

  /// Newest weekly letter (or the running week) and the saved ones.
  Map? letter;
  List<Map> letters = const [];
  List<Map> apps = const [];
  List<Map> weather = const [];
  Object? weatherError;
  int activePlace = 0;
  String historyRange = 'day';

  /// CPU and memory use in percent, the last 60 samples (HISTORY in app.js).
  final cpuHistory = <double>[];
  final memHistory = <double>[];
  int cpuPct = 0;

  /// Seconds between the last two stats samples.
  double seconds = 0;
}

const historyRanges = ['hour', 'day', 'week', 'month', 'year'];
const _info = (Color(0xFF2F6DB5), Color(0xFF7FB2F0));

/// Colour of the dashboard's level(): crit from 90 %, warn from 75 %.
Color levelColor(Palette p, int pct, {Color? normal}) => pct >= 90 ? p.danger : (pct >= 75 ? p.warn : (normal ?? p.ok));

int percent(num used, num size) => size > 0 ? math.min(100, (used / size * 100).round()) : 0;

Color infoColor(BuildContext context) => Theme.of(context).brightness == Brightness.dark ? _info.$2 : _info.$1;

/// Builds the tiles' contents for a given inner width.
class HomeTiles {
  HomeTiles(this.context, this.c, this.d, {required this.onRange, required this.onPlace, required this.onHealth});

  final BuildContext context;
  final Controller c;
  final HomeData d;
  final ValueChanged<String> onRange;
  final ValueChanged<int> onPlace;
  final VoidCallback onHealth;

  Palette get p => context.palette;
  String t(String key, [Map<String, Object?> vars = const {}]) => c.t(key, vars);

  static const titles = {
    'weather': 'weather.title',
    'apps': 'overview.apps',
    'storage': 'overview.storage',
    'system': 'overview.system',
    'jobs': 'home.recent',
    'network': 'overview.network',
    'disks': 'home.temps',
    'health': 'check.title',
    'cpu': 'overview.cpu',
    'memory': 'overview.memory',
    'history': 'history.title',
    'letter': 'letter.title',
    'photos': 'home.widget_photos',
    'media': 'home.widget_media',
    'downloads': 'home.widget_downloads',
  };

  /// Content of tile [id] at [width] (inside the padding).
  List<Widget> build(String id, double width) => switch (id) {
        'weather' => weather(width),
        'apps' => apps(width),
        'storage' => storage(),
        'system' => system(),
        'jobs' => jobs(),
        'network' => network(),
        'disks' => disks(),
        'health' => health(),
        'cpu' => cpu(),
        'memory' => memory(),
        'history' => history(width),
        'letter' => letter(),
        'photos' || 'media' || 'downloads' => [
            header(id),
            const SizedBox(height: 12),
            AppContentTile(c, id, apps: d.apps, key: ValueKey('tile-$id')),
          ],
        _ => const [],
      };

  // --- pieces ------------------------------------------------------------------

  Widget header(String id, {String? note, Widget? side}) => Row(children: [
        Text(t(titles[id]!), style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: p.fg)),
        const Spacer(),
        if (note != null) Text(note, style: TextStyle(fontSize: 13, color: p.muted)),
        ?side,
      ]);

  Widget empty(String text) => Text(text, style: TextStyle(color: p.muted, fontSize: 13.5));

  Widget row(String label, String value, {Color? color, bool bold = false, String? tooltip}) {
    final l = Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 14));
    return Row(children: [
      Expanded(child: tooltip == null ? l : Tooltip(message: tooltip, child: l)),
      const SizedBox(width: 12),
      Text(value,
          style: TextStyle(
            fontSize: 14,
            color: color,
            fontWeight: bold ? FontWeight.w600 : null,
            fontFeatures: const [FontFeature.tabularFigures()],
          )),
    ]);
  }

  Widget rows(List<Widget> children) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (var i = 0; i < children.length; i++) ...[if (i > 0) const SizedBox(height: 8), children[i]],
        ],
      );

  Widget facts(List<(String, String)> list, {double size = 13}) => Table(
        columnWidths: const {0: IntrinsicColumnWidth(), 1: FlexColumnWidth()},
        defaultVerticalAlignment: TableCellVerticalAlignment.top,
        children: [
          for (final (k, v) in list)
            TableRow(children: [
              Padding(
                padding: const EdgeInsets.only(right: 16, bottom: 5),
                child: Text(t(k), style: TextStyle(color: p.muted, fontSize: size)),
              ),
              Padding(padding: const EdgeInsets.only(bottom: 5), child: Text(v, style: TextStyle(fontSize: size))),
            ]),
        ],
      );

  /// Chip of the weather places (.chip-row.small .chip on the start screen).
  Widget chip(String text, bool on, VoidCallback onTap) => DahliaChip(text: text, on: on, onTap: onTap, height: 44);

  // --- weather -------------------------------------------------------------------

  String deg(Object? v) => v is num ? '${v.round()}°' : '–';

  List<Widget> weather(double width) {
    final list = d.weather;
    if (d.weatherError != null) return [header('weather'), empty(t('error.network'))];
    if (list.isEmpty) return [header('weather'), empty(t('weather.no_places'))];
    final i = d.activePlace < list.length ? d.activePlace : 0;
    final w = list[i];
    final tabs = Wrap(spacing: 6, runSpacing: 6, children: [
      for (var k = 0; k < list.length; k++) chip('${list[k]['name']}', k == i, () => onPlace(k)),
    ]);
    if (w['error'] != null) return [header('weather'), tabs, empty(t('weather.unavailable'))];
    final days = (w['days'] is List ? w['days'] as List : const []).whereType<Map>().skip(1).take(3).toList();
    final dayW = (width - 20) / 3;
    return [
      header('weather'),
      tabs,
      Row(children: [
        Text(deg(w['temperature']), style: const TextStyle(fontSize: 56, fontWeight: FontWeight.w700, letterSpacing: -2, height: 1)),
        const SizedBox(width: 18),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(t('weather.kind_${w['kind']}'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
            const SizedBox(height: 3),
            Text(
              t('weather.details', {'wind': ((w['wind'] as num?) ?? 0).round(), 'humidity': ((w['humidity'] as num?) ?? 0).round()}),
              style: TextStyle(fontSize: 14, color: p.muted),
            ),
          ]),
        ),
      ]),
      if (days.isNotEmpty)
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          for (var k = 0; k < days.length; k++) ...[
            if (k > 0) const SizedBox(width: 10),
            Container(
              width: dayW,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(14)),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(_weekday('${days[k]['date']}'), style: TextStyle(color: p.muted, fontSize: 12)),
                const SizedBox(height: 2),
                Text('${deg(days[k]['max'])} / ${deg(days[k]['min'])}',
                    style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text(t('weather.kind_${days[k]['kind']}'), style: TextStyle(color: p.muted, fontSize: 12)),
              ]),
            ),
          ],
        ]),
    ];
  }

  String _weekday(String date) {
    final day = DateTime.tryParse('${date}T12:00:00');
    if (day == null) return date;
    return DateFormat.E(c.settings.lang).format(day).replaceAll('.', '');
  }

  // --- apps ----------------------------------------------------------------------

  List<Widget> apps(double width) {
    final list = d.apps;
    final running = list.where((a) => a['status'] == 'running').length;
    final cols = math.max(1, ((width + 14) / (120 + 14)).floor());
    final cell = (width - 14 * (cols - 1)) / cols;
    return [
      header('apps', note: list.isEmpty ? null : t('home.apps_running', {'n': running, 'total': list.length})),
      if (list.isEmpty)
        empty(t('apps.none_installed'))
      else
        Wrap(spacing: 14, runSpacing: 14, children: [for (final a in list) SizedBox(width: cell, child: AppTile(c, a))]),
    ];
  }

  // --- storage -------------------------------------------------------------------

  static const _segments = [Color(0xFFE07AB9), Color(0xFFB98BE0), Color(0xFF9FD8FF), Color(0xFF8FE0B0)];

  (String, int) fsLabel(String mp) {
    if (mp == '/') return (t('fs.system'), 0);
    if (mp == '/mnt/cache') return (t('fs.cache'), 3);
    final disk = RegExp(r'^/mnt/disk(\d+)$').firstMatch(mp);
    if (disk != null) return (t('fs.disk', {'n': disk[1]}), 10 + int.parse(disk[1]!));
    return (mp, 300);
  }

  List<Widget> storage() {
    final fs = (d.stats?['filesystems'] is List ? d.stats!['filesystems'] as List : const []).whereType<Map>();
    var list = fs.where((f) => RegExp(r'^/mnt/(disk\d+|cache)$').hasMatch('${f['mountpoint']}')).toList();
    if (list.isEmpty) list = fs.where((f) => f['mountpoint'] == '/').toList();
    final labelled = [for (final f in list) (f, fsLabel('${f['mountpoint']}'))]..sort((a, b) => a.$2.$2 - b.$2.$2);
    if (d.stats == null) return [header('storage')];
    if (labelled.isEmpty) return [header('storage'), empty(t('overview.no_fs'))];
    final used = labelled.fold<num>(0, (n, e) => n + (e.$1['used'] as num? ?? 0));
    final size = labelled.fold<num>(0, (n, e) => n + (e.$1['size'] as num? ?? 0));
    final sync = d.jobs?['snapraid-sync'];
    final when = sync is Map && sync['result'] == 'success' ? whenDone('${sync['finished']}') : null;
    return [
      header('storage'),
      Wrap(crossAxisAlignment: WrapCrossAlignment.end, spacing: 8, children: [
        Text(bytes(used, c.t), style: const TextStyle(fontSize: 40, fontWeight: FontWeight.w700, letterSpacing: -1, height: 1.15)),
        Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Text(t('home.of_size', {'size': bytes(size, c.t)}), style: TextStyle(fontSize: 15, color: p.muted)),
        ),
      ]),
      ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: Container(
          height: 12,
          color: p.track,
          child: Row(children: [
            for (var i = 0; i < labelled.length; i++) ...[
              if (i > 0) const SizedBox(width: 2),
              Expanded(
                flex: math.max(1, ((labelled[i].$1['used'] as num? ?? 0) / (size > 0 ? size : 1) * 1000).round()),
                child: Container(
                  color: percent(labelled[i].$1['used'] as num? ?? 0, labelled[i].$1['size'] as num? ?? 0) >= 90
                      ? const Color(0xFFF0A63A)
                      : _segments[i % 4],
                ),
              ),
            ],
            Expanded(flex: math.max(0, ((size - used) / (size > 0 ? size : 1) * 1000).round()), child: const SizedBox()),
          ]),
        ),
      ),
      rows([
        for (final (f, (label, _)) in labelled)
          () {
            final pct = percent(f['used'] as num? ?? 0, f['size'] as num? ?? 0);
            return row(label, '$pct %', color: pct >= 90 ? p.warn : null, bold: pct >= 90);
          }(),
      ]),
      if (when != null) Text(t('home.parity_synced', {'when': when}), style: TextStyle(fontSize: 13, color: p.muted)),
    ];
  }

  /// systemd time stamp: time of day today, "yesterday", or the date (whenDone()).
  String? whenDone(String stamp) {
    final m = RegExp(r'(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)').firstMatch(stamp);
    if (m == null) return null;
    final day = DateTime(int.parse(m[1]!), int.parse(m[2]!), int.parse(m[3]!));
    final now = DateTime.now();
    final days = DateTime(now.year, now.month, now.day).difference(day).inHours / 24;
    if (days.round() == 0) return '${m[4]}:${m[5]}';
    if (days.round() == 1) return t('home.yesterday');
    return DateFormat('d. MMM', c.settings.lang).format(day);
  }

  // --- system --------------------------------------------------------------------

  Widget meter(String label, String value, int pct) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: const TextStyle(fontSize: 14)),
          const SizedBox(width: 8),
          Expanded(child: Text(value, textAlign: TextAlign.right, style: const TextStyle(fontSize: 14))),
        ]),
        const SizedBox(height: 6),
        GradientMeter(pct),
      ]);

  num? get cpuTemp => d.hardware?['cpu_temperature'] as num?;

  List<Widget> system() {
    final s = d.stats;
    if (s == null) return [header('system')];
    final mem = s['memory'] as Map? ?? const {};
    final total = mem['total'] as num? ?? 0;
    final used = total - (mem['available'] as num? ?? 0);
    final up = (s['uptime_seconds'] as num?)?.toInt() ?? 0;
    final info = d.system ?? const {};
    final nic = (s['network'] as List? ?? const []).whereType<Map>().where((n) => (n['addresses'] as List? ?? const []).isNotEmpty).firstOrNull;
    final board = d.hardware?['board'];
    final boardName = board is Map
        ? [board['board_vendor'], board['board_name']].whereType<String>().where((x) => x.isNotEmpty).join(' ')
        : '';
    final temp_ = cpuTemp;
    return [
      header('system'),
      meter(t('overview.cpu'), '${d.cpuPct} %', d.cpuPct),
      meter(t('overview.memory'), t('home.of_amount', {'used': bytes(used, c.t), 'size': bytes(total, c.t)}), percent(used, total)),
      Wrap(spacing: 24, runSpacing: 8, children: [
        if (temp_ != null) Text(temp(temp_), style: TextStyle(fontSize: 14, color: Color.lerp(p.fg, p.muted, .18))),
        Text(t('home.uptime', {'time': uptime(up, c.t)}), style: TextStyle(fontSize: 14, color: Color.lerp(p.fg, p.muted, .18))),
      ]),
      Details(
        label: t('overview.details'),
        child: facts([
          ('overview.hostname', '${info['hostname'] ?? ''}.local'),
          ('overview.version', '${info['version'] ?? '-'}'),
          ('overview.kernel', '${info['kernel'] ?? '-'}'),
          ('overview.cpu_model', '${(s['cpu'] as Map?)?['model'] ?? '-'}'),
          ('overview.address', nic == null ? (c.settings.host ?? '') : '${(nic['addresses'] as List).first}'.split('/').first),
          if (boardName.isNotEmpty) ('overview.board', boardName),
        ]),
      ),
    ];
  }

  // --- recently done ---------------------------------------------------------------

  List<Widget> jobs() {
    final j = d.jobs;
    if (j == null) return [header('jobs')];
    return [
      header('jobs'),
      rows([
        for (final e in j.entries)
          if (e.value is Map)
            () {
              final s = e.value as Map;
              final active = s['active'];
              final result = s['result'];
              if (active == 'active' || active == 'activating') {
                return row(t('job.${e.key}'), tv(c, 'job_state', active), color: infoColor(context));
              }
              if (active == 'failed' || (result != null && result != 'success')) {
                return row(t('job.${e.key}'), tv(c, 'job_result', result ?? 'exit-code'), color: p.danger, bold: true);
              }
              final when = whenDone('${s['finished'] ?? ''}');
              return row(t('job.${e.key}'), when ?? t('jobs.never'), color: when != null ? p.ok : null);
            }(),
      ]),
    ];
  }

  // --- weekly letter -----------------------------------------------------------------

  List<Widget> letter() {
    final l = d.letter;
    final newest = d.letters.where((x) => x['kind'] == 'week').firstOrNull;
    final all = TextButton(
      style: TextButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 8), minimumSize: const Size(0, 32)),
      onPressed: () => showDialog<void>(
        context: context,
        builder: (context) => LetterDialog(c, saved: d.letters, first: '${newest?['id'] ?? 'week'}'),
      ),
      child: Text(t('letter.all')),
    );
    if (l == null) return [header('letter', side: all)];
    return [
      Row(children: [
        Expanded(
          child: Text(letterTitle(l, t), style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: p.fg)),
        ),
        all,
      ]),
      const SizedBox(height: 2),
      Text(letterWhen(l, t, c.settings.lang), style: TextStyle(color: p.muted, fontSize: 13)),
      const SizedBox(height: 12),
      LetterLines(c, l),
    ];
  }

  // --- network -------------------------------------------------------------------

  List<Widget> network() {
    final list = (d.stats?['network'] as List? ?? const []).whereType<Map>().toList();
    if (d.stats == null) return [header('network')];
    final n = list.where((x) => (x['addresses'] as List? ?? const []).isNotEmpty).firstOrNull ?? list.firstOrNull;
    if (n == null) return [header('network'), empty(t('overview.no_network'))];
    final before = (d.lastStats?['network'] as List? ?? const []).whereType<Map>().where((x) => x['name'] == n['name']).firstOrNull;
    String r(String key) {
      final now = n[key] as num?, then = before?[key] as num?;
      return now != null && then != null && d.seconds > 0 ? rate(math.max(0, (now - then) / d.seconds), c.t) : '…';
    }

    Widget figure(String value, String key) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(value, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700)),
          Text(t(key), style: TextStyle(fontSize: 13, color: p.muted)),
        ]);
    final addrs = n['addresses'] as List? ?? const [];
    final addr = addrs.isEmpty ? t('overview.no_address') : '${addrs.first}'.split('/').first;
    return [
      header('network'),
      Wrap(spacing: 28, runSpacing: 8, children: [figure(r('rx_bytes'), 'home.down'), figure(r('tx_bytes'), 'home.up')]),
      Text('${n['name']} · $addr', style: TextStyle(fontSize: 13, color: p.muted)),
    ];
  }

  // --- disk temperatures -------------------------------------------------------------

  List<Widget> disks() {
    final hw = d.hardware;
    if (hw == null) return [header('disks'), empty(t('common.loading'))];
    final list = (hw['disks'] as List? ?? const []).whereType<Map>().toList();
    if (list.isEmpty) return [header('disks'), empty(t('storage.none'))];
    return [
      header('disks'),
      rows([
        for (final disk in list)
          () {
            final c_ = disk['temperature'] as num?;
            final kind = tempKind(c_, disk['rotational'] == true);
            final color = switch (kind) { 'crit' => p.danger, 'warn' => p.warn, 'ok' => p.ok, _ => null };
            return row('${disk['path']}'.replaceFirst('/dev/', ''), c_ == null ? '–' : temp(c_),
                color: color, bold: kind == 'warn' || kind == 'crit', tooltip: disk['model'] as String?);
          }(),
      ]),
    ];
  }

  // --- health check ------------------------------------------------------------------

  static const checkArea = {
    'disks': Area.storage,
    'usage': Area.storage,
    'array': Area.storage,
    'parity': Area.parity,
    'services': Area.log,
    'time': Area.settings,
    'updates': Area.settings,
    'backup': Area.backup,
    'notify': Area.settings,
    'firewall': Area.settings,
    'unraid': Area.storage,
  };

  Color levelOf(String? level) => switch (level) { 'critical' => p.danger, 'warning' => p.warn, _ => infoColor(context) };

  List<Widget> health() {
    final h = d.health;
    final link = TextButton(
      style: TextButton.styleFrom(foregroundColor: p.accent, minimumSize: const Size(0, 32), padding: EdgeInsets.zero),
      onPressed: onHealth,
      child: Text(t('check.again'), style: const TextStyle(fontSize: 13)),
    );
    if (h == null) return [header('health', side: link), empty(t('check.running'))];
    final findings = (h['findings'] as List? ?? const []).whereType<Map>().toList();
    final passed = (h['passed'] as List? ?? const []).length;
    final skipped = (h['skipped'] as List? ?? const []).cast<Object?>();
    int count(String l) => findings.where((f) => f['level'] == l).length;
    final parts = [
      for (final (l, key) in const [('critical', 'check.n_critical'), ('warning', 'check.n_warning'), ('info', 'check.n_info')])
        if (count(l) > 0) t(key, {'n': count(l)}),
    ];
    var summary = parts.isEmpty ? t('check.all_ok', {'n': passed}) : parts.join(' · ');
    if (skipped.isNotEmpty) {
      summary += ' · ${t('check.skipped', {'checks': skipped.map((x) => t('check.area_$x')).join(', ')})}';
    }
    return [
      header('health', side: link),
      Text(summary, style: TextStyle(color: parts.isEmpty ? p.ok : p.muted)),
      for (final f in findings)
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(10)),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Tag(t('check.level_${f['level']}'), levelOf(f['level'] as String?)),
            const SizedBox(width: 10),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(t('check.${f['id']}', _params(f)), style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(t('check.${f['id']}_hint', _params(f)), style: TextStyle(color: p.muted, fontSize: 13)),
              ]),
            ),
            if (checkArea[f['check']] != null)
              OutlinedButton(onPressed: () => c.go(checkArea[f['check']]!), child: Text(t('check.open'))),
          ]),
        ),
    ];
  }

  static Map<String, Object?> _params(Map f) => f['params'] is Map ? Map<String, Object?>.from(f['params'] as Map) : const {};

  // --- processor and memory ------------------------------------------------------------

  Widget gaugeRow(int pct, String sub, List<(String, String)> list) => Row(children: [
        Gauge(pct, sub),
        const SizedBox(width: 19),
        Expanded(child: facts(list, size: 12.75)),
      ]);

  List<Widget> cpu() {
    final s = d.stats;
    if (s == null) return [header('cpu')];
    final load = (s['load'] as List? ?? const []).map((x) => (x as num).toStringAsFixed(2)).join(' / ');
    final temp_ = cpuTemp;
    return [
      header('cpu'),
      gaugeRow(d.cpuPct, t('overview.cpu_busy'), [
        ('overview.cores', '${(s['cpu'] as Map?)?['cores'] ?? '-'}'),
        ('overview.load', load),
        ('overview.uptime', uptime((s['uptime_seconds'] as num?)?.toInt() ?? 0, c.t)),
        if (temp_ != null) ('overview.temperature', temp(temp_)),
      ]),
      SizedBox(height: 42, child: CustomPaint(painter: SparkPainter(d.cpuHistory, p.accent), size: Size.infinite)),
    ];
  }

  List<Widget> memory() {
    final s = d.stats;
    if (s == null) return [header('memory')];
    final mem = s['memory'] as Map? ?? const {};
    final total = mem['total'] as num? ?? 0;
    final used = total - (mem['available'] as num? ?? 0);
    final swap = s['swap'] as Map? ?? const {};
    final swapTotal = swap['total'] as num? ?? 0;
    return [
      header('memory'),
      gaugeRow(percent(used, total), t('overview.mem_used'), [
        ('overview.mem_used', bytes(used, c.t)),
        ('overview.mem_total', bytes(total, c.t)),
        (
          'overview.swap',
          swapTotal > 0
              ? t('overview.used_of', {
                  'used': bytes(swap['used'] as num? ?? 0, c.t),
                  'size': bytes(swapTotal, c.t),
                  'pct': percent(swap['used'] as num? ?? 0, swapTotal),
                })
              : t('overview.swap_none'),
        ),
      ]),
      SizedBox(height: 42, child: CustomPaint(painter: SparkPainter(d.memHistory, p.accent), size: Size.infinite)),
    ];
  }

  // --- history ---------------------------------------------------------------------------

  List<Widget> history(double width) {
    final h = d.history;
    final seg = Container(
      decoration: BoxDecoration(border: Border.all(color: p.line), borderRadius: BorderRadius.circular(8)),
      clipBehavior: Clip.antiAlias,
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        for (var i = 0; i < historyRanges.length; i++)
          InkWell(
            onTap: () => onRange(historyRanges[i]),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 5),
              decoration: BoxDecoration(
                color: d.historyRange == historyRanges[i] ? p.accentSoft : null,
                border: i > 0 ? Border(left: BorderSide(color: p.line)) : null,
              ),
              child: Text(tv(c, 'history_range', historyRanges[i]),
                  style: TextStyle(
                    fontSize: 12.3,
                    color: d.historyRange == historyRanges[i] ? p.accent : p.fg,
                    fontWeight: d.historyRange == historyRanges[i] ? FontWeight.w600 : null,
                  )),
            ),
          ),
      ]),
    );
    final head = Row(children: [
      Text(t('history.title'), style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: p.fg)),
      const SizedBox(width: 16),
      seg,
    ]);
    if (h == null) return [head];
    final series = h['series'] is Map ? Map<String, Object?>.from(h['series'] as Map) : <String, Object?>{};
    if (series.isEmpty) return [head, empty(t('history.empty'))];
    final points = (h['points'] as num?)?.toInt() ?? 0;
    final start = (h['start'] as num?)?.toInt() ?? 0;
    final step = (h['step'] as num?)?.toInt() ?? 1;
    List<double?>? line(String k) => series[k] is List ? [for (final v in series[k] as List) (v as num?)?.toDouble()] : null;
    List<double?>? sum(String prefix) {
      final keys = series.keys.where((k) => k.startsWith(prefix)).toList();
      if (keys.isEmpty) return null;
      return [
        for (var i = 0; i < points; i++)
          () {
            final vals = [for (final k in keys) line(k)![i]].whereType<double>();
            return vals.isEmpty ? null : vals.reduce((a, b) => a + b);
          }(),
      ];
    }

    String pct(double v) => '${v.round()} %';
    final temp_ = line('temp_cpu');
    final charts = [
      Chart(title: t('history.cpu'), lines: [line('cpu')], max: 100, fmt: pct, start: start, step: step, points: points, range: d.historyRange, c: c),
      Chart(title: t('history.mem'), lines: [line('mem')], max: 100, fmt: pct, start: start, step: step, points: points, range: d.historyRange, c: c),
      Chart(
        title: t('history.net'),
        lines: [sum('net_rx:'), sum('net_tx:')],
        fmt: (v) => rate(v, c.t),
        legend: [t('history.rx'), t('history.tx')],
        start: start,
        step: step,
        points: points,
        range: d.historyRange,
        c: c,
      ),
      if (temp_ != null && temp_.any((v) => v != null))
        Chart(title: t('history.temp'), lines: [temp_], fmt: temp, start: start, step: step, points: points, range: d.historyRange, c: c),
    ];
    final cols = math.max(1, ((width + 20) / (352 + 20)).floor());
    final cw = (width - 20 * (cols - 1)) / cols;
    return [head, Wrap(spacing: 20, runSpacing: 20, children: [for (final ch in charts) SizedBox(width: cw, child: ch)])];
  }
}

// --- widgets ------------------------------------------------------------------------------

/// Pill button of the dashboard (.chip): dashed outline when off, accent tint when on.
class DahliaChip extends StatelessWidget {
  const DahliaChip({super.key, required this.text, required this.on, required this.onTap, this.height = 40, this.leading, this.trailing, this.fontSize = 14});

  final String text;
  final bool on;
  final VoidCallback? onTap;
  final double height;
  final Widget? leading, trailing;
  final double fontSize;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final radius = BorderRadius.circular(height / 2);
    final body = Container(
      height: height,
      padding: EdgeInsets.symmetric(horizontal: height >= 44 ? 14 : 13),
      decoration: on
          ? BoxDecoration(
              color: p.accentSoft,
              borderRadius: radius,
              border: Border.all(color: p.accent.withValues(alpha: .55)),
            )
          : null,
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (leading != null) ...[leading!, const SizedBox(width: 7)],
        Text(text, style: TextStyle(fontSize: fontSize, color: on ? p.fg : p.muted)),
        if (trailing != null) ...[const SizedBox(width: 4), trailing!],
      ]),
    );
    return Material(
      color: Colors.transparent,
      borderRadius: radius,
      child: InkWell(
        borderRadius: radius,
        onTap: onTap,
        child: on ? body : CustomPaint(painter: DashedBorder(p.line, height / 2), child: body),
      ),
    );
  }
}

/// Dashed rounded outline (border: 1px dashed).
class DashedBorder extends CustomPainter {
  DashedBorder(this.color, this.radius, {this.width = 1, this.dash = 4});

  final Color color;
  final double radius, width, dash;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = RRect.fromRectAndRadius((Offset.zero & size).deflate(width / 2), Radius.circular(radius));
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = width;
    for (final m in (Path()..addRRect(rect)).computeMetrics()) {
      for (var d = 0.0; d < m.length; d += dash * 2) {
        canvas.drawPath(m.extractPath(d, math.min(d + dash, m.length)), paint);
      }
    }
  }

  @override
  bool shouldRepaint(DashedBorder old) => old.color != color || old.radius != radius;
}

/// One app on the start page (.app-tile).
class AppTile extends StatelessWidget {
  const AppTile(this.c, this.app, {super.key});

  final Controller c;
  final Map app;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final id = '${app['id'] ?? ''}';
    final name = '${app['name'] ?? id}';
    final status = '${app['status'] ?? ''}';
    final link = appUrl(c, app);
    final u = app['update'];
    final update = u != null && u != false;
    final state = update ? c.t('apps.update_short') : tv(c, 'app_status', status);
    final color = update ? p.warn : (const ['running'].contains(status) ? p.ok : (const ['busy', 'partial', 'restarting'].contains(status) ? p.warn : p.muted));
    // A running app opens its web interface; anything else goes to Apps.
    final open = link != null && status == 'running' ? () => launchUrl(link) : () => c.go(Area.apps);
    return Material(
      color: p.sub,
      borderRadius: BorderRadius.circular(18),
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        hoverColor: p.navActive.withValues(alpha: .5),
        onTap: open,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 8),
          child: Column(children: [
            AppAvatar(c, app, size: 52),
            const SizedBox(height: 10),
            Text(name, textAlign: TextAlign.center, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
            const SizedBox(height: 10),
            Text(state, style: TextStyle(color: color, fontSize: 12)),
          ]),
        ),
      ),
    );
  }
}

/// Meter of the system tile: pink gradient, amber from 75 %, red from 90 %.
class GradientMeter extends StatelessWidget {
  const GradientMeter(this.pct, {super.key});

  final int pct;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return ClipRRect(
      borderRadius: BorderRadius.circular(4),
      child: Container(
        height: 8,
        color: p.track,
        alignment: Alignment.centerLeft,
        child: FractionallySizedBox(
          widthFactor: pct.clamp(0, 100) / 100,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 600),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(4),
              color: pct >= 90 ? p.danger : (pct >= 75 ? const Color(0xFFF0A63A) : null),
              gradient: pct >= 75 ? null : const LinearGradient(colors: [Color(0xFFB8448F), Color(0xFFF29BD4)]),
            ),
          ),
        ),
      ),
    );
  }
}

/// Collapsible facts, like the dashboard's details element, with a small triangle.
class Details extends StatefulWidget {
  const Details({super.key, required this.label, required this.child});

  final String label;
  final Widget child;

  @override
  State<Details> createState() => _DetailsState();
}

class _DetailsState extends State<Details> {
  bool open = false;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      InkWell(
        onTap: () => setState(() => open = !open),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(open ? Icons.arrow_drop_down_rounded : Icons.arrow_right_rounded, size: 20, color: p.muted),
          Text(widget.label, style: TextStyle(fontSize: 13, color: p.muted)),
        ]),
      ),
      if (open) Padding(padding: const EdgeInsets.only(top: 10), child: widget.child),
    ]);
  }
}

/// Round gauge of the processor and memory tiles (.gauge).
class Gauge extends StatelessWidget {
  const Gauge(this.pct, this.sub, {super.key});

  final int pct;
  final String sub;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final color = levelColor(p, pct);
    return SizedBox.square(
      dimension: 104,
      child: CustomPaint(
        painter: _GaugePainter(pct / 100, p.track, color),
        child: Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text('$pct%', style: const TextStyle(fontSize: 21.6, fontWeight: FontWeight.w600)),
            Text(sub, style: TextStyle(color: p.muted, fontSize: 11.5)),
          ]),
        ),
      ),
    );
  }
}

class _GaugePainter extends CustomPainter {
  _GaugePainter(this.value, this.track, this.color);

  final double value;
  final Color track, color;

  @override
  void paint(Canvas canvas, Size size) {
    final s = size.width / 100;
    final rect = Rect.fromCircle(center: size.center(Offset.zero), radius: 42 * s);
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 9 * s
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(rect, 0, 2 * math.pi, false, paint..color = track);
    if (value > 0) canvas.drawArc(rect, -math.pi / 2, 2 * math.pi * value.clamp(0, 1), false, paint..color = color);
  }

  @override
  bool shouldRepaint(_GaugePainter old) => old.value != value || old.color != color || old.track != track;
}

/// Small area chart of the last samples (.spark).
class SparkPainter extends CustomPainter {
  SparkPainter(this.values, this.color);

  final List<double> values;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final n = values.length - 1;
    final pts = [for (var i = 0; i < values.length; i++) Offset(i * size.width / n, size.height - values[i] / 100 * (size.height - 2) - 1)];
    final line = Path()..addPolygon(pts, false);
    final area = Path()
      ..moveTo(0, size.height)
      ..addPolygon(pts, false)
      ..lineTo(size.width, size.height)
      ..close();
    canvas
      ..drawPath(area, Paint()..color = color.withValues(alpha: .18))
      ..drawPath(
        line,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.5,
      );
  }

  @override
  bool shouldRepaint(SparkPainter old) => true;
}

/// History chart (.chart): title, average and peak, plot with time axis.
class Chart extends StatelessWidget {
  const Chart({
    super.key,
    required this.title,
    required this.lines,
    required this.fmt,
    required this.start,
    required this.step,
    required this.points,
    required this.range,
    required this.c,
    this.max,
    this.legend,
  });

  final String title;
  final List<List<double?>?> lines;
  final String Function(double) fmt;
  final int start, step, points;
  final String range;
  final Controller c;
  final double? max;
  final List<String>? legend;

  String axis(int sec) {
    final d = DateTime.fromMillisecondsSinceEpoch(sec * 1000);
    final lang = c.settings.lang;
    return switch (range) {
      'hour' => DateFormat.Hm(lang).format(d),
      'day' => '${DateFormat.E(lang).format(d)}${lang == 'de' ? '.' : ''}, ${DateFormat.Hm(lang).format(d)}',
      'year' => DateFormat.yMMM(lang).format(d),
      _ => DateFormat('dd.MM.', lang).format(d),
    };
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final values = [for (final l in lines) ...?l].whereType<double>();
    final peak = values.isEmpty ? 0.0 : values.reduce(math.max);
    final top = max ?? (peak > 0 ? peak * 1.1 : 1.0);
    final first = (lines.firstOrNull ?? const []).whereType<double>().toList();
    final small = TextStyle(color: p.muted, fontSize: 12.5);
    Widget side;
    if (legend != null) {
      side = Row(mainAxisSize: MainAxisSize.min, children: [
        for (var i = 0; i < legend!.length; i++) ...[
          const SizedBox(width: 10),
          Container(width: 11, height: 3, color: i == 0 ? p.accent : p.ok),
          const SizedBox(width: 4),
          Text(legend![i], style: small),
        ],
      ]);
    } else if (first.isNotEmpty) {
      final avg = first.reduce((a, b) => a + b) / first.length;
      side = Text(c.t('history.stats', {'avg': fmt(avg), 'max': fmt(first.reduce(math.max))}), style: small);
    } else {
      side = const SizedBox();
    }
    final axisStyle = TextStyle(color: p.muted, fontSize: 11.2);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
        Expanded(child: Text(title, style: const TextStyle(fontSize: 14.4, fontWeight: FontWeight.w600))),
        side,
      ]),
      const SizedBox(height: 6),
      Container(
        height: 128,
        decoration: BoxDecoration(border: Border(left: BorderSide(color: p.track), bottom: BorderSide(color: p.track))),
        child: Stack(children: [
          Positioned.fill(child: CustomPaint(painter: _ChartPainter(lines, top, p.accent, p.ok, p.track))),
          Positioned(top: 0, left: 5, child: Text(fmt(top), style: axisStyle)),
        ]),
      ),
      const SizedBox(height: 3),
      Row(children: [
        Text(points > 0 ? axis(start) : '', style: axisStyle),
        const Spacer(),
        Text(points > 0 ? axis(start + (points - 1) * step) : '', style: axisStyle),
      ]),
    ]);
  }
}

class _ChartPainter extends CustomPainter {
  _ChartPainter(this.lines, this.max, this.accent, this.second, this.track);

  final List<List<double?>?> lines;
  final double max;
  final Color accent, second, track;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width, h = size.height;
    final grid = Paint()
      ..color = track
      ..strokeWidth = 1;
    for (final y in [h / 4, h / 2, 3 * h / 4]) {
      for (var x = 0.0; x < w; x += 6) {
        canvas.drawLine(Offset(x, y), Offset(math.min(x + 3, w), y), grid);
      }
    }
    for (var li = 0; li < lines.length; li++) {
      final series = lines[li];
      if (series == null) continue;
      final n = math.max(series.length - 1, 1);
      var run = <Offset>[];
      void flush() {
        if (run.length > 1) {
          if (li == 0 && lines.length == 1) {
            final area = Path()
              ..moveTo(run.first.dx, h)
              ..addPolygon(run, false)
              ..lineTo(run.last.dx, h)
              ..close();
            canvas.drawPath(area, Paint()..color = accent.withValues(alpha: .14));
          }
          canvas.drawPath(
            Path()..addPolygon(run, false),
            Paint()
              ..color = li == 0 ? accent : second
              ..style = PaintingStyle.stroke
              ..strokeWidth = 1.6
              ..strokeJoin = StrokeJoin.round,
          );
        }
        run = [];
      }

      for (var i = 0; i < series.length; i++) {
        final v = series[i];
        if (v == null) {
          flush();
          continue;
        }
        run.add(Offset(i * w / n, h - (math.min(v, max) / max) * (h - 4) - 2));
      }
      flush();
    }
  }

  @override
  bool shouldRepaint(_ChartPainter old) => true;
}
