import 'package:dahlia/src/updates.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  test('packages split into open and done', () {
    final (open, done) = splitPackages({
      'packages': [
        {'name': 'a', 'step': 'done'},
        {'name': 'b', 'step': 'unpacked'},
        {'name': 'c', 'step': 'waiting'},
      ],
    });
    expect([for (final p in open) p['name']], ['b', 'c']);
    expect([for (final p in done) p['name']], ['a']);
    expect(progressLevel('running'), 'info');
    expect(progressLevel('failed'), 'crit');
    expect(progressLevel('done'), 'ok');
  });

  test('restart times on the half hour, a saved odd time kept', () {
    final times = rebootTimes('04:00');
    expect(times.length, 48);
    expect(times.first, '00:00');
    expect(times[9], '04:30');
    expect(rebootTimes('04:15').last, '04:15');
  });

  test('auto body checks the time', () {
    expect(autoBody(enabled: true, reboot: false, time: '03:30'), {'enabled': true, 'reboot': false, 'reboot_time': '03:30'});
    expect(autoBody(enabled: true, reboot: true, time: '24:00'), isNull);
  });

  test('pending restart: null none, empty list generic, packages named', () {
    expect(rebootNotice(null, t), isNull);
    expect(rebootNotice(const [], t), 'auto.reboot_needed');
    expect(rebootNotice(['linux-image-amd64', 'libc6'], t), 'auto.reboot_needed_pkgs list=linux-image-amd64, libc6');
  });

  test('snapshot labels and the pre snapshot of a post one', () {
    final all = [
      {'number': 4, 'type': 'pre', 'description': 'apt'},
      {'number': 5, 'type': 'post', 'pre_number': 4, 'description': 'apt'},
      {'number': 6, 'type': 'single', 'userdata': {'important': 'yes'}},
    ];
    expect(snapshotTags(all[0]).single.$1, 'snap.type_pre');
    final post = snapshotTags(all[1]).single;
    expect((post.$1, post.$2['n'], post.$3), ('snap.type_post', 4, ''));
    expect(snapshotTags(all[2]).single.$1, 'snap.important');
    expect(preOf(all[1], all)?['number'], 4);
    expect(preOf(all[0], all), isNull);
    expect([for (final s in snapshotsNewestFirst({'snapshots': all})) s['number']], [6, 5, 4]);
  });

  test('descriptions as the agent allows them', () {
    expect(validDescription('Vor dem Umbau (Küche) 1.0'), isTrue);
    expect(validDescription('a/b+c-d:e,f'), isTrue);
    expect(validDescription(''), isFalse);
    expect(validDescription('rm -rf; ls'), isFalse);
    expect(validDescription('x' * 81), isFalse);
  });

  test('local times', () {
    expect(localTime('2026-11-14 03:00:00'), DateTime(2026, 11, 14, 3));
    expect(localTime(''), isNull);
    expect(localTime('2026-11-14T03:00:00+00:00'), DateTime.utc(2026, 11, 14, 3).toLocal());
    expect(oldRoots({'old_roots': ['@.pre-rollback-20261114-030000']}), ['@.pre-rollback-20261114-030000']);
  });
}
