// Services section of the settings: schedules of the regular jobs,
// notifications (email, ntfy, Gotify), export to InfluxDB, the UPS and
// opening Dahlia on the phone or pairing another desktop app. Same API as
// the dashboard's settings page.

import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/services.dart';
import 'area.dart';
import 'cards.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class ServicesSection extends StatelessWidget {
  const ServicesSection(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) {
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final half = grid.cols == 1 ? full : (full - 18) / 2;
    return Wrap(spacing: 18, runSpacing: 18, children: [
      SizedBox(width: full, child: _SchedulesCard(c)),
      SizedBox(width: full, child: _NotifyCard(c)),
      SizedBox(width: half, child: _InfluxCard(c)),
      SizedBox(width: half, child: _UpsCard(c)),
      SizedBox(width: full, child: PhoneCard(c)),
    ]);
  }
}

Color _level(Palette p, String level) => switch (level) {
      'ok' => p.ok,
      'warn' => p.warn,
      'crit' => p.danger,
      _ => p.accent,
    };

Widget _fact(BuildContext context, String label, Widget value) => Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 160, child: Text(label, style: TextStyle(color: context.palette.muted))),
        Expanded(child: value),
      ]),
    );

// --- schedules ------------------------------------------------------------------

class _SchedulesCard extends StatefulWidget {
  const _SchedulesCard(this.c);

  final Controller c;

  @override
  State<_SchedulesCard> createState() => _SchedulesCardState();
}

class _SchedulesCardState extends State<_SchedulesCard> with AreaCalls, CardLoad {
  final edits = <String, Schedule>{};

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/settings/schedules';

  Map get all => d?['schedules'] is Map ? d!['schedules'] as Map : const {};

  @override
  void fill(Map data) {
    final s = data['schedules'] is Map ? data['schedules'] as Map : const {};
    for (final job in scheduleJobs) {
      edits[job] = Schedule.of(s[job]);
    }
  }

  Future<void> _time(Schedule e) async {
    final parts = e.time.split(':');
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: int.tryParse(parts.first) ?? 3, minute: parts.length > 1 ? int.tryParse(parts[1]) ?? 0 : 0),
      builder: (context, child) => MediaQuery(data: MediaQuery.of(context).copyWith(alwaysUse24HourFormat: true), child: child!),
    );
    if (picked == null) return;
    setState(() => e.time = '${picked.hour.toString().padLeft(2, '0')}:${picked.minute.toString().padLeft(2, '0')}');
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    if (d == null) return SettingsCard(c.t('settings.schedules_title'), [Busy(c.t('common.loading'))]);
    return SettingsCard(c.t('settings.schedules_title'), [
      cardHint(context, c.t('settings.schedules_hint')),
      for (final job in scheduleJobs)
        Builder(builder: (context) {
          final e = edits[job] ??= Schedule();
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Wrap(spacing: 12, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [
              SizedBox(width: 260, child: Text(tv(c, 'job', job), style: const TextStyle(fontWeight: FontWeight.w600))),
              SizedBox(
                width: 150,
                child: cardSelect('', e.frequency, [for (final f in frequencies) (f, c.t('schedule.$f'))], (v) => setState(() => e.frequency = v)),
              ),
              if (e.frequency == 'weekly')
                SizedBox(
                  width: 150,
                  child: cardSelect('', e.weekday, [for (final w in weekdays) (w, c.t('weekday.$w'))], (v) => setState(() => e.weekday = v)),
                ),
              if (e.frequency == 'monthly')
                SizedBox(
                  width: 150,
                  child: cardSelect('', e.monthday, [for (var n = 1; n <= 28; n++) (n, c.t('schedule.monthday', {'n': n}))], (v) => setState(() => e.monthday = v)),
                ),
              if (e.frequency != 'off')
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Tooltip(
                    message: e.frequency == 'hourly' ? c.t('schedule.hourly_hint') : '',
                    child: OutlinedButton.icon(onPressed: () => _time(e), icon: const Icon(Icons.schedule_rounded, size: 18), label: Text(e.time)),
                  ),
                ),
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: FilledButton(
                  onPressed: busy ? null : () => save(() => s.post('/api/settings/schedules/$job', e.body), '').then((_) {
                    if (mounted) say(ScaffoldMessenger.of(this.context), c.t('schedule.saved', {'job': tv(c, 'job', job)}));
                  }),
                  child: Text(c.t('common.save')),
                ),
              ),
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(scheduleNote(all[job], c.t), style: TextStyle(color: p.muted, fontSize: 12.5)),
              ),
            ]),
          );
        }),
    ]);
  }
}

