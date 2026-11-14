import 'package:dahlia/src/parity.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

Map parity({Map? status, Map runs = const {}, Map jobs = const {}, bool mounted = true, List problems = const []}) => {
      'configured': problems.isEmpty,
      'problems': problems,
      'parity': [
        {'index': 1, 'mountpoint': '/mnt/parity1', 'size': 4000, 'mounted': true}
      ],
      'data': [
        {'index': 1, 'mountpoint': '/mnt/disk1', 'size': 2000, 'mounted': mounted},
        {'index': 2, 'mountpoint': '/mnt/disk2', 'size': 2000, 'mounted': true},
      ],
      'status': status,
      'runs': runs,
      'jobs': jobs,
      'history': [],
      'error': '',
    };

void main() {
  final synced = {
    'files': 10,
    'size': 100,
    'bad': 0,
    'unsynced': 0,
    'blocks': 200,
    'unscrubbed': 50,
    'disks': {
      'd1': {'files': 7}
    },
  };

  test('a unit runs while activating, the repair unit not after it exited', () {
    expect(unitRunning({'active': 'activating'}), isTrue);
    expect(unitRunning({'active': 'active', 'sub': 'running'}), isTrue);
    expect(unitRunning({'active': 'active', 'sub': 'exited'}), isFalse);
    expect(unitRunning({'active': 'inactive'}), isFalse);
    expect(unitRunning(null), isFalse);
    expect(runningJob(parity(jobs: {'scrub': {'active': 'activating'}})), 'scrub');
    expect(runningJob(parity()), '');
  });

  test('state follows the dashboard', () {
    expect(parityState(parity(problems: ['no parity disk'])), ('state_none', ''));
    expect(parityState(parity()), ('state_unsynced', 'warn'));
    expect(parityState(parity(status: {...synced, 'bad': 3})), ('state_bad', 'crit'));
    expect(parityState(parity(status: synced, runs: {'sync': {'result': 'blocked'}})), ('state_stale', 'warn'));
    expect(parityState(parity(status: synced)), ('state_ok', 'ok'));
  });

  test('what may be started', () {
    expect(canSync(parity()), isTrue);
    expect(canScrub(parity()), isFalse, reason: 'scrub needs a first sync');
    expect(canScrub(parity(status: synced)), isTrue);
    expect(canRestore(parity(status: synced)), isTrue);
    expect(canSync(parity(mounted: false)), isFalse);
    expect(canSync(parity(jobs: {'mover': {'active': 'activating'}})), isFalse);
    expect(restoreTargets(parity()), ['disk1', 'disk2']);
  });

  test('run texts and history', () {
    String time(num? s) => 'T$s';
    expect(runText(null, t, time), 'parity.never');
    expect(runText({'end': 5, 'result': 'ok', 'counts': {'added': 2}}, t, time),
        'T5: ok (parity.counts added=2 removed=0 updated=0)', reason: 'unknown result keys show the value');
    expect(runText({'end': 5, 'result': 'failed', 'message': 'disk gone'}, t, time), 'T5: failed (disk gone)');
    expect(fixTarget('d2', t), '/mnt/disk2');
    expect(fixTarget('bad', t), 'parity.what_bad');
    expect(historyKind({'kind': 'fix', 'what': 'd1'}, t), 'parity.kind_fix (/mnt/disk1)');
    expect(historyDetail({'message': 'x'}, t), 'x');
    expect(historyDetail({}, t), '-');
    expect(runDuration(42), '42 s');
    expect(runDuration(125), '2 min');
    expect(runDuration(3725), '1 h 2 min');
    expect(resultLevel('no_changes'), 'ok');
    expect(resultLevel('blocked'), 'warn');
    expect(checkedPercent(synced), 75);
    expect(checkedPercent(null), isNull);
  });

  test('disk rows carry the file count of data disks', () {
    final rows = diskRows(parity(status: synced));
    expect([for (final r in rows) r.$1], ['parity.parity_n', 'parity.data_n', 'parity.data_n']);
    expect([for (final r in rows) r.$4], ['-', '7', '-']);
  });

  test('notices and their buttons', () {
    List<(String, String)> keys(Map d) => [for (final n in notices(d)) (n.$1, n.$3)];
    expect(keys(parity(status: synced)), isEmpty);
    expect(keys(parity(status: {...synced, 'bad': 2})), [('parity.bad', 'repair')]);
    expect(keys(parity(status: {...synced, 'bad': 2}, mounted: false)), [('parity.unmounted', ''), ('parity.bad', '')]);
    final blocked = parity(status: synced, runs: {
      'sync': {'result': 'blocked', 'counts': {'removed': 900}, 'threshold': 50}
    });
    final (key, vars, action) = notices(blocked).single;
    expect((key, action), ('parity.blocked', 'force'));
    expect(vars, {'n': 900, 'threshold': 50});
    expect(keys(parity(jobs: {'sync': {'active': 'activating'}}, status: {...synced, 'unsynced': 1})), [('parity.running_sync', '')]);
    expect(keys({...parity(), 'parity': []}), [('parity.no_parity', 'disks')]);
  });

  test('settings are checked against the form bounds', () {
    expect(settingsBody({'snapraid_delete_threshold': '50', 'scrub_percent': '8', 'scrub_older_than_days': '10'}),
        {'snapraid_delete_threshold': 50, 'scrub_percent': 8, 'scrub_older_than_days': 10});
    expect(settingsBody({'snapraid_delete_threshold': '50', 'scrub_percent': '0', 'scrub_older_than_days': '10'}), isNull);
    expect(settingsBody({'snapraid_delete_threshold': '', 'scrub_percent': '8', 'scrub_older_than_days': '10'}), isNull);
  });
}
