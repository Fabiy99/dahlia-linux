import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:dahlia/src/headless.dart';
import 'package:flutter_test/flutter_test.dart';

/// A slot as iso/build-iso.sh writes it.
List<int> slot(String name, int size) {
  final b = StringBuffer('#DAHLIA-SLOT:$name:$size\n');
  while (b.length < size) {
    b.write('${'#' * 63}\n');
  }
  return utf8.encode(b.toString().substring(0, size));
}

void main() {
  test('values like the installer checks them', () {
    expect(validHostname('nas'), isTrue);
    expect(validHostname('Nas'), isFalse);
    expect(validAccount('fabian'), isTrue);
    for (final bad in ['root', 'dahlia', 'dahlia-x', 'a', 'Anna']) {
      expect(validAccount(bad), isFalse, reason: bad);
    }
    expect(validPassword('geheim123'), isTrue);
    expect(validPassword('kurz'), isFalse);
    expect(newToken(Random(1)), matches(RegExp(r'^[0-9a-f]{32}$')));
    expect(newSetupCode(Random(1)), matches(RegExp(r'^[2-9A-HJKMNP-Z]{5}-[2-9A-HJKMNP-Z]{5}$')));
  });

  test('slots keep their size', () {
    final s = headlessSlots(headlessConfig(
        hostname: 'nas', timezone: 'Europe/Berlin', keymap: 'de', pcs: ['192.168.1.20:8485'], token: 'ab' * 16));
    for (final e in s.entries) {
      expect(utf8.encode(e.value).length, slotSizes[e.key], reason: e.key);
    }
    expect(s['headless'], startsWith('HOSTNAME=nas\nTIMEZONE=Europe/Berlin\nKEYMAP=de\nPC=192.168.1.20:8485\nTOKEN='));
    expect(s['grub'], startsWith('set default=4\n'));
    expect(() => slotText('x' * 600, 512), throwsArgumentError);
  });

  test('patches a copy of the ISO in place', () async {
    final dir = await Directory.systemTemp.createTemp('headless');
    addTearDown(() => dir.delete(recursive: true));
    final iso = File('${dir.path}/in.iso');
    final bytes = <int>[
      ...List.filled(2048 * 3, 0x41),
      ...slot('grub', 512), ...List.filled(2048 - 512, 0),
      ...List.filled(2048 * 700, 0x42),
      ...slot('headless', 4096),
      ...slot('isolinux', 512), ...List.filled(2048 - 512, 0),
      ...List.filled(2048, 0x43),
    ];
    await iso.writeAsBytes(bytes);
    final out = File('${dir.path}/out.iso');
    final slots = headlessSlots(headlessConfig(hostname: 'nas', timezone: 'Europe/Berlin', keymap: 'de', pcs: ['10.0.2.2:8485'], token: 'ab' * 16));
    await patchIso(iso, out, slots);
    final got = await out.readAsBytes();
    expect(got.length, bytes.length);
    expect(await iso.readAsBytes(), bytes);
    expect(utf8.decode(got.sublist(2048 * 3, 2048 * 3 + 512)), slots['grub']);
    final h = 2048 * 704;
    expect(utf8.decode(got.sublist(h, h + 4096)), slots['headless']);
    expect(got.last, 0x43);
    // A stick image without slots (an older ISO) is refused.
    await iso.writeAsBytes(List.filled(4096, 0));
    expect(() => patchIso(iso, File('${dir.path}/x.iso'), slots), throwsA(isA<SlotError>()));
  });

  test('disk list and answer', () {
    final disks = parseDiskList('/dev/sda.500107862016.Samsung_SSD_870.S6PNNX0R.0~/dev/sdb.4000787030016.WDC_WD40.WD-WCC7.1~bad~');
    expect(disks.length, 2);
    expect(disks[0].model, 'Samsung SSD 870');
    expect(disks[1].hasData, isTrue);
    expect(disks[1].confirmWord, 'WD-WCC7');
    final a = answerText(disk: disks[0], account: 'fabian', password: 'geheim123', code: 'ABCDE-FGH23');
    expect(a, 'DISK=/dev/sda\nACCOUNT=fabian\nPASSWORD=67656865696d313233\nSETUP_CODE=ABCDE-FGH23\nCONFIRM=\n');
    expect(sameFingerprint('ab' * 32, List.filled(32, 'AB').join(':')), isTrue);
    expect(sameFingerprint('', ''), isFalse);
  });

  test('serves the installer', () async {
    var changes = 0;
    final srv = HeadlessServer('ab' * 16, onChange: () => changes++);
    final port = await srv.start(port: 18485);
    addTearDown(srv.stop);
    final http = HttpClient();
    addTearDown(() => http.close(force: true));
    Future<(int, String)> get(String path) async {
      final r = await (await http.getUrl(Uri.parse('http://127.0.0.1:$port$path'))).close();
      return (r.statusCode, await r.transform(utf8.decoder).join());
    }

    final t = 'ab' * 16;
    expect((await get('/headless/wrong/hello')).$1, 404);
    expect(await get('/headless/$t/hello?disks=/dev/sda.1000.M.S.0'), (200, 'ok\n'));
    expect(srv.disks!.single.path, '/dev/sda');
    expect(srv.installerIp, '127.0.0.1');
    expect((await get('/headless/$t/answer')).$1, 204);
    srv.answer('DISK=/dev/sda\n');
    expect(await get('/headless/$t/answer'), (200, 'DISK=/dev/sda\n'));
    await get('/headless/$t/progress?step=rejected_confirm');
    expect(srv.rejected, 'confirm');
    expect(srv.answered, isFalse);
    await get('/headless/$t/progress?step=finished&fingerprint=${'cd' * 32}');
    expect(srv.step, 'finished');
    expect(srv.fingerprint, 'cd' * 32);
    expect(changes, greaterThan(3));
  });
}
