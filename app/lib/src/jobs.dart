// Jobs and system log without widgets: job states and what may be started,
// log query and line levels, the same rules as the dashboard's pages.

/// Jobs that may be started by hand (the system update runs from its own page).
const startableJobs = ['mover', 'snapraid-sync', 'snapraid-scrub', 'backup', 'smart-short', 'smart-long'];

bool jobActive(Object? s) => s is Map && const ['active', 'activating'].contains(s['active']);

/// Level of a job's last result for its label ('ok', 'warn', 'crit', '').
String jobResultLevel(Object? result) => switch (result) {
      'success' => 'ok',
      'signal' => 'warn',
      'exit-code' || 'timeout' => 'crit',
      _ => '',
    };

bool canStartJob(String name, Object? s) => startableJobs.contains(name) && !jobActive(s);

/// Jobs in the server's order; entries that are no map are left out.
List<(String, Map)> jobList(Object? response) => [
      if (response is Map)
        for (final e in response.entries)
          if (e.value is Map) ('${e.key}', e.value as Map),
    ];

const logPriorities = ['err', 'warning', 'info', 'debug'];
const logLineChoices = [100, 300, 1000, 5000];

final unitRe = RegExp(r'^[a-zA-Z0-9][a-zA-Z0-9@._:-]{0,127}$');

/// Query of `GET /api/log`; null while the service name is not valid.
String? logQuery({required int lines, required String priority, required String unit}) {
  final u = unit.trim();
  if (u.isNotEmpty && !unitRe.hasMatch(u)) return null;
  return Uri(queryParameters: {'lines': '$lines', 'priority': priority, if (u.isNotEmpty) 'unit': u}).query;
}

final _err = RegExp(r'\b(error|failed|fail|critical|panic)\b', caseSensitive: false);
final _warn = RegExp(r'\bwarn(ing)?\b', caseSensitive: false);

/// Colour of a journal line: 'crit' for errors, 'warn' for warnings, '' else.
String lineLevel(String line) => _err.hasMatch(line) ? 'crit' : (_warn.hasMatch(line) ? 'warn' : '');

/// File name offered when saving the diagnostics bundle.
String diagnosticsName(DateTime now) {
  String two(int n) => n.toString().padLeft(2, '0');
  return 'diagnostics-${now.year}${two(now.month)}${two(now.day)}-${two(now.hour)}${two(now.minute)}.tar.gz';
}
