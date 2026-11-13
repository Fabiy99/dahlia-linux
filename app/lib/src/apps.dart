// Apps area without widgets: locations, folder choices for the install,
// catalog filter and the background jobs of install and update, the same
// rules as the dashboard's app.js.

import 'dart:async';

import 'server.dart';

/// Where app data and folders can live.
const arrayRoot = '/mnt/user';
const systemRoot = '/srv';
const remotesRoot = '/mnt/remotes';

String joinPath(String base, String name) => base.isEmpty ? name : '$base/$name';

String fullPath(String root, String path) => path.isEmpty ? root : '$root/$path';

/// Text of a {lang: text} map, English when the language is missing.
String localized(Object? m, String lang) {
  if (m is! Map) return '';
  final v = m[lang] ?? m['en'];
  return v is String ? v : '';
}

/// Images of the services beside the main container, for "With: ...".
String helpers(Map app) {
  final services = app['services'];
  if (services is! List) return '';
  return services.whereType<Map>().where((s) => s['name'] != 'app').map((s) => '${s['image']}').join(', ');
}

/// LAN ports as "8080/tcp, 53/udp".
String lanPorts(Map app) {
  final ports = app['lan_ports'];
  if (ports is! List) return '';
  return ports.whereType<Map>().map((p) => '${p['host']}/${p['protocol']}').join(', ');
}

/// Folder of a share: root plus path below it.
class Place {
  const Place(this.root, this.path);

  final String root;
  final String path;

  String get full => fullPath(root, path);

  /// The folder holding this one, so the picker shows its siblings.
  String get parent => path.contains('/') ? path.substring(0, path.lastIndexOf('/')) : '';

  @override
  bool operator ==(Object other) => other is Place && other.root == root && other.path == path;

  @override
  int get hashCode => Object.hash(root, path);
}

/// The default location: the array once it is mounted, else the system SSD.
String defaultLocation(bool poolMounted) => poolMounted ? arrayRoot : systemRoot;

/// Folder of [share] when the user did not change it.
Place defaultPlace(String location, String share) => Place(location, share);

/// Body of the install request: unchanged shares follow the location and are
/// not sent, changed ones are sent as full paths.
Map<String, Object> installBody(String location, Map<String, Place> chosen) {
  final folders = <String, String>{
    for (final e in chosen.entries)
      if (e.value != defaultPlace(location, e.key)) e.key: e.value.full,
  };
  return {'location': location, 'folders': folders};
}

/// Catalog entries matching the search text and category ('' = all).
List<Map> filterCatalog(List<Map> catalog, String query, String category, String lang) {
  final q = query.trim().toLowerCase();
  return catalog.where((app) {
    if (category.isNotEmpty && app['category'] != category) return false;
    if (q.isEmpty) return true;
    return '${app['id']} ${app['name']} ${localized(app['description'], lang)}'.toLowerCase().contains(q);
  }).toList();
}

/// Categories used in the catalog with their number of apps.
Map<String, int> categories(List<Map> catalog) {
  final out = <String, int>{};
  for (final app in catalog) {
    final c = '${app['category'] ?? 'other'}';
    out[c] = (out[c] ?? 0) + 1;
  }
  return out;
}

/// Subfolders shown in the picker; app data folders at the top belong to
/// their apps and are not offered.
List<String> pickableDirs(List dirs, String path) =>
    dirs.whereType<String>().where((d) => path.isNotEmpty || d != 'appdata').toList();

bool jobRunning(Object? job) => job is Map && (job['active'] == 'active' || job['active'] == 'activating');

/// Polls the job of app [id] until it is no longer running and returns its
/// last state; null after [limit] polls. [tick] runs after each poll, so the
/// list can show the "busy" status in between.
Future<Map?> waitForJob(
  Server s,
  String id, {
  Duration every = const Duration(seconds: 3),
  int limit = 600,
  FutureOr<void> Function()? tick,
}) async {
  for (var i = 0; i < limit; i++) {
    await Future<void>.delayed(every);
    final job = await s.get('/api/apps/$id/job');
    await tick?.call();
    if (!jobRunning(job)) return job is Map ? job : null;
  }
  return null;
}

/// Locale key and values of the message after a job.
(String, Map<String, Object?>) jobResult(Map? job, String op, String id, String name) {
  if (job != null && job['result'] == 'success') return ('apps.${op}_done', {'name': name});
  final error = job?['error'];
  if (error is String && error.isNotEmpty) return ('apps.job_failed_reason', {'name': name, 'error': error});
  return ('apps.job_failed', {'name': name, 'unit': 'dahlia-app-$id'});
}
