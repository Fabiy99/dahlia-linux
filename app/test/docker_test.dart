import 'package:dahlia/src/docker.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  test('container states map to label levels', () {
    expect(containerLevel('running'), 'ok');
    expect(containerLevel('restarting'), 'warn');
    expect(containerLevel('paused'), 'warn');
    expect(containerLevel('dead'), 'crit');
    expect(containerLevel('exited'), '');
    expect(containerLevel(null), '');
  });

  test('a running container restarts or stops, others start', () {
    expect(containerOps('running'), ['restart', 'stop']);
    expect(containerOps('exited'), ['start']);
    expect(containerOps('created'), ['start']);
  });

  test('ports one per line', () {
    expect(portsText('127.0.0.1:8096->8096/tcp, 127.0.0.1:8920->8920/tcp'), '127.0.0.1:8096->8096/tcp\n127.0.0.1:8920->8920/tcp');
    expect(portsText(''), '-');
    expect(portsText(null), '-');
  });

  test('live figures, dash without a sample', () {
    final stats = statsOf({
      'stats': {
        'jellyfin': {'cpu_percent': 3.456, 'memory': 1536},
        'broken': 'x',
      },
    });
    expect(stats.keys, ['jellyfin']);
    expect(cpuText(stats['jellyfin']), '3.5 %');
    expect(memoryText(stats['jellyfin'], t), '1.5 unit.KB');
    expect(cpuText(stats['other']), '-');
    expect(memoryText(null, t), '-');
    expect(statsOf(null), isEmpty);
  });

  test('disk usage detail', () {
    expect(usageDetail({'active': 2, 'total': 5, 'reclaimable': 2048}, t), 'docker.usage_detail active=2 total=5 free=2.0 unit.KB');
    expect(usageDetail({}, t), 'docker.usage_detail active=0 total=0 free=0 unit.B');
  });

  test('container names are escaped in paths', () {
    expect(containerPath('dahlia-jellyfin-1', 'restart'), '/api/docker/dahlia-jellyfin-1/restart');
    expect(containerPath('a b', 'stop'), '/api/docker/a%20b/stop');
    expect(logsPath('web'), '/api/docker/web/logs?lines=500');
  });
}
