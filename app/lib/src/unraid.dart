// Unraid takeover without widgets: the lines of the plan and of the running
// import, the same words as the dashboard's storage page (renderUnraid).

import 'format.dart';

const unraidSections = ['disks', 'shares', 'users', 'wireguard', 'containers'];

/// Mark of a plan entry: ok (taken over), info (needs an answer), bad (not taken over).
String unraidMark(String state) => switch (state) {
      'ready' || 'done' => 'ok',
      'skip' => 'bad',
      _ => 'info',
    };

/// Entries of one section; WireGuard is a single entry or none.
List<Map> unraidItems(Map plan, String section) {
  final v = plan[section];
  if (section == 'wireguard') return v is Map ? [v] : const [];
  return v is List ? v.whereType<Map>().toList() : const [];
}

String unraidLine(String section, Map item, Translate t) {
  final name = switch (section) {
    'disks' => t('unraid.disk', {'slot': item['name'], 'role': t('unraid.role_${item['role']}')}),
    'wireguard' => t('unraid.wireguard', {'n': (item['peers'] as List?)?.length ?? 0}),
    _ => '${item['name']}',
  };
  final parts = [t('unraid.state_${item['state']}')];
  if ('${item['reason'] ?? ''}'.isNotEmpty) parts.add(t('unraid.reason_${item['reason']}'));
  for (final n in (item['notes'] as List? ?? const []).whereType<Map>()) {
    parts.add(t('unraid.note_${n['key']}', {'value': n['value']}));
  }
  if (item['image'] != null) parts.add('${item['image']}');
  return '$name: ${parts.join(' · ')}';
}

final tagPattern = RegExp(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$');

/// A version the import accepts instead of "latest".
bool validTag(String tag) => tagPattern.hasMatch(tag) && tag != 'latest';

/// Shown while found, unless hidden and no import ran.
bool showUnraid(Map? data) =>
    data != null && data['found'] == true && (data['dismissed'] != true || data['job'] != null);
