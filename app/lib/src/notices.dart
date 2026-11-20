// Notifications of the server (disk problems, failed jobs, backup probe ...)
// as the app shows them: in the bell list of the top bar and as messages of
// the operating system. The server keeps the last 100 (notify.py).

class Notice {
  const Notice(this.time, this.level, this.title, this.message);

  /// Unix time in seconds.
  final int time;

  /// info, warning or critical.
  final String level;
  final String title, message;

  static Notice? fromJson(Object? j) {
    if (j is! Map || j['time'] is! int) return null;
    String s(String k) => j[k] is String ? j[k] as String : '';
    final level = s('level');
    return Notice(
      j['time'] as int,
      const ['info', 'warning', 'critical'].contains(level) ? level : 'info',
      s('title'),
      s('message'),
    );
  }

  DateTime get when => DateTime.fromMillisecondsSinceEpoch(time * 1000);
}

/// Notices of a /api/notifications answer, newest first.
List<Notice> parseNotices(Object? j) {
  final items = j is Map && j['items'] is List ? j['items'] as List : const [];
  final out = [for (final i in items) Notice.fromJson(i)].whereType<Notice>().toList();
  out.sort((a, b) => b.time.compareTo(a.time));
  return out;
}

/// Notices newer than [seen], oldest first, at most [max]: after a long
/// break the desktop shows the last few, not a flood.
List<Notice> freshNotices(List<Notice> all, int seen, {int max = 3}) {
  final fresh = all.where((n) => n.time > seen).toList()..sort((a, b) => a.time.compareTo(b.time));
  return fresh.length > max ? fresh.sublist(fresh.length - max) : fresh;
}

/// Newest time in [all], or [fallback].
int newestTime(List<Notice> all, int fallback) =>
    all.isEmpty ? fallback : all.map((n) => n.time).reduce((a, b) => a > b ? a : b);