// --- notifications --------------------------------------------------------------

class _NotifyCard extends StatefulWidget {
  const _NotifyCard(this.c);

  final Controller c;

  @override
  State<_NotifyCard> createState() => _NotifyCardState();
}

class _NotifyCardState extends State<_NotifyCard> with AreaCalls, CardLoad {
  final enabled = <String, bool>{};
  final fields = <String, Map<String, TextEditingController>>{};
  final security = <String, String>{};

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/notify';

  @override
  void fill(Map data) {
    for (final ch in notifyChannels) {
      final v = data[ch] is Map ? data[ch] as Map : const {};
      enabled[ch] = v['enabled'] == true;
      final map = fields[ch] ??= {};
      for (final (name, _, kind) in channelFields[ch]!) {
        final t = map[name] ??= TextEditingController();
        if (kind == 'security') {
          security[ch] = smtpSecurity.contains(v[name]) ? '${v[name]}' : 'starttls';
        } else {
          t.text = kind == 'secret' ? '' : '${v[name] ?? ''}';
        }
      }
    }
  }

  @override
  void dispose() {
    for (final m in fields.values) {
      for (final t in m.values) {
        t.dispose();
      }
    }
    super.dispose();
  }

  Future<void> _saveChannel(String ch) async {
    final values = {for (final e in fields[ch]!.entries) e.key: e.value.text};
    if (ch == 'email') values['security'] = security[ch] ?? 'starttls';
    await save(() => s.post('/api/notify/$ch', {'settings': channelBody(ch, enabled[ch] ?? false, values)}), '');
    if (mounted) say(ScaffoldMessenger.of(context), c.t('notify.saved', {'channel': c.t('notify.$ch')}));
  }

