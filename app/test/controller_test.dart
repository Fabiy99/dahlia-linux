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

  final popped = <String>[];

  Controller make() => Controller(
        settingsFile: File('${dir.path}/settings.json'),
        loadAsset: (p) async => File(p).readAsString(),
        secrets: secrets,
        version: '1.4.0',
        popup: (n) async => popped.add(n.title),
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

  test('pending updates show a banner; closed, it returns with more updates', () async {
    fake.pending = ['libc6', 'base'];
    final c = make();
    await c.start();
    await c.choose(fake.host);
    await c.confirmFingerprint();
    await c.login('fabian', 'pw', save: false);
    await c.checkPendingUpdates();
    expect(c.pendingUpdates, 2);
    c.closeUpdateBanner();
    expect(c.updateBannerClosed, isTrue);
    await c.checkPendingUpdates();
    expect(c.updateBannerClosed, isTrue);
    fake.pending = ['libc6', 'base', 'agent'];
    await c.checkPendingUpdates();
    expect([c.pendingUpdates, c.updateBannerClosed], [3, false]);
    fake.pending = const [];
    await c.checkPendingUpdates();
    expect(c.pendingUpdates, 0);
    c.dispose();
  });

  test('notifications: old ones are only marked, new ones go to the desktop', () async {
    popped.clear();
    Map n(int time, String title) => {'time': time, 'level': 'warning', 'title': title, 'message': 'm'};
    fake.notices = [n(100, 'old')];
    final c = make();
    await c.start();
    await c.choose(fake.host);
    await c.confirmFingerprint();
    await c.login('fabian', 'pw', save: false);
    await c.checkNotices();
    expect([popped, c.unreadNotices, c.settings.noticeSeen], [<String>[], 0, 100]);
    fake.notices = [n(300, 'third'), n(200, 'second'), n(100, 'old')];
    await c.checkNotices();
    expect(popped, ['second', 'third']);
    expect(c.unreadNotices, 2);
    c.readNotices();
    expect(c.unreadNotices, 0);
    await c.checkNotices();
    expect(popped, hasLength(2));
    c.setDesktopNotices(false);
    fake.notices = [n(400, 'fourth'), ...fake.notices];
    await c.checkNotices();
    expect([popped.length, c.unreadNotices], [2, 1]);
    final saved = Settings.load(File('${dir.path}/settings.json'));
    expect([saved.noticeSeen, saved.noticeRead, saved.desktopNotices], [400, 300, false]);
    c.dispose();
  });

  test('the welcome tour runs once after the first sign-in and can be started again', () async {
    final c = make();
    await c.start();
    expect(c.tourActive, isFalse);
    await c.choose(fake.host);
    await c.confirmFingerprint();
    await c.login('fabian', 'pw', save: false);
    expect(c.tourActive, isTrue);
    c.go(Area.apps);
    c.finishTour();
    expect(c.tourActive, isFalse);
    expect(Settings.load(File('${dir.path}/settings.json')).tourDone, isTrue);
    c.restartTour();
    expect([c.tourActive, c.area], [true, Area.home]);
    c.dispose();
  });

  test('start screen comes from the server; its look is kept for the next start', () async {
    final c = make();
    await c.start();
    await c.choose(fake.host);
    await c.confirmFingerprint();
    await c.login('fabian', 'pw', save: false);
    await c.loadHome();
    expect(c.home.shown.map((t) => t.id), ['weather', 'apps']);
    expect([c.home.look.theme, c.home.look.accent, c.settings.look.accent], ['dark', 'teal', 'teal']);
    expect(Settings.load(File('${dir.path}/settings.json')).look.accent, 'teal');
    await c.saveHome({'tiles': c.home.tilesJson(c.home.change('cpu', on: true, size: 'xl'))});
    expect(c.home.tiles.last.toJson(), {'id': 'cpu', 'size': 'xl', 'on': true});
    expect(fake.home['tiles'], hasLength(3));
    await c.resetHome();
    expect(c.settings.look.accent, 'dahlia');
    c.setMood((kind: 'rain', day: false));
    expect(c.mood, (kind: 'rain', day: false));
    c.setAppUpdates(2);
    expect(c.appUpdates, 2);
    c.dispose();
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
