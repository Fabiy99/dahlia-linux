import 'package:dahlia/src/jobs.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('jobs in order, startable only when idle', () {
    final jobs = jobList({
      'mover': {'active': 'activating', 'result': ''},
      'update': {'active': 'inactive', 'result': 'success'},
      'broken': 'x',
      'backup': {'active': 'inactive', 'result': 'exit-code'},
    });
    expect([for (final (n, _) in jobs) n], ['mover', 'update', 'backup']);
    expect(jobActive(jobs[0].$2), isTrue);
    expect(canStartJob('mover', jobs[0].$2), isFalse);
    expect(canStartJob('update', jobs[1].$2), isFalse);
    expect(canStartJob('backup', jobs[2].$2), isTrue);
    expect(jobList(null), isEmpty);
  });

  test('result levels', () {
    expect(jobResultLevel('success'), 'ok');
    expect(jobResultLevel('signal'), 'warn');
    expect(jobResultLevel('exit-code'), 'crit');
    expect(jobResultLevel('timeout'), 'crit');
    expect(jobResultLevel(''), '');
  });

  test('log query checks the service name', () {
    expect(logQuery(lines: 300, priority: 'info', unit: ''), 'lines=300&priority=info');
    expect(logQuery(lines: 100, priority: 'err', unit: ' docker.service '), 'lines=100&priority=err&unit=docker.service');
    expect(logQuery(lines: 100, priority: 'err', unit: 'dahlia-app@x.service'), contains('unit=dahlia-app%40x.service'));
    expect(logQuery(lines: 100, priority: 'err', unit: 'a b'), isNull);
    expect(logQuery(lines: 100, priority: 'err', unit: '-x'), isNull);
  });

  test('line levels', () {
    expect(lineLevel('kernel: I/O Error on sdb'), 'crit');
    expect(lineLevel('unit failed to start'), 'crit');
    expect(lineLevel('Warning: low space'), 'warn');
    expect(lineLevel('started docker'), '');
    expect(lineLevel('errors=remount-ro'), '');
  });

  test('diagnostics file name', () {
    expect(diagnosticsName(DateTime(2026, 11, 4, 9, 5)), 'diagnostics-20261104-0905.tar.gz');
  });
}
