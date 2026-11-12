import 'dart:io';

import 'package:dahlia/src/manifest.dart';
import 'package:dahlia/src/self_update.dart';
import 'package:flutter_test/flutter_test.dart';

final manifest = Manifest.parse(File('test/fixtures/manifest.json').readAsStringSync(), '1.4.0');

Future<Manifest> fetchOk(String v, {required String publicKey}) async {
  expect(v, '1.4.0');
  expect(publicKey, 'KEY');
  return manifest;
}

void main() {
  test('downloads the exact version and installs it', () async {
    File? installed;
    final u = SelfUpdate(
      target: '1.4.0',
      loadKey: () async => 'KEY',
      installable: () => true,
      fetch: fetchOk,
      fetchFile: (a, f, {progress}) async {
        progress!(1.0);
        await f.writeAsString('x');
      },
      install: (f) async {
        installed = f;
        throw UpdateError('install');
      },
    );
    final phases = <UpdatePhase>[];
    u.addListener(() => phases.add(u.phase));
    await u.run();
    expect(installed!.path, endsWith('installer.bin'));
    expect(phases, containsAllInOrder([UpdatePhase.checking, UpdatePhase.downloading, UpdatePhase.installing, UpdatePhase.failed]));
    expect(u.error, 'install');
  });

  test('a missing manifest or a copy that cannot install', () async {
    final u = SelfUpdate(
      target: '1.4.0',
      loadKey: () async => 'KEY',
      installable: () => true,
      fetch: (v, {required publicKey}) async => throw UpdateError('no_manifest'),
    );
    await u.run();
    expect([u.phase, u.error], [UpdatePhase.failed, 'no_manifest']);
    final m = SelfUpdate(target: '1.4.0', loadKey: () async => 'KEY', installable: () => false);
    await m.run();
    expect(m.phase, UpdatePhase.manual);
    expect(m.releasePage.toString(), endsWith('/releases/tag/v1.4.0'));
  });
}
