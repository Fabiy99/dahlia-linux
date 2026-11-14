import 'package:dahlia/src/services.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  test('schedules from the server and back', () {
    final s = Schedule.of({'frequency': 'weekly', 'time': '02:30', 'weekday': 'sat', 'monthday': 40, 'enabled': true});
    expect(s.body, {'frequency': 'weekly', 'time': '02:30', 'weekday': 'sat', 'monthday': 1});
    expect(Schedule.of(null).body, {'frequency': 'off', 'time': '03:00', 'weekday': 'sun', 'monthday': 1});
    expect(Schedule.of({'frequency': 'yearly'}).frequency, 'off');
    expect(scheduleNote({'enabled': true, 'frequency': 'daily', 'next': 'Sun 2026-11-15 03:00'}, t), 'schedule.next time=Sun 2026-11-15 03:00');
    expect(scheduleNote({'enabled': true, 'frequency': 'off', 'next': 'x'}, t), 'schedule.disabled');
    expect(scheduleNote(null, t), 'schedule.disabled');
  });

  test('notification channels', () {
    expect(channelBody('email', true, {'host': ' smtp.example ', 'port': '587', 'security': 'starttls', 'user': 'u', 'password': '', 'sender': 'a@b', 'to': 'c@d'}), {
      'enabled': true,
      'host': 'smtp.example',
      'port': 587,
      'security': 'starttls',
      'user': 'u',
      'password': '',
      'sender': 'a@b',
      'to': 'c@d',
    });
    expect(channelBody('gotify', false, {'url': 'https://g', 'token': 'x'}), {'enabled': false, 'url': 'https://g', 'token': 'x'});
    expect(channelBody('ntfy', true, {})['topic'], '');
    expect(secretSet({'token_set': true}, 'token'), isTrue);
    expect(testResult({'results': {'email': 'ok', 'ntfy': 'HTTP 401'}}, t), ('notify.email: notify.sent\nnotify.ntfy: HTTP 401', false));
    expect(testResult({'results': {'gotify': 'ok'}}, t).$2, isTrue);
    expect(historyFailures({'results': {'email': 'ok', 'ntfy': 'timeout'}}, t), 'notify.ntfy: timeout');
  });

  test('UPS', () {
    expect(upsShutdownLabel(0, t), 'ups.on_low_battery');
    expect(upsShutdownLabel(5, t), 'ups.after n=5');
    expect(upsBody(mode: 'usb', name: ' ', shutdown: 5, host: 'x'), {'mode': 'usb', 'name': 'ups', 'shutdown_minutes': 5});
    expect(upsBody(mode: 'remote', name: 'apc', shutdown: 0, host: ' nas ', port: '', user: 'monuser', password: 'pw'),
        {'mode': 'remote', 'name': 'apc', 'shutdown_minutes': 0, 'host': 'nas', 'port': 3493, 'user': 'monuser', 'password': 'pw'});
    expect(upsFacts(null, t), [('ups.state', 'ups.mode_off', '')]);
    expect(upsFacts({'reachable': false, 'error': 'refused'}, t), [('ups.state', 'ups.unreachable', 'crit'), ('ups.error', 'refused', '')]);
    expect(upsFacts({'reachable': true, 'on_battery': true, 'model': 'Back-UPS', 'charge': 80, 'runtime': 1500, 'load': null}, t), [
      ('ups.state', 'ups.on_battery', 'warn'),
      ('ups.model', 'Back-UPS', ''),
      ('ups.charge', '80 %', ''),
      ('ups.runtime', 'ups.minutes n=25', ''),
    ]);
  });

  test('InfluxDB keeps empty secrets', () {
    final b = influxBody(enabled: true, version: 2, url: ' http://i:8086 ', org: 'o', bucket: 'b', database: '', username: '');
    expect(b.containsKey('token'), isFalse);
    expect(b['url'], 'http://i:8086');
    expect(influxBody(enabled: true, version: 1, url: '', org: '', bucket: '', database: 'd', username: 'u', password: 'p')['password'], 'p');
  });

  test('pairing code time left', () {
    final now = DateTime(2026, 11, 14, 23);
    expect(codeLeft(now.add(const Duration(seconds: 299)), now), '4:59');
    expect(codeLeft(now.add(const Duration(seconds: 5)), now), '0:05');
    expect(codeLeft(now, now), isNull);
  });
}
