// Parity area without widgets: state, run texts, notices and what may be
// started, the same rules as the dashboard's parity page.

import 'format.dart';

/// Jobs that keep the array busy; one of them running blocks sync, scrub and restore.
const parityJobs = ['sync', 'scrub', 'repair', 'mover'];

const paritySettings = ['snapraid_delete_threshold', 'scrub_percent', 'scrub_older_than_days'];

/// Bounds of the settings, as in the dashboard's form.
const settingLimits = {
  'snapraid_delete_threshold': (0, 100000),
  'scrub_percent': (1, 100),
  'scrub_older_than_days': (0, 3650),
};

List<Map> maps(Object? v) => v is List ? v.whereType<Map>().toList() : <Map>[];

/// A oneshot unit runs while "activating"; the repair unit stays "active (exited)".
bool unitRunning(Object? s) =>
    s is Map && (const ['activating', 'deactivating'].contains(s['active']) || (s['active'] == 'active' && s['sub'] != 'exited'));

/// The first busy job of [d], '' when idle.
String runningJob(Map d) {
  final jobs = d['jobs'] is Map ? d['jobs'] as Map : const {};
  return parityJobs.firstWhere((k) => unitRunning(jobs[k]), orElse: () => '');
}

Map _runs(Map d) => d['runs'] is Map ? d['runs'] as Map : const {};

/// Head state: locale key below parity. and its level ('ok', 'warn', 'crit', '').
(String, String) parityState(Map d) {
  final s = d['status'];
  if (d['configured'] != true) return ('state_none', '');
  if (s is Map && ((s['bad'] as num?) ?? 0) > 0) return ('state_bad', 'crit');
  if (s is! Map) return ('state_unsynced', 'warn');
  final sync = _runs(d)['sync'];
  if (sync is Map && const ['blocked', 'failed'].contains(sync['result'])) return ('state_stale', 'warn');
  return ('state_ok', 'ok');
}

String countsText(Object? counts, Translate t) {
  final c = counts is Map ? counts : const {};
  return t('parity.counts', {'added': c['added'] ?? 0, 'removed': c['removed'] ?? 0, 'updated': c['updated'] ?? 0});
}

/// "/mnt/disk2" for "d2", the bad-blocks text for "bad".
String fixTarget(Object? what, Translate t) {
  if (what == 'bad') return t('parity.what_bad');
  final w = '${what ?? ''}';
  return '/mnt/disk${w.isEmpty ? '' : w.substring(1)}';
}

/// Last run of a kind: `<end>: <result> (<counts or message>)`.
String runText(Object? run, Translate t, String Function(num?) time) {
  if (run is! Map) return t('parity.never');
  final result = run['result'];
  final key = 'parity_result.$result';
  var text = '${time(run['end'] as num?)}: ${t(key) == key ? '$result' : t(key)}';
  if (result == 'ok' && run['counts'] != null) text += ' (${countsText(run['counts'], t)})';
  if (result == 'failed' && '${run['message'] ?? ''}'.isNotEmpty) text += ' (${run['message']})';
  return text;
}

String runDuration(num sec) {
  final s = sec.round() < 0 ? 0 : sec.round();
  final h = s ~/ 3600;
  final m = (s % 3600) ~/ 60;
  return h > 0 ? '$h h $m min' : (m > 0 ? '$m min' : '$s s');
}

/// Share of blocks checked by scrubs, null before the first sync.
int? checkedPercent(Object? status) {
  if (status is! Map) return null;
  final blocks = (status['blocks'] as num?) ?? 0;
  if (blocks <= 0) return null;
  return (100 * (blocks - ((status['unscrubbed'] as num?) ?? 0)) / blocks).floor();
}

bool _allMounted(Map d) => [...maps(d['parity']), ...maps(d['data'])].every((x) => x['mounted'] == true);

/// Sync, scrub and restore need parity set up, every disk mounted and no job running.
bool idle(Map d) => d['configured'] == true && runningJob(d).isEmpty && _allMounted(d);

bool canSync(Map d) => idle(d);

bool canScrub(Map d) => idle(d) && d['status'] is Map;

bool canRestore(Map d) => canScrub(d) && maps(d['data']).isNotEmpty;