  Future<void> _test() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/notify/test', null, const Duration(minutes: 1)), m);
    if (mounted) setState(() => busy = false);
    if (r == null) return;
    final (text, ok) = testResult(r, c.t);
    say(m, text, error: !ok);
    await load();
  }

  Future<void> _event(String key, bool on) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.post('/api/notify/events', {'events': {key: on}}), m);
    if (r != null) say(m, c.t('notify.events_saved'));
    await load();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return SettingsCard(c.t('notify.title'), [Busy(c.t('common.loading'))]);
    final events = data['events'] is Map ? data['events'] as Map : const {};
    final history = data['history'] is List ? (data['history'] as List).whereType<Map>().take(10).toList() : <Map>[];
    final fmt = DateFormat.yMd(c.settings.lang).add_Hm();
    return SettingsCard(c.t('notify.title'), [
      cardHint(context, c.t('notify.hint')),
      Align(alignment: Alignment.centerLeft, child: OutlinedButton(onPressed: busy ? null : _test, child: Text(c.t('notify.test')))),
      const SizedBox(height: 14),
      for (final ch in notifyChannels)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Container(
            decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(14)),
            child: ExpansionTile(
              shape: const RoundedRectangleBorder(side: BorderSide.none),
              collapsedShape: const RoundedRectangleBorder(side: BorderSide.none),
              title: Row(children: [
                Text(c.t('notify.$ch'), style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(width: 10),
                Tag(c.t((data[ch] is Map && (data[ch] as Map)['enabled'] == true) ? 'notify.on' : 'notify.off'),
                    (data[ch] is Map && (data[ch] as Map)['enabled'] == true) ? p.ok : p.muted),
              ]),
              childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              expandedCrossAxisAlignment: CrossAxisAlignment.start,
              children: [
                cardCheck(c.t('notify.enabled'), enabled[ch] ?? false, (v) => setState(() => enabled[ch] = v)),
                const SizedBox(height: 6),
                for (final (name, label, kind) in channelFields[ch]!)
                  if (kind == 'security')
                    cardSelect(c.t(label), security[ch] ?? 'starttls', [for (final v in smtpSecurity) (v, c.t('notify.sec_$v'))], (v) => setState(() => security[ch] = v))
                  else
                    cardField(
                      fields[ch]![name]!,
                      c.t(label),
                      obscure: kind == 'secret',
                      hint: kind == 'secret' && data[ch] is Map && secretSet(data[ch] as Map, name) ? c.t('notify.secret_kept') : null,
                      formatters: kind == 'number' ? [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(5)] : null,
                    ),
                FilledButton(onPressed: busy ? null : () => _saveChannel(ch), child: Text(c.t('common.save'))),
              ],
            ),
          ),
        ),
      const SizedBox(height: 14),
      Text(c.t('notify.events'), style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 6),
      Wrap(spacing: 18, children: [
        for (final e in events.entries)
          SizedBox(
            width: 280,
            child: cardCheck(tv(c, 'notify.event', e.key), e.value == true, (v) => _event('${e.key}', v)),
          ),
      ]),
      const SizedBox(height: 14),
      Text(c.t('notify.history'), style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 8),
      if (history.isEmpty) cardHint(context, c.t('notify.no_history')),
      for (final h in history)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Tooltip(
            message: '${h['message'] ?? ''}',
            child: Wrap(spacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
              Tag(tv(c, 'level', h['level']), _level(p, switch (h['level']) { 'warning' => 'warn', 'critical' => 'crit', _ => '' })),
              Text('${h['title'] ?? ''}', style: const TextStyle(fontWeight: FontWeight.w600)),
              if (h['time'] is num)
                Text(fmt.format(DateTime.fromMillisecondsSinceEpoch(((h['time'] as num) * 1000).round())), style: TextStyle(color: p.muted, fontSize: 12.5)),
              if (historyFailures(h, c.t).isNotEmpty) Text(historyFailures(h, c.t), style: TextStyle(color: p.danger, fontSize: 12.5)),
            ]),
          ),
        ),
    ]);
  }
}

// --- InfluxDB -------------------------------------------------------------------

class _InfluxCard extends StatefulWidget {
  const _InfluxCard(this.c);

  final Controller c;

  @override
  State<_InfluxCard> createState() => _InfluxCardState();
}

class _InfluxCardState extends State<_InfluxCard> with AreaCalls, CardLoad {
  bool enabled = false;
  int version = 2;
  final _url = TextEditingController();
  final _org = TextEditingController();
  final _bucket = TextEditingController();
  final _token = TextEditingController();
  final _database = TextEditingController();
  final _username = TextEditingController();
  final _password = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/history/influx';

  Map get settings => d?['settings'] is Map ? d!['settings'] as Map : const {};

  @override
  void fill(Map data) {
    final s = data['settings'] is Map ? data['settings'] as Map : const {};
    enabled = s['enabled'] == true;
    version = s['version'] == 1 ? 1 : 2;
    _url.text = '${s['url'] ?? ''}';
    _org.text = '${s['org'] ?? ''}';
    _bucket.text = '${s['bucket'] ?? ''}';
    _database.text = '${s['database'] ?? ''}';
    _username.text = '${s['username'] ?? ''}';
    _token.clear();
    _password.clear();
  }

