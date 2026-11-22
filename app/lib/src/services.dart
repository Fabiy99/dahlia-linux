// Services section of the settings without widgets: schedules, notification
// channels, InfluxDB export, UPS and the pairing code. Same rules as the
// dashboard's settings page.

import 'format.dart';
import 'letters.dart';

String _text(Object? v) => v == null ? '' : '$v';

const scheduleJobs = ['mover', 'snapraid-sync', 'snapraid-scrub', 'backup', 'backup-probe', 'smart-short', 'smart-long'];
const frequencies = ['off', 'hourly', 'daily', 'weekly', 'monthly'];
const weekdays = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

/// A job's schedule as the form edits it.
class Schedule {
  Schedule({this.frequency = 'off', this.time = '03:00', this.weekday = 'sun', this.monthday = 1});

  factory Schedule.of(Object? s) {
    if (s is! Map) return Schedule();
    final day = s['monthday'] is num ? (s['monthday'] as num).toInt() : 1;
    return Schedule(
      frequency: frequencies.contains(s['frequency']) ? '${s['frequency']}' : 'off',
      time: _text(s['time']).isEmpty ? '03:00' : _text(s['time']),
      weekday: weekdays.contains(s['weekday']) ? '${s['weekday']}' : 'sun',
      monthday: day < 1 || day > 28 ? 1 : day,
    );
  }

  String frequency;
  String time;
  String weekday;
  int monthday;

  Map<String, Object> get body => {'frequency': frequency, 'time': time, 'weekday': weekday, 'monthday': monthday};
}

/// Line below a schedule: next run or that it does not run.
String scheduleNote(Object? s, Translate t) {
  final next = s is Map ? _text(s['next']) : '';
  final on = s is Map && s['enabled'] == true && s['frequency'] != 'off' && next.isNotEmpty;
  return on ? t('schedule.next', {'time': next}) : t('schedule.disabled');
}

const notifyChannels = ['email', 'ntfy', 'gotify'];

/// Fields of a channel form: name, locale key, kind ('text', 'number',
/// 'secret', 'security'). The secret is kept on the server when left empty.
const channelFields = {
  'email': [
    ('host', 'notify.smtp_host', 'text'),
    ('port', 'notify.smtp_port', 'number'),
    ('security', 'notify.smtp_security', 'security'),
    ('user', 'notify.smtp_user', 'text'),
    ('password', 'notify.smtp_password', 'secret'),
    ('sender', 'notify.sender', 'text'),
    ('to', 'notify.to', 'text'),
  ],
  'ntfy': [
    ('url', 'notify.ntfy_url', 'text'),
    ('topic', 'notify.ntfy_topic', 'text'),
    ('token', 'notify.token', 'secret'),
  ],
  'gotify': [
    ('url', 'notify.gotify_url', 'text'),
    ('token', 'notify.gotify_token', 'secret'),
  ],
};

const smtpSecurity = ['starttls', 'tls', 'none'];

/// Settings of `POST /api/notify/<channel>` from the typed values.
Map<String, Object> channelBody(String channel, bool enabled, Map<String, String> values) => {
      'enabled': enabled,
      for (final (name, _, kind) in channelFields[channel] ?? const <(String, String, String)>[])
        name: kind == 'number' ? (int.tryParse((values[name] ?? '').trim()) ?? 0) : (values[name] ?? '').trim(),
    };

/// A saved secret is not sent back; the field says it is kept.
bool secretSet(Map ch, String name) => ch['${name}_set'] == true;

/// Result of a test message: one line per channel and whether all went out.
(String, bool) testResult(Object? r, Translate t) {
  final results = r is Map && r['results'] is Map ? r['results'] as Map : const {};
  final lines = [for (final e in results.entries) '${t('notify.${e.key}')}: ${e.value == 'ok' ? t('notify.sent') : e.value}'];
  return (lines.join('\n'), results.values.every((v) => v == 'ok'));
}

/// Channels that failed for a sent notification, as one line.
String historyFailures(Map h, Translate t) {
  final results = h['results'] is Map ? h['results'] as Map : const {};
  return [for (final e in results.entries) if (e.value != 'ok') '${t('notify.${e.key}')}: ${e.value}'].join(' · ');
}

