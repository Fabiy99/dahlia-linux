// Docker area: space used by images, containers, volumes and build cache,
// every container with state, ports and live CPU/RAM, start, stop, restart,
// its logs and cleaning up unused images. Same API as the dashboard's page.

import 'dart:async';

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/docker.dart';
import '../src/format.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class DockerPage extends StatefulWidget {
  const DockerPage(this.c, {super.key});

  final Controller c;

  @override
  State<DockerPage> createState() => _DockerPageState();
}

class _DockerPageState extends State<DockerPage> with AreaCalls {
  List<Map>? containers;
  List<Map> usage = const [];
  Map<String, Map> stats = const {};
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
    final r = await call(() => Future.wait([s.get('/api/docker'), s.get('/api/docker/disk')]), m);
    if (r == null || !mounted) return;
    final [list, disk] = r;
    setState(() {
      containers = list is Map && list['containers'] is List ? (list['containers'] as List).whereType<Map>().toList() : <Map>[];
      usage = disk is Map && disk['usage'] is List ? (disk['usage'] as List).whereType<Map>().toList() : <Map>[];
    });
    await _stats();
    // CPU and RAM every 5 seconds, as in the dashboard.
    if (mounted) _timer = Timer.periodic(const Duration(seconds: 5), (_) => _stats());
  }

  Future<void> _stats() async {
    final r = await call(() => s.get('/api/docker/stats'));
    if (r == null || !mounted) return;
    setState(() => stats = statsOf(r));
  }

  Future<void> _op(Map ct, String op) async {
    final m = ScaffoldMessenger.of(context);
    final name = '${ct['name']}';
    if (op != 'start' && !await ask(c.t('docker.confirm_$op', {'name': name}), 'docker.$op', danger: op == 'stop')) return;
    setState(() => busy = true);
    final r = await call(() => s.post(containerPath(name, op)), m);
    if (r != null) say(m, c.t('docker.${op}_done', {'name': name}));
    if (mounted) setState(() => busy = false);
    await _load();
  }

  Future<void> _prune() async {
    final m = ScaffoldMessenger.of(context);
    if (!await ask(c.t('docker.confirm_prune'), 'docker.prune', danger: true)) return;
    setState(() => busy = true);
    final r = await call(() => s.post('/api/docker/prune'), m);
    if (r is Map) say(m, c.t('docker.pruned', {'size': bytes((r['reclaimed'] as num?) ?? 0, c.t)}));
    if (mounted) setState(() => busy = false);
    await _load();
  }

  Future<void> _logs(String name) => showDialog<void>(context: context, builder: (_) => _LogsDialog(c: c, name: name));

  Color _level(Palette p, String level) => switch (level) {
        'ok' => p.ok,
        'warn' => p.warn,
        'crit' => p.danger,
        _ => p.muted,
      };

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final list = containers;
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final tiles = grid.cols == 1 ? 1 : 4;
    final tile = (full - 18 * (tiles - 1)) / tiles;
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('nav.docker'), hint: c.t('docker.hint'))),
      const SizedBox(height: 28),
      if (list == null)
        Busy(c.t('common.loading'))
      else ...[
        if (usage.isNotEmpty)
          Wrap(spacing: 18, runSpacing: 18, children: [
            for (final u in usage) SizedBox(width: tile, child: _usageTile(p, u)),
          ]),
        const SizedBox(height: 18),
        _containersCard(p, list),
      ],
    ]);
  }

  Widget _usageTile(Palette p, Map u) => Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(tv(c, 'docker_type', u['type']), style: TextStyle(color: p.muted, fontSize: 13)),
          const SizedBox(height: 6),
          Text(bytes((u['size'] as num?) ?? 0, c.t), style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
          const SizedBox(height: 4),
          Text(usageDetail(u, c.t), style: TextStyle(color: p.muted, fontSize: 12.5)),
        ]),
      );

  Widget _containersCard(Palette p, List<Map> list) {
    final head = TextStyle(color: p.muted, fontSize: 12, fontWeight: FontWeight.w600);
    const mono = TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12.5);
    return Glass(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(spacing: 10, runSpacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text(c.t('docker.containers'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(width: 8),
          OutlinedButton(onPressed: busy ? null : _load, child: Text(c.t('common.refresh'))),
          OutlinedButton(
            style: OutlinedButton.styleFrom(foregroundColor: p.danger),
            onPressed: busy ? null : _prune,
            child: Text(c.t('docker.prune')),
          ),
        ]),
        const SizedBox(height: 14),
        if (list.isEmpty)
          Text(c.t('docker.none'), style: TextStyle(color: p.muted))
        else
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              headingRowHeight: 36,
              dataRowMinHeight: 48,
              dataRowMaxHeight: 72,
              horizontalMargin: 0,
              columnSpacing: 24,
              columns: [
                for (final k in ['name', 'image', 'state', 'ports', 'cpu', 'memory']) DataColumn(label: Text(c.t('docker.$k'), style: head)),
                const DataColumn(label: SizedBox()),
              ],
              rows: [
                for (final ct in list)
                  DataRow(cells: [
                    DataCell(Row(mainAxisSize: MainAxisSize.min, children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(color: _level(p, containerLevel(ct['state'])), shape: BoxShape.circle),
                      ),
                      const SizedBox(width: 10),
                      Text('${ct['name']}', style: const TextStyle(fontWeight: FontWeight.w600)),
                      if (ct['app'] != null) ...[
                        const SizedBox(width: 8),
                        Tag(c.t('docker.app', {'app': ct['app']}), p.accent),
                      ],
                    ])),
                    DataCell(ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 300),
                      child: Text('${ct['image'] ?? ''}', style: mono, overflow: TextOverflow.ellipsis),
                    )),
                    DataCell(Tag(tv(c, 'container_state', ct['state']), _level(p, containerLevel(ct['state'])), tooltip: '${ct['status'] ?? ''}')),
                    DataCell(Text(portsText(ct['ports']), style: mono.copyWith(fontSize: 12, color: p.muted))),
                    DataCell(Text(cpuText(stats['${ct['name']}']))),
                    DataCell(Text(memoryText(stats['${ct['name']}'], c.t))),
                    DataCell(Row(mainAxisSize: MainAxisSize.min, children: [
                      for (final op in containerOps(ct['state']))
                        IconButton(
                          tooltip: c.t('docker.$op'),
                          color: op == 'stop' ? p.danger : null,
                          onPressed: busy ? null : () => _op(ct, op),
                          icon: Icon(switch (op) {
                            'restart' => Icons.restart_alt_rounded,
                            'stop' => Icons.stop_rounded,
                            _ => Icons.play_arrow_rounded,
                          }),
                        ),
                      IconButton(
                        tooltip: c.t('docker.logs'),
                        onPressed: () => _logs('${ct['name']}'),
                        icon: const Icon(Icons.article_outlined),
                      ),
                    ])),
                  ]),
              ],
            ),
          ),
      ]),
    );
  }
}

