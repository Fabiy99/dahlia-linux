// Files area without widgets: query strings, what the selection allows,
// job bodies and texts, the same rules as the dashboard's file manager.

import 'apps.dart';
import 'format.dart';

/// "root=/mnt/user&path=a%20b": the query part of a file manager request.
String fmQuery(Map<String, String> params) => Uri(queryParameters: params).query;

String parentOf(String path) => path.contains('/') ? path.substring(0, path.lastIndexOf('/')) : '';

/// The selection's names in list order, so actions keep the order shown.
List<String> selectedInOrder(List<Map> entries, Set<String> selected) =>
    [for (final e in entries) if (selected.contains(e['name'])) '${e['name']}'];

/// What the action bar allows for [names] in a folder that is [writable].
({bool download, bool rename, bool copy, bool move, bool delete}) allowed(
    List<Map> entries, List<String> names, bool writable) {
  final one = names.length == 1 ? entries.where((e) => e['name'] == names.first).firstOrNull : null;
  return (
    download: one != null && one['kind'] == 'file',
    rename: one != null && writable,
    copy: names.isNotEmpty,
    move: names.isNotEmpty && writable,
    delete: names.isNotEmpty && writable,
  );
}

/// Body of POST /api/files/start.
Map<String, Object> startBody(String op, String root, String path, List<String> names, [Place? dest]) => {
      'op': op,
      'root': root,
      'paths': [for (final n in names) joinPath(path, n)],
      if (dest != null) 'dest_root': dest.root,
      if (dest != null) 'dest': dest.path,
    };

/// Locale key of the hint above the list, '' for none.
String folderHint(Map data, String path) {
  if (data['mounted'] != true) return 'fm.not_mounted';
  if (path.isEmpty) return 'fm.hint_root';
  if (data['writable'] != true) return 'fm.hint_appdata';
  if (data['truncated'] == true) return 'fm.truncated';
  return '';
}

/// Progress line of a running delete, copy or move.
String jobText(Map job, Translate t) {
  final done = (job['done'] as num?) ?? 0;
  final total = (job['total'] as num?) ?? 0;
  final amount = job['op'] == 'delete'
      ? t('fm.items', {'done': done, 'total': total})
      : '${bytes(done, t)} / ${bytes(total, t)}';
  return '${t('fm.running_${job['op']}')} $amount';
}

/// What an upload of [name] does here: 'new', 'overwrite' (ask first) or
/// 'dir' (a folder of that name is in the way).
String uploadCase(List<Map> entries, String name) {
  final hit = entries.where((e) => e['name'] == name).firstOrNull;
  if (hit == null) return 'new';
  return hit['kind'] == 'dir' ? 'dir' : 'overwrite';
}
