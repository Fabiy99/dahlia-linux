// Weekly letter and year review (letters.py on the server) as sentences,
// the same as letterLines() in the dashboard's app.js.

import 'package:intl/intl.dart';

import 'format.dart';

/// One sentence of a letter: an icon name ([letterIcons] in the UI) and the text.
typedef LetterLine = (String, String);

num? _num(Object? v) => v is num ? v : null;

List<LetterLine> letterLines(Map l, Translate t) {
  final out = <LetterLine>[];
  final year = l['kind'] == 'year';
  final st = l['storage'];
  if (st is Map && st['used'] is num && st['size'] is num) {
    final used = bytes(st['used'] as num, t), size = bytes(st['size'] as num, t);
    final added = _num(st['added']);
    out.add((
      'storage',
      added == null
          ? t('letter.storage', {'used': used, 'size': size})
          : t(added >= 0 ? 'letter.storage_added' : 'letter.storage_freed', {
              'added': bytes(added.abs(), t),
              'used': used,
              'size': size,
            }),
    ));
  }
  final b = l['backups'] is Map ? l['backups'] as Map : const {};
  if ((_num(b['runs']) ?? 0) > 0) {
    var text = t('letter.backups', {'ok': b['ok'], 'runs': b['runs']});
    if (b['probe'] != null) text += ' ${t(b['probe'] == 'success' ? 'letter.probe_ok' : 'letter.probe_failed')}';
    out.add(('backup', text));
  } else if (b['runs'] == 0) {
    out.add(('backup', t('letter.no_backups')));
  }
  if ((_num(l['updates']) ?? 0) > 0) out.add(('updates', t('letter.updates', {'count': l['updates']})));
  final a = l['apps'] is Map ? l['apps'] as Map : null;
  if (a != null && a['count'] != null) {
    var text = t('letter.apps', {'count': a['count']});
    final added = a['new'] is List ? (a['new'] as List).join(', ') : '';
    final updated = a['updated'] is List ? (a['updated'] as List).join(', ') : '';
    if (added.isNotEmpty) text += ' ${t('letter.apps_new', {'names': added})}';
    if (updated.isNotEmpty) text += ' ${t('letter.apps_updated', {'names': updated})}';
    out.add(('apps', text));
  }
  if ((_num(l['photos']) ?? 0) > 0) out.add(('photos', t('letter.photos', {'count': l['photos']})));
  final tr = l['traffic'];
  if (tr is Map && tr['rx'] is num && tr['tx'] is num) {
    out.add(('network', t('letter.traffic', {'rx': bytes(tr['rx'] as num, t), 'tx': bytes(tr['tx'] as num, t)})));
  }
  final cpu = _num(l['cpu_avg']), hot = _num(l['temp_max']);
  if (cpu != null) {
    out.add((
      'cpu',
      hot == null
          ? t('letter.cpu', {'cpu': cpu.round()})
          : t('letter.cpu_temp', {'cpu': cpu.round(), 'temp': hot.round()}),
    ));
  }
  final up = _num(year ? l['uptime_max'] : l['uptime']) ?? 0;
  if (up > 86400) out.add(('uptime', t(year ? 'letter.uptime_max' : 'letter.uptime', {'days': up ~/ 86400})));
  final n = l['notices'] is Map ? l['notices'] as Map : const {};
  final warn = _num(n['warning']) ?? 0, crit = _num(n['critical']) ?? 0;
  if (warn > 0 || crit > 0) {
    var text = t('letter.notices', {'warning': warn, 'critical': crit});
    final titles = n['titles'] is List ? (n['titles'] as List).join(', ') : '';
    if (titles.isNotEmpty) text += ': $titles';
    out.add(('notices', text));
  } else {
    out.add(('notices', t(year ? 'letter.quiet_year' : 'letter.quiet')));
  }
  return out;
}

String letterTitle(Map l, Translate t) {
  if (l['kind'] == 'year') return t(l['preview'] == true ? 'letter.year_now' : 'letter.year', {'year': l['id']});
  return t(l['preview'] == true ? 'letter.week_now' : 'letter.week');
}

/// "9. November bis 15. November 2026".
String letterWhen(Map l, Translate t, String lang) {
  final from = l['from'], to = l['to'];
  if (from is! int || to is! int) return t('letter.no_weeks');
  final day = DateFormat.MMMMd(lang), full = DateFormat.yMMMMd(lang);
  return t('letter.range', {
    'from': day.format(DateTime.fromMillisecondsSinceEpoch(from * 1000)),
    'to': full.format(DateTime.fromMillisecondsSinceEpoch((to - 1) * 1000)),
  });
}

/// Name of a saved letter in the picker.
String letterName(Map l, Translate t, String lang) {
  if (l['kind'] == 'year') return t('letter.year', {'year': l['id']});
  final from = l['from'];
  final date = from is int
      ? DateFormat.yMMMd(lang).format(DateTime.fromMillisecondsSinceEpoch(from * 1000))
      : '${l['id']}';
  return t('letter.week_of', {'date': date});
}
