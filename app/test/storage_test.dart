import 'package:dahlia/src/server.dart';
import 'package:dahlia/src/storage.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_dahlia.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  final disks = <Map>[
    {'path': '/dev/sda1', 'disk': '/dev/sda', 'kind': 'part', 'status': 'registered', 'uuid': 'a'},
    {'path': '/dev/sdb', 'disk': '/dev/sdb', 'kind': 'disk', 'status': 'empty', 'removable': false},
    {'path': '/dev/sda2', 'disk': '/dev/sda', 'kind': 'part', 'status': 'candidate', 'uuid': 'b'},
    {'path': '/dev/nvme0n1p2', 'disk': '/dev/nvme0n1', 'kind': 'part', 'status': 'system'},
  ];

  test('one group per physical disk, in list order', () {
    final g = groupDisks(disks);
    expect([for (final (d, _) in g) d], ['/dev/sda', '/dev/sdb', '/dev/nvme0n1']);
    expect([for (final p in g.first.$2) p['path']], ['/dev/sda1', '/dev/sda2']);
    expect(diskStatus(g[0].$2), 'registered');
    expect(diskStatus(g[1].$2), isNull);
    expect(diskStatus(g[2].$2), 'system');
    expect(shortName('/dev/nvme0n1'), 'nvme0n1');
  });

  test('what a disk offers', () {
    final candidate = {'path': '/dev/sdc1', 'disk': '/dev/sdc', 'status': 'candidate'};
    final external = {'status': 'external-only'};
    final blank = {'path': '/dev/sdb', 'disk': '/dev/sdb', 'status': 'empty'};
    final blankPart = {'path': '/dev/sdb1', 'disk': '/dev/sdb', 'status': 'empty'};
    final imported = {'path': '/dev/sdc1', 'disk': '/dev/sdc', 'status': 'candidate', 'imported': true};
    expect(canAdd(candidate), isTrue);
    expect(rolesFor(candidate), roles);
    expect(rolesFor(external), ['external']);
    expect(canImport(candidate), isTrue);
    expect(canImport(imported), isFalse);
    expect(canFormat(blank), isTrue);
    expect(canFormat(blankPart), isFalse);
    expect(canFormat(imported), isTrue);
    expect(canRemove({'status': 'registered'}), isTrue);
    expect(canRemove(candidate), isFalse);
    expect(offerBurnin('/dev/sdb', [blank..['kind'] = 'disk']), isTrue);
    expect(offerBurnin('/dev/loop0', [blank]), isFalse);
    expect(offerBurnin('/dev/sdb', [{...blank, 'removable': true}]), isFalse);
    expect(offerBurnin('/dev/sda', groupDisks(disks).first.$2), isFalse);
  });

  test('mount of a filesystem, also through its LUKS container', () {
    final mounts = <Map>[
      {'source': '/dev/sda1', 'mountpoint': '/mnt/disk1'},
      {'source': '/dev/mapper/luks-u1', 'mountpoint': '/mnt/disk2'},
    ];
    expect(mountOf({'path': '/dev/sda1'}, mounts)?['mountpoint'], '/mnt/disk1');
    expect(mountOf({'path': '/dev/sdb1', 'luks': 'u1'}, mounts)?['mountpoint'], '/mnt/disk2');
    expect(mountOf({'path': '/dev/sdc1'}, mounts), isNull);
  });

  test('labels, temperatures and percentages', () {
    expect(fsLabel('/', t), 'fs.system');
    expect(fsLabel('/mnt/user', t), 'fs.user');
    expect(fsLabel('/mnt/disk3', t), 'fs.disk n=3');
    expect(fsLabel('/mnt/parity1', t), 'fs.parity n=1');
    expect(fsLabel('/mnt/external/usb', t), 'fs.external name=usb');
    expect(fsLabel('/boot/efi', t), '/boot/efi');
    expect(tempLevel(44, true), 'ok');
    expect(tempLevel(45, true), 'warn');
    expect(tempLevel(55, true), 'crit');
    expect(tempLevel(55, false), 'ok');
    expect(tempLevel(70, false), 'crit');
    expect(percent(1, 3), 33);
    expect(percent(5, 0), 0);
    expect(percent(9, 4), 100);
  });

  test('import target and share names', () {
    expect(importTarget({'label': 'My Photos!', 'path': '/dev/sdc1'}), 'import-my-photos');
    expect(importTarget({'path': '/dev/sdc1'}), 'import-sdc1');
    expect(importTarget({'label': 'x' * 80, 'path': '/dev/sdc1'}).length, 64);
    expect(shareNameRe.hasMatch('import-sdc1'), isTrue);
    expect(shareNameRe.hasMatch('-bad'), isFalse);
    expect(shareNameRe.hasMatch('a/b'), isFalse);
  });

  test('free space and mover age settings', () {
    expect(parseFree('50G'), (50, 'G'));
    expect(parseFree('200M'), (200, 'M'));
    expect(parseFree('2T'), (2, 'T'));
    expect(parseFree('10485760'), (10, 'M'));
    expect(parseFree('100K'), (1, 'M'));
    expect(parseFree('junk'), (50, 'G'));
    expect(moverAgesWith(60), moverAges);
    expect(moverAgesWith(90), [0, 60, 90, 360, 720, 1440, 4320, 10080, 43200]);
    expect(moverAgeLabel(0, t), 'storage.mover_age_none');
    expect(moverAgeLabel(2880, t), 'storage.mover_age_days n=2');
    expect(moverAgeLabel(360, t), 'storage.mover_age_hours n=6');
    expect(moverAgeLabel(90, t), 'storage.mover_age_minutes n=90');
  });

  test('a cache with files needs a second yes; the detail comes from the server', () async {
    expect(needsForce(422, 'cache still holds files, use --force'), isTrue);
    expect(needsForce(422, 'busy'), isFalse);
    expect(needsForce(500, '--force'), isFalse);
    final fake = FakeDahlia();
    await fake.start();
    addTearDown(() => fake.http.close(force: true));
    final s = Server(host: fake.host, pem: fixture('ca.crt'), appVersion: '1.4.0');
    await expectLater(
      s.post('/api/storage/remove', {'uuid': 'a'}),
      throwsA(isA<ApiError>()
          .having((e) => e.status, 'status', 422)
          .having((e) => e.detail, 'detail', contains('--force'))),
    );
  });
}
