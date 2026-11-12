import 'dart:io';

import 'package:dahlia/src/settings.dart';
import 'package:dahlia/src/strings.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('settings roundtrip and defaults', () {
    final dir = Directory.systemTemp.createTempSync('dahlia-settings');
    final f = File('${dir.path}/sub/settings.json');
    final empty = Settings.load(f);
    expect(empty.paired, isFalse);
    expect(empty.lang, 'en');
    Settings(host: 'dahlia.local', pem: 'PEM', username: 'fabian', lang: 'de').save(f);
    final s = Settings.load(f);
    expect([s.host, s.pem, s.username, s.lang, s.autoLogin, s.paired],
        ['dahlia.local', 'PEM', 'fabian', 'de', true, true]);
    f.writeAsStringSync('{broken');
    expect(Settings.load(f).host, isNull);
    expect(Settings.fromJson({'lang': 'fr'}).lang, 'en');
    dir.deleteSync(recursive: true);
  });

  test('own texts first, then the server, then the key', () {
    final s = Strings('de', {'a': 'App {name}'}, {'a': 'Server', 'b': 'Server b'});
    expect(s.t('a', {'name': 'Dahlia'}), 'App Dahlia');
    expect(s.t('b'), 'Server b');
    expect(s.t('c'), 'c');
  });

  test('app locales have the same keys', () {
    final de = Strings.parse(File('assets/locales/de.json').readAsStringSync());
    final en = Strings.parse(File('assets/locales/en.json').readAsStringSync());
    expect(de.keys.toSet(), en.keys.toSet());
    expect(de['lang'], 'de');
    expect(en['lang'], 'en');
  });
}
