// VMs and containers (Incus): the state of /api/vms and the request for a new
// instance, as in the dashboard's VMs page.

class VmInstance {
  VmInstance(Map m)
      : name = '${m['name'] ?? ''}',
        vm = m['type'] == 'vm',
        status = '${m['status'] ?? ''}',
        cpus = m['cpus'] is int ? m['cpus'] as int : null,
        memory = m['memory'] is int ? m['memory'] as int : null,
        autostart = m['autostart'] == true,
        image = '${m['image'] ?? ''}',
        addresses = m['addresses'] is List ? (m['addresses'] as List).whereType<String>().toList() : const [],
        lan = m['network'] == 'lan',
        console = m['console'] == true,
        iso = m['iso'] == true;

  final String name;
  final bool vm;
  final String status;
  final int? cpus;

  /// MiB.
  final int? memory;
  final bool autostart;
  final String image;
  final List<String> addresses;
  final bool lan;
  final bool console;
  final bool iso;

  bool get running => status == 'running';

  /// Shell of a container, for the terminal.
  String get shell => 'incus exec $name -- bash -l';
}

class VmCreating {
  VmCreating(Map m)
      : name = '${m['name'] ?? ''}',
        failed = m['job'] is Map &&
            !const ['active', 'activating'].contains((m['job'] as Map)['active']) &&
            '${(m['job'] as Map)['result'] ?? ''}'.isNotEmpty &&
            (m['job'] as Map)['result'] != 'success';

  final String name;
  final bool failed;
}

class VmState {
  VmState(Map m)
      : installed = m['installed'] == true,
        installing = m['install_job'] is Map && const ['active', 'activating'].contains((m['install_job'] as Map)['active']),
        installFailed = m['install_job'] is Map &&
            '${(m['install_job'] as Map)['started'] ?? ''}'.isNotEmpty &&
            !const ['', 'success'].contains('${(m['install_job'] as Map)['result'] ?? ''}'),
        cpus = (m['host'] is Map ? (m['host'] as Map)['cpus'] : null) as int? ?? 1,
        memory = (m['host'] is Map ? (m['host'] as Map)['memory'] : null) as int? ?? 0,
        images = m['images'] is List ? (m['images'] as List).whereType<String>().toList() : const [],
        cache = m['cache'] == true,
        lan = m['lan'] == true,
        error = m['error'] is String ? m['error'] as String : null,
        instances = m['instances'] is List ? (m['instances'] as List).whereType<Map>().map(VmInstance.new).toList() : [],
        creating = m['creating'] is List ? (m['creating'] as List).whereType<Map>().map(VmCreating.new).toList() : [];

  final bool installed;
  final bool installing;
  final bool installFailed;

  /// Processors and memory (MiB) of the server.
  final int cpus;
  final int memory;
  final List<String> images;
  final bool cache;
  final bool lan;
  final String? error;
  final List<VmInstance> instances;
  final List<VmCreating> creating;

  /// Something runs in the background: the page polls.
  bool get busy => installing || creating.any((c) => !c.failed);
}

/// MiB as GB for display and input: "4", "1.5".
String gbText(int mib) {
  final gb = mib / 1024;
  return gb == gb.roundToDouble() ? '${gb.round()}' : gb.toStringAsFixed(2).replaceFirst(RegExp(r'0$'), '');
}

final vmNameRe = RegExp(r'^[a-z]([a-z0-9-]{0,30}[a-z0-9])?$');

/// Body of POST /api/vms. [memoryGb] and [diskGb] as typed; null when invalid.
Map<String, Object?>? vmRequest({
  required String name,
  required bool vm,
  required bool fromIso,
  required String image,
  String? isoRoot,
  String? isoPath,
  required int cpus,
  required double memoryGb,
  required int diskGb,
  required bool lan,
  required bool autostart,
  required bool start,
  required bool secureboot,
  required bool windows,
}) {
  if (!vmNameRe.hasMatch(name) || cpus < 1 || memoryGb < .125 || diskGb < 1) return null;
  final iso = vm && fromIso;
  if (iso && (isoRoot == null || isoPath == null)) return null;
  return {
    'name': name,
    'kind': vm ? 'vm' : 'container',
    'source': iso ? 'iso' : 'image',
    'image': image,
    if (iso) 'iso_root': isoRoot,
    if (iso) 'iso_path': isoPath,
    'cpus': cpus,
    'memory': (memoryGb * 1024).round(),
    'disk': vm ? diskGb : 10,
    'network': lan ? 'lan' : 'nat',
    'autostart': autostart,
    'start': start,
    'secureboot': secureboot,
    'windows': vm && windows,
  };
}

/// One Incus snapshot of an instance (vm.snapshots).
class VmSnapshot {
  VmSnapshot(Map m)
      : name = '${m['name'] ?? ''}',
        created = DateTime.tryParse('${m['created'] ?? ''}');

  final String name;
  final DateTime? created;
}

/// Snapshots newest first, as the list shows them.
List<VmSnapshot> parseSnapshots(Object? data) {
  final list = data is Map && data['snapshots'] is List ? (data['snapshots'] as List).whereType<Map>() : const <Map>[];
  return [for (final m in list) VmSnapshot(m)].reversed.toList();
}

/// A snapshot name the agent takes (empty: named by the time).
final snapshotNameRe = RegExp(r'^([a-z0-9][a-z0-9-]{0,40})?$');

const snapshotSchedules = ['off', 'daily', 'weekly'];
