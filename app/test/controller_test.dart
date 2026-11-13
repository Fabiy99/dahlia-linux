import 'dart:io';

import 'package:dahlia/src/controller.dart';
import 'package:dahlia/src/settings.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_dahlia.dart';

class MemorySecrets extends Secrets {
  final values = <String, String>{};

  @override
  Future<String?> read(String host, String what) async => values['$host/$what'];

  @override
  Future<void> write(String host, String what, String? value) async {
    if (value == null) {
      values.remove('$host/$what');
    } else {
      values['$host/$what'] = value;
    }
  }
}

void main() {
  late FakeDahlia fake;
  late Directory dir;
  late MemorySecrets secrets;

  Controller make() => Controller(
        settingsFile: File('${dir.path}/settings.json'),
        loadAsset: (p) async => File(p).readAsString(),
        secrets: secrets,
        version: '1.4.0',
      );

  setUp(() async {
    fake = FakeDahlia();
    await fake.start();
    dir = await Directory.systemTemp.createTemp('dahlia-ctl');
    secrets = MemorySecrets();
  });
  tearDown(() async {
    await fake.http.close(force: true);
    await dir.delete(recursive: true);
  });

  test('pair, log in, then the next start signs in by itself', () async {
    final c = make();
    await c.start();
    expect(c.stage, Stage.connect);
    expect(c.t('connect.title'), 'Connect to your Dahlia');
    await c.choose(fake.host);
    expect(c.stage, Stage.fingerprint);
    await c.confirmFingerprint();
    expect(c.stage, Stage.login);
    await c.login('fabian', 'wrong');
    expect([c.stage, c.error], [Stage.login, 'error.invalid_credentials']);
    await c.login('Fabian ', 'pw');
    expect(c.stage, Stage.home);
    expect(secrets.values['${fake.host}/password'], 'pw');
    c.dispose();

    // Session gone on the server: the saved password signs in again.
    secrets.values.remove('${fake.host}/session');
    final again = make();
    await again.start();
    expect(again.stage, Stage.home);
    expect(again.settings.username, 'fabian');
    again.dispose();
  });

  test('pairing code is checked against the CA', () async {
    final c = make();
    await c.start();
    await c.choose(fake.host);
    await c.pairWithCode('ABCDE-FGHJL');
    expect([c.stage, c.error], [Stage.fingerprint, 'error.proof']);
    await c.pairWithCode('abcde fghjk');
    expect(c.stage, Stage.login);
    c.dispose();
  });

  test('without saving, the next start asks again', () async {
    final c = make();
    await c.start();
    await c.choose(fake.host);
    await c.confirmFingerprint();
    await c.login('fabian', 'pw', save: false);
    expect(c.stage, Stage.home);
    expect(secrets.values, isEmpty);
    c.dispose();
    final again = make();
    await again.start();
    expect(again.stage, Stage.login);
    again.dispose();
  });

  test('areas of the top bar; signing out goes back to the start page', () async {
    final c = make();
    await c.start();
    await c.choose(fake.host);
    await c.confirmFingerprint();
    await c.login('fabian', 'pw', save: false);
    expect(c.area, Area.home);
    var told = 0;
    c.addListener(() => told++);
    c.go(Area.apps);
    expect(c.area, Area.apps);
    expect(told, 1);
    await c.logout();
    expect(c.stage, Stage.login);
    expect(c.area, Area.home);
    c.dispose();
  });

  test('another server version leads to the update, also later', () async {
    final c = make();
    await c.start();
    await c.choose(fake.host);
    await c.confirmFingerprint();
    await c.login('fabian', 'pw');
    fake.version = '1.5.0';
    await expectLater(c.server!.get('/api/state'), throwsA(anything));
    expect([c.stage, c.serverVersion], [Stage.update, '1.5.0']);
    c.dispose();
  });

  test('a changed certificate asks to pair again; language is kept', () async {
    Settings(host: fake.host, pem: fixture('other-ca.crt'), lang: 'de').save(File('${dir.path}/settings.json'));
    final c = make();
    await c.start();
    expect([c.stage, c.error], [Stage.connect, 'error.changed']);
    expect(c.t('connect.title'), 'Mit deiner Dahlia verbinden');
    await c.otherServer();
    expect(c.settings.lang, 'de');
    expect(c.settings.paired, isFalse);
    c.dispose();
  });
}
