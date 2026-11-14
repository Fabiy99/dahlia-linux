// Backup area without widgets: target texts and bodies, the disks offered
// as target and what a restore can bring back, the same rules as the
// dashboard's backup page.

import 'format.dart';

/// A unit that is running or starting.
bool unitActive(Object? unit) => unit is Map && const ['active', 'activating'].contains(unit['active']);

/// Where the backup goes, as one line; '' without a target.
String targetText(Object? target) {
  if (target is! Map) return '';
  if (target['type'] == 'sftp') return 'sftp://${target['user']}@${target['host']}:${target['port']}/${target['path']}';
  return '${target['path'] ?? ''}';
}

/// Disks offered as folder target; a saved target that is not mounted stays
/// in the list, marked missing, so saving again does not lose it.
List<Map> folderChoices(Map data) {
  final list = data['candidates'] is List ? (data['candidates'] as List).whereType<Map>().toList() : <Map>[];
  final target = data['target'];
  if (target is Map && target['type'] == 'folder' && '${target['path'] ?? ''}'.isNotEmpty && !list.any((c) => c['path'] == target['path'])) {
    list.insert(0, {'path': target['path'], 'mount': target['path'], 'avail': null, 'missing': true});
  }
  return list;
}

String choiceLabel(Map c, Translate t) => c['missing'] == true
    ? '${c['path']} (${t('backup.not_mounted')})'
    : '${c['path']} (${t('backup.free', {'size': bytes((c['avail'] as num?) ?? 0, t)})})';

/// Body of `POST /api/backup`; null while the target is incomplete. The saved
/// extra [folders] go back unchanged, the agent would otherwise clear them.
Map<String, Object>? backupBody({
  required String type,
  required String folder,
  required String host,
  required String port,
  required String user,
  required String path,
  required bool stopApps,
  List<Object?> folders = const [],
}) {
  final Map<String, Object> target = type == 'sftp'
      ? {'type': 'sftp', 'host': host.trim(), 'port': int.tryParse(port.trim()) ?? 22, 'user': user.trim(), 'path': path.trim()}
      : {'type': 'folder', 'path': folder};
  if ('${target['path']}'.isEmpty) return null;
  if (type == 'sftp' && ('${target['host']}'.isEmpty || '${target['user']}'.isEmpty)) return null;
  return {'target': target, 'stop_apps': stopApps, 'folders': folders};
}

/// What a snapshot can restore: the configuration, then each installed app.
List<(String, String)> restoreChoices(List<Map> installed, Translate t) => [
      ('config', t('backup.what_config')),
      for (final a in installed) ('${a['id']}', '${a['name'] ?? a['id']}'),
    ];

/// Times come as ISO text or as milliseconds.
DateTime? parseTime(Object? v) {
  if (v is num) return DateTime.fromMillisecondsSinceEpoch(v.toInt());
  if (v is String) return DateTime.tryParse(v)?.toLocal();
  return null;
}
