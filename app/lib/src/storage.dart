// Disks area without widgets: grouping, labels, what each disk offers and the
// storage settings, the same rules as the dashboard's storage page.

import 'format.dart';

/// The pools shown at the top: mount point, name key, hint key.
const pools = [
  ('/mnt/user', 'fs.user', 'storage.pool_user'),
  ('/mnt/storage', 'fs.storage', 'storage.pool_storage'),
  ('/mnt/cache', 'fs.cache', 'storage.pool_cache'),
];

/// Roles a usable disk can be added in.
const roles = ['data', 'parity', 'cache', 'external'];

/// Filesystems offered when formatting; XFS first (recommended).
const filesystems = ['xfs', 'ext4', 'btrfs'];

/// Mover minimum ages offered, in minutes.
const moverAges = [0, 60, 360, 720, 1440, 4320, 10080, 43200];

const importRunning = ['mount', 'copy', 'verify'];

int percent(num used, num size) => size > 0 ? ((used / size) * 100).round().clamp(0, 100) : 0;

/// One entry per physical disk, its filesystems in the order of the list.
List<(String, List<Map>)> groupDisks(List<Map> disks) {
  final groups = <String, List<Map>>{};
  for (final d in disks) {
    final key = '${d['disk'] ?? d['path']}';
    (groups[key] ??= []).add(d);
  }
  return [for (final e in groups.entries) (e.key, e.value)];
}

/// Status shown on a disk's head: system, managed or usable, else none.
String? diskStatus(List<Map> parts) {
  final kinds = {for (final p in parts) p['status']};
  for (final k in ['system', 'registered', 'candidate']) {
    if (kinds.contains(k)) return k;
  }
  return null;
}

String shortName(String disk) => disk.replaceFirst(RegExp(r'^/dev/'), '');

bool canAdd(Map d) => d['status'] == 'candidate' || d['status'] == 'external-only';

/// Roles allowed for [d]; a disk that only works as external has just that one.
List<String> rolesFor(Map d) => d['status'] == 'external-only' ? const ['external'] : roles;

bool canRemove(Map d) => d['status'] == 'registered';

bool canImport(Map d) => canAdd(d) && d['imported'] != true;

/// A whole blank disk, or a filesystem whose data was imported (formats its whole disk).
bool canFormat(Map d) => (d['status'] == 'empty' && d['path'] == d['disk']) || d['imported'] == true;

/// A new, blank disk that a burn-in test can be offered for.
bool offerBurnin(String disk, List<Map> parts) =>
    !disk.startsWith('/dev/loop') &&
    parts.first['removable'] != true &&
    parts.every((p) => p['kind'] == 'disk' && p['status'] == 'empty');

/// The mounted filesystem of [d]; an encrypted one is mounted from its
/// unlocked container.
Map? mountOf(Map d, List<Map> mounts) {
  final sources = {d['path'], if (d['luks'] != null) '/dev/mapper/luks-${d['luks']}'};
  for (final m in mounts) {
    if (sources.contains(m['source'])) return m;
  }
  return null;
}

/// Name of a mount point following the storage layout.
String fsLabel(String mp, Translate t) {
  if (mp == '/') return t('fs.system');
  for (final (p, key, _) in pools) {
    if (mp == p) return t(key);
  }
  var m = RegExp(r'^/mnt/disk(\d+)$').firstMatch(mp);
  if (m != null) return t('fs.disk', {'n': m[1]});
  m = RegExp(r'^/mnt/parity(\d+)$').firstMatch(mp);
  if (m != null) return t('fs.parity', {'n': m[1]});
  m = RegExp(r'^/mnt/external/(.+)$').firstMatch(mp);
  if (m != null) return t('fs.external', {'name': m[1]});
  return mp;
}

/// 'ok', 'warn' or 'crit' for a disk temperature: HDDs are warm at 45 °C,
/// SSDs at 60 °C, critical 10 degrees above.
String tempLevel(num c, bool rotational) {
  final warn = rotational ? 45 : 60;
  if (c >= warn + 10) return 'crit';
  if (c >= warn) return 'warn';
  return 'ok';
}

/// Default share name for importing [d]: import-<label or device>.
String importTarget(Map d) {
  final base = '${d['label'] ?? shortName('${d['path']}')}'.toLowerCase().replaceAll(RegExp(r'[^a-z0-9._-]+'), '-');
  final name = 'import-$base'.replaceFirst(RegExp(r'-+$'), '');
  return name.length > 64 ? name.substring(0, 64) : name;
}

final shareNameRe = RegExp(r'^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$');

/// minfreespace ("50G", "200M", "1T", or bytes/KB) as value and unit M, G or T.
(int, String) parseFree(String text) {
  final m = RegExp(r'^(\d+)([KMGT]?)$').firstMatch(text);
  if (m == null) return (50, 'G');
  final value = int.parse(m[1]!);
  final unit = m[2]!.isEmpty ? 'B' : m[2]!;
  if (unit == 'M' || unit == 'G' || unit == 'T') return (value, unit);
  final bytes = value * (unit == 'K' ? 1024 : 1);
  final mb = (bytes / (1024 * 1024)).round();
  return (mb < 1 ? 1 : mb, 'M');
}

/// The ages to offer, including a value set elsewhere.
List<int> moverAgesWith(int current) =>
    moverAges.contains(current) ? moverAges : ([...moverAges, current]..sort());

String moverAgeLabel(int m, Translate t) {
  if (m == 0) return t('storage.mover_age_none');
  if (m % 1440 == 0) return t('storage.mover_age_days', {'n': m ~/ 1440});
  if (m % 60 == 0) return t('storage.mover_age_hours', {'n': m ~/ 60});
  return t('storage.mover_age_minutes', {'n': m});
}

/// Whether removing failed only because a cache still holds files, which
/// needs a second yes (the agent's message names --force).
bool needsForce(int status, String detail) => status == 422 && detail.contains('--force');
