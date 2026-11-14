// Updates area without widgets: update progress, automatic security updates
// and the system snapshots (snapper), the same rules as the dashboard's page.

import 'format.dart';

List<Map> _maps(Object? v) => v is List ? v.whereType<Map>().toList() : <Map>[];

/// Packages of an update run that are still open and those done.
(List<Map>, List<Map>) splitPackages(Map progress) {
  final all = _maps(progress['packages']);
  return ([for (final p in all) if (p['step'] != 'done') p], [for (final p in all) if (p['step'] == 'done') p]);
}

/// Level of an update run for its box ('info' while running, 'ok', 'crit').
String progressLevel(Object? state) => switch (state) {
      'done' => 'ok',
      'failed' => 'crit',
      _ => 'info',
    };

/// Restart times offered: every half hour, plus a saved time that is not on the grid.
List<String> rebootTimes(String saved) {
  String two(int n) => n.toString().padLeft(2, '0');
  final times = [for (var h = 0; h < 24; h++) for (final m in [0, 30]) '${two(h)}:${two(m)}'];
  if (saved.isNotEmpty && !times.contains(saved)) times.add(saved);
  return times;
}

final rebootTimeRe = RegExp(r'^([01][0-9]|2[0-3]):[0-5][0-9]$');

/// Body of `POST /api/updates/auto`; null for a time the agent refuses.
Map<String, Object>? autoBody({required bool enabled, required bool reboot, required String time}) =>
    rebootTimeRe.hasMatch(time) ? {'enabled': enabled, 'reboot': reboot, 'reboot_time': time} : null;

/// Text about a pending restart, null when none is needed. The agent sends
/// null for none and a list (possibly empty) of packages otherwise.
String? rebootNotice(Object? required, Translate t) {
  if (required is! List) return null;
  return required.isEmpty ? t('auto.reboot_needed') : t('auto.reboot_needed_pkgs', {'list': required.join(', ')});
}

/// Level of the last automatic run for its label.
String autoResultLevel(Object? result) => switch (result) {
      'installed' => 'ok',
      'failed' => 'crit',
      'running' => 'info',
      _ => '',
    };

/// snapper writes local times as "2026-11-14 03:00:00", the rollback note
/// ISO text with an offset; both come back in local time.
DateTime? localTime(Object? v) => v is String && v.isNotEmpty ? DateTime.tryParse(v.replaceFirst(' ', 'T'))?.toLocal() : null;

/// Labels behind a snapshot's description: locale key, values, level.
List<(String, Map<String, Object?>, String)> snapshotTags(Map s) => [
      if (s['type'] == 'pre') ('snap.type_pre', const {}, 'info'),
      if (s['type'] == 'post') ('snap.type_post', {'n': s['pre_number']}, ''),
      if (s['userdata'] is Map && (s['userdata'] as Map)['important'] == 'yes') ('snap.important', const {}, 'accent'),
    ];

/// The "before" snapshot of a "post" one, to point at it before a rollback.
Map? preOf(Map s, List<Map> all) {
  if (s['type'] != 'post') return null;
  for (final x in all) {
    if (x['number'] == s['pre_number']) return x;
  }
  return null;
}

/// Same characters as the agent allows (letters and digits of any script).
final snapshotDescriptionRe = RegExp(r'^[\p{L}\p{N}_ .,:()/+-]{1,80}$', unicode: true);

bool validDescription(String text) => snapshotDescriptionRe.hasMatch(text.trim());

/// Snapshots newest first.
List<Map> snapshotsNewestFirst(Object? data) => _maps(data is Map ? data['snapshots'] : null).reversed.toList();

List<String> oldRoots(Object? data) => [if (data is Map && data['old_roots'] is List) for (final n in data['old_roots'] as List) '$n'];
