import 'package:dahlia/src/format.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key.replaceFirst('unit.', '') : '${vars['n']} ${key.split('.').last}';

void main() {
  test('names, sizes, durations', () {
    expect(displayName('fabian'), 'Fabian');
    expect(displayName(''), '');
    expect(bytes(512, t), '512 B');
    expect(bytes(1536, t), '1.5 KB');
    expect(bytes(4 * 1024 * 1024 * 1024 * 1024, t), '4.0 TB');
    expect(bytes(250 * 1024 * 1024, t), '250 MB');
    expect(duration(3 * 86400 + 5, t), '3 days');
    expect(duration(7200, t), '2 hours');
    expect(duration(600, t), '10 minutes');
  });

  test('dates in both languages', () async {
    await initializeDateFormatting();
    final d = DateTime(2026, 11, 12, 22, 5);
    expect(longDate(d, 'de'), 'Donnerstag, 12. November · 22:05');
    expect(longDate(d, 'en'), 'Thursday, 12 November · 22:05');
  });
}
