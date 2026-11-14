// Jobs and system log areas: the background jobs with state, last result and
// starting them by hand; the system journal with filters and the diagnostics
// bundle for bug reports. Same API and rules as the dashboard's pages.

import 'dart:async';
import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/jobs.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

Color _level(Palette p, String level) => switch (level) {
  'ok' => p.ok,
  'warn' => p.warn,
  'crit' => p.danger,
  'info' => p.accent,
  _ => p.muted,
};

class JobsPage extends StatefulWidget {
  const JobsPage(this.c, {super.key});

  final Controller c;

  @override
  State<JobsPage> createState() => _JobsPageState();
}

class _JobsPageState extends State<JobsPage> with AreaCalls {
  List<(String, Map)>? jobs;
  bool busy = false;
  Timer? _timer;

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    _timer?.cancel();
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.get('/api/jobs'), m);
    if (r == null || !mounted) return;
    final list = jobList(r);
    setState(() => jobs = list);
    // A started job can run for hours; keep its state live while it does.
    if (list.any((e) => jobActive(e.$2))) _timer = Timer(const Duration(seconds: 5), _load);
  }

  Future<void> _start(String name) async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/jobs/$name/start'), m);
    if (r != null) say(m, c.t('jobs.started', {'job': c.t('job.$name')}));
    if (mounted) setState(() => busy = false);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final list = jobs;
    final head = TextStyle(color: p.muted, fontSize: 12, fontWeight: FontWeight.w600);
    return PageFrame(
      c: c,
      children: [
        const SizedBox(height: 40),
        Rise(child: AreaTitle(c.t('jobs.title'))),
        const SizedBox(height: 28),
        if (list == null)
          Busy(c.t('common.loading'))
        else
          SizedBox(
            width: double.infinity,
            child: Glass(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: DataTable(
                      headingRowHeight: 36,
                      dataRowMinHeight: 48,
                      horizontalMargin: 0,
                      columnSpacing: 28,
                      columns: [
                        for (final k in ['job', 'state', 'result', 'last_run']) DataColumn(label: Text(c.t('jobs.$k'), style: head)),
                        const DataColumn(label: SizedBox()),
                      ],
                      rows: [
                        for (final (name, st) in list)
                          DataRow(
                            cells: [
                              DataCell(Text(tv(c, 'job', name), style: const TextStyle(fontWeight: FontWeight.w600))),
                              DataCell(Tag(tv(c, 'job_state', st['active']), jobActive(st) ? p.accent : (st['active'] == 'failed' ? p.danger : p.muted))),
                              DataCell(
                                '${st['result'] ?? ''}'.isEmpty
                                    ? Tag(c.t('jobs.never'), p.muted)
                                    : Tag(tv(c, 'job_result', st['result']), _level(p, jobResultLevel(st['result']))),
                              ),
                              DataCell(Text('${st['started'] ?? ''}'.isEmpty ? '-' : '${st['started']}')),
                              DataCell(
                                startableJobs.contains(name)
                                    ? IconButton(
                                        tooltip: c.t('jobs.start'),
                                        onPressed: !busy && canStartJob(name, st) ? () => _start(name) : null,
                                        icon: const Icon(Icons.play_arrow_rounded),
                                      )
                                    : const SizedBox(),
                              ),
                            ],
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 14),
                  OutlinedButton(onPressed: busy ? null : _load, child: Text(c.t('common.refresh'))),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

class LogPage extends StatefulWidget {
  const LogPage(this.c, {super.key});

  final Controller c;

  @override
  State<LogPage> createState() => _LogPageState();
}

class _LogPageState extends State<LogPage> with AreaCalls {
  List<String>? lines;
  bool loading = false;
  String priority = 'info';
  int count = 300;
  final _unit = TextEditingController();
  final _scroll = ScrollController();

  /// Text below the diagnostics button while it is made.
  String diag = '';
  bool diagBusy = false;

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _unit.dispose();
    _scroll.dispose();
    super.dispose();
  }

  String? get _query => logQuery(lines: count, priority: priority, unit: _unit.text);

  Future<void> _load() async {
    final q = _query;
    if (q == null) return;
    final m = ScaffoldMessenger.of(context);
    setState(() => loading = true);
    final r = await call(() => s.get('/api/log?$q'), m);
    if (!mounted) return;
    setState(() {
      loading = false;
      if (r != null) lines = r is Map && r['lines'] is List ? [for (final l in r['lines'] as List) '$l'] : <String>[];
    });
    // The newest lines are at the bottom.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
    });
  }

  void _diagDone() {
    diagBusy = false;
    diag = '';
  }

  Future<void> _diagnostics() async {
    final m = ScaffoldMessenger.of(context);
    setState(() {
      diagBusy = true;
      diag = c.t('diag.working');
    });
    final r = await call(() => s.post('/api/diagnostics', null, const Duration(minutes: 3)), m);
    if (r is! Map || !mounted) {
      if (mounted) setState(_diagDone);
      return;
    }
    setState(() => diag = c.t('diag.ready', {'size': bytes((r['size'] as num?) ?? 0, c.t)}));
    final where = await getSaveLocation(suggestedName: diagnosticsName(DateTime.now()));
    if (where == null || !mounted) {
      if (mounted) setState(_diagDone);
      return;
    }
    final ok = await call(() async {
      await s.download('/api/diagnostics', File(where.path));
      return true;
    }, m);
    if (!mounted) return;
    setState(_diagDone);
    if (ok == true) say(m, c.t('fm.downloaded', {'path': where.path}));
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final list = lines;
    final size = MediaQuery.of(context).size;
    const mono = TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12, height: 1.4);
    return PageFrame(
      c: c,
      children: [
        const SizedBox(height: 40),
        Rise(child: AreaTitle(c.t('nav.log'))),
        const SizedBox(height: 28),
        Glass(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 12,
                runSpacing: 12,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  SizedBox(
                    width: 220,
                    child: DropdownButtonFormField<String>(
                      initialValue: priority,
                      isExpanded: true,
                      decoration: InputDecoration(labelText: c.t('log.priority')),
                      borderRadius: BorderRadius.circular(12),
                      items: [for (final v in logPriorities) DropdownMenuItem(value: v, child: Text(c.t('log.p_$v')))],
                      onChanged: (v) => setState(() => priority = v ?? priority),
                    ),
                  ),
                  SizedBox(
                    width: 280,
                    child: TextField(
                      controller: _unit,
                      autocorrect: false,
                      inputFormatters: [LengthLimitingTextInputFormatter(128)],
                      decoration: InputDecoration(
                        labelText: c.t('log.unit'),
                        hintText: c.t('log.unit_hint'),
                        errorText: _query == null ? c.t('log.unit_invalid') : null,
                      ),
                      onChanged: (_) => setState(() {}),
                      onSubmitted: (_) => _load(),
                    ),
                  ),
                  SizedBox(
                    width: 130,
                    child: DropdownButtonFormField<int>(
                      initialValue: count,
                      decoration: InputDecoration(labelText: c.t('log.lines')),
                      borderRadius: BorderRadius.circular(12),
                      items: [for (final n in logLineChoices) DropdownMenuItem(value: n, child: Text('$n'))],
                      onChanged: (v) => setState(() => count = v ?? count),
                    ),
                  ),
                  FilledButton(onPressed: !loading && _query != null ? _load : null, child: Text(c.t('common.refresh'))),
                ],
              ),
              const SizedBox(height: 16),
              Container(
                width: double.infinity,
                height: (size.height - 420).clamp(260, 900),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
                child: list == null
                    ? Busy(c.t('common.loading'))
                    : list.isEmpty
                    ? Text(c.t('log.empty'), style: TextStyle(color: p.muted))
                    : SingleChildScrollView(
                        controller: _scroll,
                        child: SizedBox(
                          width: double.infinity,
                          child: SelectableText.rich(
                            TextSpan(
                              style: mono,
                              children: [
                                for (final l in list)
                                  TextSpan(
                                    text: '$l\n',
                                    style: switch (lineLevel(l)) {
                                      'crit' => TextStyle(color: p.danger),
                                      'warn' => TextStyle(color: p.warn),
                                      _ => null,
                                    },
                                  ),
                              ],
                            ),
                          ),
                        ),
                      ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 18),
        Glass(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(c.t('diag.title'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 10),
              Text(c.t('diag.hint'), style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
              const SizedBox(height: 14),
              Wrap(
                spacing: 14,
                runSpacing: 10,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  FilledButton(onPressed: diagBusy ? null : _diagnostics, child: Text(c.t('diag.create'))),
                  if (diag.isNotEmpty) Text(diag, style: TextStyle(color: p.muted, fontSize: 13)),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}
