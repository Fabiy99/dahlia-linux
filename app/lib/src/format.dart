// Numbers, sizes, times and names as the dashboard shows them.

import 'package:intl/intl.dart';

typedef Translate = String Function(String key, [Map<String, Object?> vars]);

/// Account names are stored in lower case; shown with a capital first letter.
String displayName(String name) => name.isEmpty ? name : name[0].toUpperCase() + name.substring(1);

/// 1500 -> "1.5 KB": base 1000 with one decimal, like formatBytes() in the dashboard.
String bytes(num n, Translate t) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  var v = n.toDouble();
  var i = 0;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i++;
  }
  return '${v.toStringAsFixed(i == 0 ? 0 : 1)} ${t('unit.${units[i]}')}';
}

/// "2.4 MB/s"
String rate(num bytesPerSecond, Translate t) => '${bytes(bytesPerSecond, t)}/s';

/// "3 Tage, 4 Std., 1 Min." (overview.uptime_value of the dashboard).
String uptime(int seconds, Translate t) =>
    t('overview.uptime_value', {'d': seconds ~/ 86400, 'h': seconds % 86400 ~/ 3600, 'm': seconds % 3600 ~/ 60});

/// "47 °C"
String temp(num c) => '${c.round()} °C';

/// Disk temperatures like a NAS shows them: HDDs warm at 45, SSDs at 60 (tempKind()).
String tempKind(num? c, bool rotational) {
  if (c == null) return '';
  final warn = rotational ? 45 : 60;
  if (c >= warn + 10) return 'crit';
  if (c >= warn) return 'warn';
  return 'ok';
}

/// Uptime: "3 d", "5 h", "12 min".
String duration(int seconds, Translate t) {
  if (seconds >= 86400) return t('time.days', {'n': seconds ~/ 86400});
  if (seconds >= 3600) return t('time.hours', {'n': seconds ~/ 3600});
  return t('time.minutes', {'n': seconds ~/ 60});
}

/// "Thursday, 12 November · 22:40" in the app's language.
String longDate(DateTime d, String lang) {
  final day = DateFormat(lang == 'de' ? 'EEEE, d. MMMM' : 'EEEE, d MMMM', lang).format(d);
  return '$day · ${DateFormat.Hm(lang).format(d)}';
}
