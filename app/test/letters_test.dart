import 'package:dahlia/src/letters.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  setUpAll(() => initializeDateFormatting());

  test('a full week letter', () {
    final lines = letterLines({
      'kind': 'week',
      'storage': {'used': 2000000000, 'size': 4000000000, 'added': 1000000},
      'backups': {'runs': 7, 'ok': 6, 'probe': 'success'},
      'updates': 12,
      'apps': {'count': 3, 'new': ['Immich'], 'updated': []},
      'traffic': {'rx': 1000, 'tx': 2000},
      'cpu_avg': 12.4,
      'temp_max': 51.6,
      'uptime': 3 * 86400 + 5,
      'notices': {'warning': 1, 'critical': 0, 'titles': ['Disk hot']},
    }, t);
    expect(lines.map((l) => l.$1), ['storage', 'backup', 'updates', 'apps', 'network', 'cpu', 'uptime', 'notices']);
    expect(lines[0].$2, contains('letter.storage_added'));
    expect(lines[1].$2, 'letter.backups ok=6 runs=7 letter.probe_ok');
    expect(lines[3].$2, contains('letter.apps_new names=Immich'));
    expect(lines[5].$2, 'letter.cpu_temp cpu=12 temp=52');
    expect(lines[6].$2, 'letter.uptime days=3');
    expect(lines.last.$2, endsWith(': Disk hot'));
  });

  test('a quiet first letter and an empty year', () {
    expect(letterLines({'kind': 'week', 'storage': {'used': 1, 'size': 2}, 'backups': {'runs': 0}}, t).map((l) => l.$2),
        [startsWith('letter.storage '), 'letter.no_backups', 'letter.quiet']);
    final year = {'kind': 'year', 'id': '2026', 'preview': true, 'weeks': 0};
    expect(letterTitle(year, t), 'letter.year_now year=2026');
    expect(letterWhen(year, t, 'de'), 'letter.no_weeks');
    expect(letterLines(year, t).single.$2, 'letter.quiet_year');
  });

  test('names and dates', () {
    int at(int d) => DateTime(2026, 11, d).millisecondsSinceEpoch ~/ 1000;
    final w = {'kind': 'week', 'id': '2026-W46', 'from': at(9), 'to': at(16)};
    expect(letterTitle(w, t), 'letter.week');
    expect(letterWhen(w, t, 'de'), 'letter.range from=9. November to=15. November 2026');
    expect(letterName(w, t, 'en'), startsWith('letter.week_of date=Nov'));
  });
}