const upsShutdown = [0, 1, 2, 5, 10, 15, 30, 60];

String upsShutdownLabel(int m, Translate t) => m == 0 ? t('ups.on_low_battery') : t('ups.after', {'n': m});

/// Body of `POST /api/ups`; the server's address only for a remote NUT server.
Map<String, Object> upsBody({required String mode, required String name, required int shutdown, String host = '', String port = '', String user = '', String password = ''}) => {
      'mode': mode,
      'name': name.trim().isEmpty ? 'ups' : name.trim(),
      'shutdown_minutes': shutdown,
      if (mode == 'remote') ...{
        'host': host.trim(),
        'port': int.tryParse(port.trim()) ?? 3493,
        'user': user.trim(),
        'password': password,
      },
    };

/// Facts about the UPS: locale key, value, level for a label ('' plain).
List<(String, String, String)> upsFacts(Object? s, Translate t) {
  if (s is! Map) return [('ups.state', t('ups.mode_off'), '')];
  if (s['reachable'] != true) return [('ups.state', t('ups.unreachable'), 'crit'), ('ups.error', _text(s['error']), '')];
  return [
    if (s['low_battery'] == true)
      ('ups.state', t('ups.low_battery'), 'crit')
    else if (s['on_battery'] == true)
      ('ups.state', t('ups.on_battery'), 'warn')
    else
      ('ups.state', t('ups.online'), 'ok'),
    if (_text(s['model']).isNotEmpty) ('ups.model', _text(s['model']), ''),
    if (s['charge'] != null) ('ups.charge', '${s['charge']} %', ''),
    if (s['runtime'] is num) ('ups.runtime', t('ups.minutes', {'n': ((s['runtime'] as num) / 60).round()}), ''),
    if (s['load'] != null) ('ups.load', '${s['load']} %', ''),
  ];
}

const energySources = ['off', 'ups', 'plug'];
const energyPlugs = {'shelly': 'Shelly', 'tasmota': 'Tasmota'};
const energyCurrencies = ['EUR', 'CHF', 'GBP', 'USD'];

/// Body of `POST /api/energy`; the plug only for source "plug".
Map<String, Object> energyBody({required String source, required String plug, required String host, required String price, required String currency}) => {
      'source': source,
      'plug': plug,
      'host': source == 'plug' ? host.trim() : '',
      'price': (num.tryParse(price.trim().replaceAll(',', '.')) ?? 0).round().clamp(0, 1000),
      'currency': currency,
    };

/// Draw now and over a year at that draw: locale key, value, level ('' plain).
List<(String, String, String)> energyFacts(Map data, Translate t, String lang) {
  if (data['source'] == 'off') return [];
  final w = data['watts'];
  if (w is! num) return [('energy.now', t('energy.no_reading'), 'warn')];
  final kwh = w * 24 * 365 / 1000;
  final price = data['price'] is num ? data['price'] as num : 0;
  return [
    ('energy.now', '${w.round()} W', ''),
    ('energy.year', t('energy.year_value', {'kwh': kwh.round(), 'cost': money(kwh * price / 100, '${data['currency'] ?? 'EUR'}', lang)}), ''),
  ];
}

/// Body of `POST /api/history/influx`; empty secrets keep the saved ones.
Map<String, Object> influxBody({
  required bool enabled,
  required int version,
  required String url,
  required String org,
  required String bucket,
  required String database,
  required String username,
  String token = '',
  String password = '',
}) =>
    {
      'enabled': enabled,
      'version': version,
      'url': url.trim(),
      'org': org.trim(),
      'bucket': bucket.trim(),
      'database': database.trim(),
      'username': username.trim(),
      if (token.isNotEmpty) 'token': token,
      if (password.isNotEmpty) 'password': password,
    };

/// How long a pairing code is still valid ("4:59"), null once expired.
String? codeLeft(DateTime until, DateTime now) {
  final left = until.difference(now).inSeconds;
  if (left <= 0) return null;
  return '${left ~/ 60}:${(left % 60).toString().padLeft(2, '0')}';
}
