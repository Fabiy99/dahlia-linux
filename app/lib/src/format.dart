// Numbers, sizes, times and names as the dashboard shows them.

import 'package:intl/intl.dart';

typedef Translate = String Function(String key, [Map<String, Object?> vars]);

/// Account names are stored in lower case; shown with a capital first letter.
String displayName(String name) => name.isEmpty ? name : name[0].toUpperCase() + name.substring(1);

/// 1536 -> "1.5 KB" (base 1024, like the dashboard).
String bytes(num n, Translate t) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  var v = n.toDouble();
  var i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  final s = i == 0 || v >= 100 ? v.round().toString() : v.toStringAsFixed(1);
  return '$s ${t('unit.${units[i]}')}';
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
