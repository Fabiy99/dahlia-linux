import 'dart:io';

import 'package:dahlia/src/manifest.dart';
import 'package:flutter_test/flutter_test.dart';

String fixture(String name) => File('test/fixtures/$name').readAsStringSync();

/// Serves test/fixtures under /app/ and counts requests.
Future<HttpServer> serve(Map<String, List<int>> files) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  server.listen((req) {
    final body = files[req.uri.path];
    if (body == null) {
      req.response.statusCode = 404;
    } else {
      req.response.add(body);
    }
    req.response.close();
  });
  return server;
}

void main() {
  final key = fixture('test-key.asc');
  final manifest = File('test/fixtures/manifest.json').readAsBytesSync();
  final sig = File('test/fixtures/manifest.json.sig').readAsBytesSync();
  final installer = File('test/fixtures/installer.bin').readAsBytesSync();

  test('only plain release versions update', () {
    expect(isReleaseVersion('1.4.0'), isTrue);
    for (final v in ['dev', '1.4', '1.4.0-rc1', '../1.4.0', '']) {
      expect(isReleaseVersion(v), isFalse, reason: v);
    }
  });

  test('parse checks version and fields', () {
    final m = Manifest.parse(fixture('manifest.json'), '1.4.0');
    expect(m.files.keys, containsAll(['windows-x64', 'macos', 'linux-x64']));
    expect(m.forThisPlatform()!.size, installer.length);
    expect(() => Manifest.parse(fixture('manifest.json'), '1.3.0'), throwsA(isA<UpdateError>()));
    expect(() => Manifest.parse('{', '1.4.0'), throwsA(isA<UpdateError>()));
    final http = fixture('manifest.json').replaceAll('https://', 'http://');
    expect(() => Manifest.parse(http, '1.4.0'), throwsA(isA<UpdateError>()));
  });

  test('fetch verifies the signature', () async {
    final server = await serve({'/app/1.4.0.json': manifest, '/app/1.4.0.json.sig': sig});
    final base = 'http://127.0.0.1:${server.port}/app/';
    final m = await fetchManifest('1.4.0', publicKey: key, base: base);
    expect(m.version, '1.4.0');
    await expectLater(
      fetchManifest('1.5.0', publicKey: key, base: base),
      throwsA(isA<UpdateError>().having((e) => e.key, 'key', 'no_manifest')),
    );
    await expectLater(
      fetchManifest('1.4.0', publicKey: fixture('other-key.asc'), base: base),
      throwsA(isA<UpdateError>().having((e) => e.key, 'key', 'signature')),
    );
    await server.close(force: true);
  });

  test('download checks size and checksum', () async {
    final server = await serve({'/i.bin': installer, '/bad.bin': [...installer]..[0] ^= 1});
    final dir = await Directory.systemTemp.createTemp('dahlia-dl');
    final good = Manifest.parse(fixture('manifest.json'), '1.4.0').forThisPlatform()!;
    Asset at(String path) => Asset(
          name: good.name,
          url: Uri.parse('http://127.0.0.1:${server.port}$path'),
          sha256: good.sha256,
          size: good.size,
        );
    final target = File('${dir.path}/i.bin');
    final seen = <double>[];
    await download(at('/i.bin'), target, progress: seen.add);
    expect(await target.readAsBytes(), installer);
    expect(seen.last, 1.0);
    final bad = File('${dir.path}/bad.bin');
    await expectLater(
      download(at('/bad.bin'), bad),
      throwsA(isA<UpdateError>().having((e) => e.key, 'key', 'checksum')),
    );
    expect(bad.existsSync(), isFalse);
    await expectLater(download(at('/missing'), bad), throwsA(isA<UpdateError>()));
    await server.close(force: true);
    await dir.delete(recursive: true);
  });
}
