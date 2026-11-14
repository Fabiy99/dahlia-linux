// Docker area without widgets: container states, ports, live figures and
// disk usage, the same rules as the dashboard's Docker page.

import 'format.dart';

/// Level of a container state for its label ('ok', 'warn', 'crit', '').
String containerLevel(Object? state) => switch (state) {
      'running' => 'ok',
      'restarting' || 'paused' || 'removing' => 'warn',
      'dead' => 'crit',
      _ => '',
    };

/// Buttons a container offers: a running one restarts or stops, any other starts.
List<String> containerOps(Object? state) => state == 'running' ? const ['restart', 'stop'] : const ['start'];

/// Ports one per line, '-' without any.
String portsText(Object? ports) {
  final p = '${ports ?? ''}'.trim();
  return p.isEmpty ? '-' : p.split(', ').join('\n');
}

/// CPU of one sample: "3.5 %", '-' while the container has none.
String cpuText(Object? stat) {
  if (stat is! Map || stat['cpu_percent'] is! num) return '-';
  return '${(stat['cpu_percent'] as num).toStringAsFixed(1)} %';
}

String memoryText(Object? stat, Translate t) {
  if (stat is! Map || stat['memory'] is! num) return '-';
  return bytes(stat['memory'] as num, t);
}

/// "2 of 5 in use, 1.2 GB reclaimable" below a disk usage tile.
String usageDetail(Map u, Translate t) => t('docker.usage_detail', {
      'active': u['active'] ?? 0,
      'total': u['total'] ?? 0,
      'free': bytes((u['reclaimable'] as num?) ?? 0, t),
    });

/// Live figures by container name.
Map<String, Map> statsOf(Object? response) {
  final s = response is Map ? response['stats'] : null;
  if (s is! Map) return const {};
  return {for (final e in s.entries) if (e.value is Map) '${e.key}': e.value as Map};
}

/// Path of a container action; the name is escaped like the dashboard does.
String containerPath(String name, String op) => '/api/docker/${Uri.encodeComponent(name)}/$op';

String logsPath(String name, {int lines = 500}) => '/api/docker/${Uri.encodeComponent(name)}/logs?lines=$lines';