  @override
  void dispose() {
    for (final t in [_url, _org, _bucket, _token, _database, _username, _password]) {
      t.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return SettingsCard(c.t('influx.title'), [Busy(c.t('common.loading'))]);
    final s = settings;
    final last = data['last'];
    final kept = c.t('notify.secret_kept');
    return SettingsCard(c.t('influx.title'), [
      cardHint(context, c.t('influx.hint')),
      if (s['enabled'] == true && last is Map) ...[
        _fact(
          context,
          c.t('influx.last'),
          Align(
            alignment: Alignment.centerLeft,
            child: Tag(
              c.t(last['ok'] == true ? 'influx.ok' : 'influx.failed', {
                'time': last['time'] is num ? DateFormat.yMd(c.settings.lang).add_Hm().format(DateTime.fromMillisecondsSinceEpoch(((last['time'] as num) * 1000).round())) : '',
              }),
              last['ok'] == true ? p.ok : p.danger,
            ),
          ),
        ),
        if (last['ok'] != true) _fact(context, c.t('influx.error'), SelectableText('${last['error'] ?? ''}')),
        const SizedBox(height: 6),
      ],
      cardCheck(c.t('influx.enabled'), enabled, (v) => setState(() => enabled = v)),
      const SizedBox(height: 6),
      if (enabled) ...[
        cardSelect(c.t('influx.version'), version, [(2, c.t('influx.v2')), (1, c.t('influx.v1'))], (v) => setState(() => version = v)),
        cardField(_url, c.t('influx.url'), hint: 'http://192.168.1.10:8086'),
        if (version == 2) ...[
          cardField(_org, c.t('influx.org')),
          cardField(_bucket, c.t('influx.bucket')),
          cardField(_token, c.t('influx.token'), obscure: true, hint: s['has_token'] == true ? kept : null),
        ] else ...[
          cardField(_database, c.t('influx.database')),
          cardField(_username, c.t('influx.username')),
          cardField(_password, c.t('influx.password'), obscure: true, hint: s['has_password'] == true ? kept : null),
        ],
      ],
      FilledButton(
        onPressed: busy
            ? null
            : () => save(
                  () => _post(influxBody(
                    enabled: enabled,
                    version: version,
                    url: _url.text,
                    org: _org.text,
                    bucket: _bucket.text,
                    database: _database.text,
                    username: _username.text,
                    token: _token.text,
                    password: _password.text,
                  )),
                  enabled ? 'influx.saved' : 'influx.off',
                ),
        child: Text(c.t('common.save')),
      ),
    ]);
  }

  /// Saving sends a test value first, which can take a moment.
  Future<Object?> _post(Map<String, Object> body) => s.post('/api/history/influx', body, const Duration(minutes: 1));
}

// --- UPS ------------------------------------------------------------------------

class _UpsCard extends StatefulWidget {
  const _UpsCard(this.c);

  final Controller c;

  @override
  State<_UpsCard> createState() => _UpsCardState();
}

class _UpsCardState extends State<_UpsCard> with AreaCalls, CardLoad {
  String mode = 'off';
  int shutdown = 0;
  final _host = TextEditingController();
  final _port = TextEditingController();
  final _name = TextEditingController();
  final _user = TextEditingController();
  final _password = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/ups';

  @override
  void fill(Map data) {
    mode = const ['off', 'usb', 'remote'].contains(data['mode']) ? '${data['mode']}' : 'off';
    final m = data['shutdown_minutes'] is num ? (data['shutdown_minutes'] as num).toInt() : 0;
    shutdown = upsShutdown.contains(m) ? m : 0;
    _host.text = '${data['host'] ?? ''}';
    _port.text = '${data['port'] ?? ''}';
    _name.text = '${data['name'] ?? ''}';
    _user.text = '${data['user'] ?? ''}';
    _password.clear();
  }