/// The last 500 lines of a container, refreshed on request.
class _LogsDialog extends StatefulWidget {
  const _LogsDialog({required this.c, required this.name});

  final Controller c;
  final String name;

  @override
  State<_LogsDialog> createState() => _LogsDialogState();
}

class _LogsDialogState extends State<_LogsDialog> with AreaCalls {
  String? text;
  final _scroll = ScrollController();

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => text = null);
    final r = await call(() => s.get(logsPath(widget.name)), m);
    if (!mounted) return;
    final lines = r is Map && r['lines'] is List ? [for (final l in r['lines'] as List) '$l'] : <String>[];
    setState(() => text = lines.isEmpty ? c.t('log.empty') : lines.join('\n'));
    // Newest lines are at the bottom.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
    });
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final size = MediaQuery.of(context).size;
    return AlertDialog(
      title: Text(c.t('docker.logs_of', {'name': widget.name})),
      content: SizedBox(
        width: (size.width - 120).clamp(320, 1100),
        height: (size.height - 260).clamp(240, 700),
        child: Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
          child: text == null
              ? Busy(c.t('common.loading'))
              : SingleChildScrollView(
                  controller: _scroll,
                  child: SizedBox(
                    width: double.infinity,
                    child: SelectableText(text!, style: const TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12, height: 1.4)),
                  ),
                ),
        ),
      ),
      actions: [
        TextButton(onPressed: text == null ? null : _refresh, child: Text(c.t('common.refresh'))),
        FilledButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.close'))),
      ],
    );
  }
}