/// One notice above the cards: locale key, its values, and the button it offers
/// ('disks', 'repair', 'force' or '').
typedef Notice = (String key, Map<String, Object?> vars, String action);

List<Notice> notices(Map d) {
  final out = <Notice>[];
  final running = runningJob(d);
  final s = d['status'] is Map ? d['status'] as Map : null;
  final parity = maps(d['parity']);
  final data = maps(d['data']);
  final problems = d['problems'] is List ? [for (final p in d['problems'] as List) '$p'] : const <String>[];
  final missing = [for (final x in [...parity, ...data]) if (x['mounted'] != true) '${x['mountpoint']}'];
  final runs = _runs(d);
  if (running.isNotEmpty) out.add(('parity.running_$running', const {}, ''));
  if (parity.isEmpty) {
    out.add(('parity.no_parity', const {}, 'disks'));
  } else if (data.isEmpty) {
    out.add(('parity.no_data', const {}, 'disks'));
  } else if (problems.isNotEmpty) {
    out.add(('parity.problems', {'list': problems.join('; ')}, ''));
  }
  if (missing.isNotEmpty) out.add(('parity.unmounted', {'list': missing.join(', ')}, ''));
  if ('${d['error'] ?? ''}'.isNotEmpty) out.add(('parity.error', {'text': d['error']}, ''));
  final ready = running.isEmpty && missing.isEmpty;
  final bad = (s?['bad'] as num?) ?? 0;
  if (bad > 0) out.add(('parity.bad', {'n': bad}, ready ? 'repair' : ''));
  final sync = runs['sync'];
  if (sync is Map && sync['result'] == 'blocked' && ready) {
    final counts = sync['counts'] is Map ? sync['counts'] as Map : const {};
    out.add(('parity.blocked', {'n': counts['removed'] ?? 0, 'threshold': sync['threshold']}, 'force'));
  }
  if (s != null && ((s['unsynced'] as num?) ?? 0) > 0 && running.isEmpty) out.add(('parity.interrupted', const {}, ''));
  final fix = runs['fix'];
  if (fix is Map && fix['result'] == 'failed' && running.isEmpty) {
    out.add(('parity.fix_failed', {'text': fix['message'] ?? ''}, ''));
  }
  return out;
}

/// Rows of the disk table: name key and values, the disk, files ('-' unknown).
List<(String, Map<String, Object?>, Map, String)> diskRows(Map d) {
  final s = d['status'] is Map ? d['status'] as Map : null;
  final perDisk = s?['disks'] is Map ? s!['disks'] as Map : const {};
  return [
    for (final x in maps(d['parity'])) ('parity.parity_n', {'n': x['index']}, x, '-'),
    for (final x in maps(d['data']))
      ('parity.data_n', {'n': x['index']}, x, perDisk['d${x['index']}'] is Map ? '${(perDisk['d${x['index']}'] as Map)['files']}' : '-'),
  ];
}

/// Disks a restore can target: "disk1", "disk2", ...
List<String> restoreTargets(Map d) => [for (final x in maps(d['data'])) 'disk${x['index']}'];

/// History, newest first, with the kind text (a fix names its target).
String historyKind(Map run, Translate t) {
  var kind = t('parity.kind_${run['kind']}');
  if (run['kind'] == 'fix' && run['what'] != null) kind += ' (${fixTarget(run['what'], t)})';
  return kind;
}

String historyDetail(Map run, Translate t) {
  final m = '${run['message'] ?? ''}';
  if (m.isNotEmpty) return m;
  return run['counts'] != null ? countsText(run['counts'], t) : '-';
}

/// Level of a run result for its label.
String resultLevel(Object? result) => switch (result) {
      'ok' || 'no_changes' => 'ok',
      'blocked' => 'warn',
      'failed' => 'crit',
      _ => '',
    };

/// Checks the settings form; the body for POST /api/parity/settings, or null.
Map<String, int>? settingsBody(Map<String, String> fields) {
  final body = <String, int>{};
  for (final k in paritySettings) {
    final v = int.tryParse(fields[k]?.trim() ?? '');
    final (lo, hi) = settingLimits[k]!;
    if (v == null || v < lo || v > hi) return null;
    body[k] = v;
  }
  return body;
}