  @override
  void dispose() {
    for (final t in [_host, _port, _name, _user, _password]) {
      t.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return SettingsCard(c.t('ups.title'), [Busy(c.t('common.loading'))]);
    return SettingsCard(c.t('ups.title'), [
      for (final (key, value, level) in upsFacts(data['status'], c.t))
        _fact(context, c.t(key), level.isEmpty ? SelectableText(value) : Align(alignment: Alignment.centerLeft, child: Tag(value, _level(p, level)))),
      const SizedBox(height: 8),
      cardSelect(c.t('ups.mode'), mode, [for (final m in ['off', 'usb', 'remote']) (m, c.t('ups.mode_$m'))], (v) => setState(() => mode = v)),
      if (mode == 'remote') ...[
        cardField(_host, c.t('ups.host')),
        cardField(_port, c.t('ups.port'), formatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(5)]),
        cardField(_name, c.t('ups.name')),
        cardField(_user, c.t('ups.user')),
        cardField(_password, c.t('ups.password'), obscure: true, hint: data['password_set'] == true ? c.t('notify.secret_kept') : null),
      ],
      cardSelect<int>(c.t('ups.shutdown'), shutdown, [for (final m in upsShutdown) (m, upsShutdownLabel(m, c.t))], mode == 'off' ? null : (v) => setState(() => shutdown = v)),
      cardHint(context, c.t('ups.hint')),
      FilledButton(
        onPressed: busy
            ? null
            : () => save(
                  () => s.post(
                    '/api/ups',
                    upsBody(mode: mode, name: _name.text, shutdown: shutdown, host: _host.text, port: _port.text, user: _user.text, password: _password.text),
                    const Duration(minutes: 1),
                  ),
                  'ups.saved',
                ),
        child: Text(c.t('common.save')),
      ),
    ]);
  }
}

// --- phone and other desktop apps -----------------------------------------------

class PhoneCard extends StatefulWidget {
  const PhoneCard(this.c, {super.key});

  final Controller c;

  @override
  State<PhoneCard> createState() => _PhoneCardState();
}

class _PhoneCardState extends State<PhoneCard> with AreaCalls, CardLoad {
  String? code;
  DateTime? until;
  Timer? _tick;

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/pairing';

  @override
  void fill(Map data) {}

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _newCode() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/pairing/code'), m);
    if (mounted) setState(() => busy = false);
    if (r is! Map) return;
    final seconds = r['expires_in'] is num ? (r['expires_in'] as num).toInt() : 0;
    setState(() {
      code = '${r['code'] ?? ''}';
      until = DateTime.now().add(Duration(seconds: seconds));
    });
    _tick?.cancel();
    _tick = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) return t.cancel();
      setState(() {});
      if (codeLeft(until!, DateTime.now()) == null) t.cancel();
    });
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return SettingsCard(c.t('phone.open'), [Busy(c.t('common.loading'))]);
    final qr = '${data['qr'] ?? ''}';
    final url = '${data['url'] ?? ''}'.isNotEmpty ? '${data['url']}' : 'https://${c.server?.host ?? ''}';
    final left = until == null ? null : codeLeft(until!, DateTime.now());
    return SettingsCard(c.t('phone.title'), [
      Wrap(spacing: 22, runSpacing: 14, children: [
        if (qr.isNotEmpty)
          Container(
            width: 190,
            height: 190,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12)),
            child: SvgPicture.string(qr, fit: BoxFit.contain),
          ),
        SizedBox(
          width: 520,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            cardHint(context, c.t('phone.scan')),
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: SelectableText(url, style: const TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback)),
            ),
            cardHint(context, c.t('phone.bookmark')),
            cardHint(context, c.t('phone.certificate')),
          ]),
        ),
      ]),
      const SizedBox(height: 18),
      Text(c.t('phone.desktop_title'), style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 6),
      cardHint(context, c.t('phone.desktop_hint')),
      if (code != null)
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Wrap(spacing: 14, crossAxisAlignment: WrapCrossAlignment.center, children: [
            SelectableText(code!, style: TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 26, fontWeight: FontWeight.w700, color: left == null ? p.muted : null)),
            Text(left == null ? c.t('phone.code_expired') : c.t('phone.code_valid', {'min': left.split(':').first, 'sec': left.split(':').last}), style: TextStyle(color: p.muted)),
          ]),
        ),
      OutlinedButton(onPressed: busy ? null : _newCode, child: Text(c.t('phone.code_new'))),
    ]);
  }
}
