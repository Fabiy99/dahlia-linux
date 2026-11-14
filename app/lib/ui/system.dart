// System section of the settings: server name, time, network and its layout
// with Wake-on-LAN, disk spindown, CPU governor, remote syslog and the PCI
// and USB devices. Each card loads on its own, as on the dashboard's page.

import 'dart:async';

import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/server.dart';
import '../src/system.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class SystemSection extends StatefulWidget {
  const SystemSection(this.c, {super.key});

  final Controller c;

  @override
  State<SystemSection> createState() => _SystemSectionState();
}

class _SystemSectionState extends State<SystemSection> {
  late final _net = _Net(widget.c);

  @override
  void initState() {
    super.initState();
    _net.load();
  }

  @override
  void dispose() {
    _net.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final half = grid.cols == 1 ? full : (full - 18) / 2;
    return Wrap(spacing: 18, runSpacing: 18, children: [
      SizedBox(width: half, child: _ServerCard(c)),
      SizedBox(width: half, child: _TimeCard(c)),
      SizedBox(width: half, child: _NetworkCard(c, _net)),
      SizedBox(width: half, child: _SpindownCard(c)),
      SizedBox(width: full, child: _LayoutCard(c, _net)),
      SizedBox(width: half, child: _PowerCard(c)),
      SizedBox(width: half, child: _SyslogCard(c)),
      SizedBox(width: full, child: _DevicesCard(c)),
    ]);
  }
}

// --- pieces ---------------------------------------------------------------------

class _Card extends StatelessWidget {
  const _Card(this.title, this.children);

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 14),
          ...children,
        ]),
      );
}

Widget _hint(BuildContext context, String text) => Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Text(text, style: TextStyle(color: context.palette.muted, fontSize: 13, height: 1.4)),
    );

Widget _field(TextEditingController t, String label, {String? hint, List<TextInputFormatter>? formatters, ValueChanged<String>? onChanged}) => Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: t,
        autocorrect: false,
        inputFormatters: formatters ?? [LengthLimitingTextInputFormatter(255)],
        decoration: InputDecoration(labelText: label, hintText: hint),
        onChanged: onChanged,
      ),
    );

Widget _check(String label, bool value, ValueChanged<bool>? onChanged) => CheckboxListTile(
      value: value,
      dense: true,
      contentPadding: EdgeInsets.zero,
      controlAffinity: ListTileControlAffinity.leading,
      title: Text(label),
      onChanged: onChanged == null ? null : (v) => onChanged(v ?? false),
    );

Widget _select<T>(String label, T? value, List<(T, String)> items, ValueChanged<T>? onChanged) => Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: DropdownButtonFormField<T>(
        key: ValueKey('$label ${items.map((e) => e.$1).join(',')} $value'),
        initialValue: items.any((e) => e.$1 == value) ? value : null,
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        borderRadius: BorderRadius.circular(12),
        items: [for (final (v, text) in items) DropdownMenuItem(value: v, child: Text(text, overflow: TextOverflow.ellipsis))],
        onChanged: onChanged == null ? null : (v) => v == null ? null : onChanged(v),
      ),
    );

/// Shared by the cards: the first load, then reloads that keep the form.
mixin _CardLoad<T extends StatefulWidget> on State<T>, AreaCalls<T> {
  Map? d;
  bool busy = false;

  String get path;

  /// Takes the server's values into the form fields.
  void fill(Map data);

  @override
  void initState() {
    super.initState();
    load(fill: true);
  }

  Future<void> load({bool fill = false}) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.get(path), m);
    if (r is! Map || !mounted) return;
    setState(() {
      d = r;
      if (fill) this.fill(r);
    });
  }

  /// Runs a change with the save button disabled, then loads again.
  Future<void> save(Future<Object?> Function() f, String done) async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(f, m);
    if (r != null && done.isNotEmpty) say(m, c.t(done));
    if (mounted) setState(() => busy = false);
    if (r != null) await load(fill: true);
  }
}

// --- server name ----------------------------------------------------------------

class _ServerCard extends StatefulWidget {
  const _ServerCard(this.c);

  final Controller c;

  @override
  State<_ServerCard> createState() => _ServerCardState();
}

class _ServerCardState extends State<_ServerCard> with AreaCalls, _CardLoad {
  final _name = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/system';

  @override
  void fill(Map data) => _name.text = '${data['hostname'] ?? ''}';

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _rename() async {
    final m = ScaffoldMessenger.of(context);
    final old = '${d?['hostname'] ?? ''}';
    final name = _name.text.trim().toLowerCase();
    setState(() => busy = true);
    final r = await call(() => s.post('/api/system/hostname', {'hostname': name}, const Duration(minutes: 3)), m);
    if (mounted) setState(() => busy = false);
    if (r == null) return;
    say(m, c.t('overview.hostname_set', {'name': name}));
    await load(fill: true);
    await c.serverRenamed(old, name);
  }

  @override
  Widget build(BuildContext context) {
    if (d == null) return _Card(c.t('account.server_title'), [Busy(c.t('common.loading'))]);
    final name = _name.text.trim().toLowerCase();
    final ok = hostnameRe.hasMatch(name) && name != '${d!['hostname']}';
    return _Card(c.t('account.server_title'), [
      _field(_name, c.t('field.hostname'), formatters: [
          LengthLimitingTextInputFormatter(63),
          TextInputFormatter.withFunction((old, v) => v.copyWith(text: v.text.toLowerCase())),
        ], onChanged: (_) => setState(() {})),
      _hint(context, c.t('account.hostname_hint')),
      FilledButton(onPressed: busy || !ok ? null : _rename, child: Text(c.t('overview.set_hostname'))),
    ]);
  }
}

// --- time -----------------------------------------------------------------------

class _TimeCard extends StatefulWidget {
  const _TimeCard(this.c);

  final Controller c;

  @override
  State<_TimeCard> createState() => _TimeCardState();
}

class _TimeCardState extends State<_TimeCard> with AreaCalls, _CardLoad {
  String zone = '';
  bool ntp = true;

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/settings/time';

  @override
  void fill(Map data) {
    zone = '${data['timezone'] ?? ''}';
    ntp = data['ntp'] == true;
  }

  @override
  Widget build(BuildContext context) {
    final data = d;
    if (data == null) return _Card(c.t('settings.time_title'), [Busy(c.t('common.loading'))]);
    final zones = textList(data['timezones']);
    return _Card(c.t('settings.time_title'), [
      Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: DropdownMenu<String>(
          key: ValueKey('zone ${zones.length} $zone'),
          initialSelection: zones.contains(zone) ? zone : null,
          label: Text(c.t('settings.timezone')),
          expandedInsets: EdgeInsets.zero,
          enableFilter: true,
          requestFocusOnTap: true,
          menuHeight: 360,
          dropdownMenuEntries: [for (final z in zones) DropdownMenuEntry(value: z, label: z)],
          onSelected: (v) => setState(() => zone = v ?? zone),
        ),
      ),
      _check(c.t('settings.ntp'), ntp, (v) => setState(() => ntp = v)),
      _hint(context, timeState(data, c.t)),
      FilledButton(
        onPressed: busy || !zones.contains(zone)
            ? null
            : () => save(() => s.post('/api/settings/time', {'timezone': zone, 'ntp': ntp}), 'settings.time_saved'),
        child: Text(c.t('common.save')),
      ),
    ]);
  }
}

// --- network and layout -----------------------------------------------------------

/// Network state for both network cards. While a change waits for
/// confirmation (or the address moves) it is asked again every 3 seconds.
class _Net extends ChangeNotifier {
  _Net(this.c);

  final Controller c;
  Map? network;
  Map? layout;

  /// Set once the form fields have been filled from the server.
  int generation = 0;
  Timer? _timer;
  bool _disposed = false;

  Future<void> load({bool fill = true}) async {
    _timer?.cancel();
    final s = c.server;
    if (s == null) return;
    try {
      final [n, l] = await Future.wait([s.get('/api/settings/network'), s.get('/api/settings/network/layout')]);
      if (_disposed) return;
      if (n is Map) network = n;
      if (l is Map) layout = l;
      if (fill) generation++;
      if (n is Map && netPending(n) != null) poll();
    } on VersionMismatch {
      return;
    } on ApiError catch (e) {
      if (_disposed) return;
      if (e.status == 401) {
        await c.sessionEnded();
        return;
      }
      // While the address changes, requests fail; keep trying.
      poll();
    }
    if (!_disposed) notifyListeners();
  }

  void poll() {
    _timer?.cancel();
    _timer = Timer(const Duration(seconds: 3), () => load(fill: false));
  }

  @override
  void dispose() {
    _disposed = true;
    _timer?.cancel();
    super.dispose();
  }
}

class _NetworkCard extends StatefulWidget {
  const _NetworkCard(this.c, this.net);

  final Controller c;
  final _Net net;

  @override
  State<_NetworkCard> createState() => _NetworkCardState();
}

class _NetworkCardState extends State<_NetworkCard> with AreaCalls {
  String mode = 'dhcp';
  final _address = TextEditingController();
  final _gateway = TextEditingController();
  final _dns = TextEditingController();
  int _filled = -1;
  bool busy = false;

  @override
  Controller get c => widget.c;

  _Net get net => widget.net;

  @override
  void initState() {
    super.initState();
    net.addListener(_changed);
  }

  @override
  void dispose() {
    net.removeListener(_changed);
    for (final t in [_address, _gateway, _dns]) {
      t.dispose();
    }
    super.dispose();
  }

  void _changed() {
    if (mounted) setState(() {});
  }

  void _fill(Map d) {
    _filled = net.generation;
    mode = d['mode'] == 'static' ? 'static' : 'dhcp';
    // Prefilled with what DHCP gave, so switching to static starts from the current address.
    final current = textList(d['current']);
    _address.text = '${d['address'] ?? ''}'.isNotEmpty ? '${d['address']}' : (current.isEmpty ? '' : current.first);
    _gateway.text = '${d['gateway'] ?? ''}'.isNotEmpty ? '${d['gateway']}' : '${d['current_gateway'] ?? ''}';
    _dns.text = textList(d['dns']).join(', ');
  }

  Future<void> _apply() async {
    final body = networkBody(mode: mode, address: _address.text, gateway: _gateway.text, dns: _dns.text);
    if (body == null) return;
    final m = ScaffoldMessenger.of(context);
    if (!await ask(c.t('settings.net_confirm'), 'settings.net_apply', danger: true)) return;
    setState(() => busy = true);
    final r = await call(() => s.post('/api/settings/network', body), m);
    if (mounted) setState(() => busy = false);
    if (r is Map) {
      say(m, c.t('settings.net_started', {'s': r['rollback_seconds']}));
      net.poll();
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = net.network;
    if (d == null) return _Card(c.t('settings.network_title'), [Busy(c.t('common.loading'))]);
    if (_filled != net.generation) _fill(d);
    final pending = netPending(d);
    final static = mode == 'static';
    return _Card(c.t('settings.network_title'), [
      _PendingBox(c, net),
      _hint(context, netCurrent(d, c.t)),
      _select(c.t('settings.net_mode'), mode, [('dhcp', c.t('settings.net_dhcp')), ('static', c.t('settings.net_static'))], (v) => setState(() => mode = v)),
      if (static) ...[
        _field(_address, c.t('settings.net_address'), hint: c.t('settings.net_address_hint'), onChanged: (_) => setState(() {})),
        _field(_gateway, c.t('settings.net_gateway'), hint: c.t('settings.net_gateway_hint'), onChanged: (_) => setState(() {})),
        _field(_dns, c.t('settings.net_dns'), hint: c.t('settings.net_dns_hint'), onChanged: (_) => setState(() {})),
      ],
      _hint(context, c.t('settings.net_hint')),
      FilledButton(
        onPressed: busy || pending != null || networkBody(mode: mode, address: _address.text, gateway: _gateway.text, dns: _dns.text) == null ? null : _apply,
        child: Text(c.t('common.save')),
      ),
    ]);
  }
}

/// A network change waiting for confirmation, or one that was reverted.
/// Shown on both network cards, so it is seen wherever the change was made.
class _PendingBox extends StatefulWidget {
  const _PendingBox(this.c, this.net);

  final Controller c;
  final _Net net;

  @override
  State<_PendingBox> createState() => _PendingBoxState();
}

class _PendingBoxState extends State<_PendingBox> with AreaCalls {
  bool busy = false;

  @override
  Controller get c => widget.c;

  Future<void> _keep() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/settings/network/confirm'), m);
    if (mounted) setState(() => busy = false);
    if (r == null) return;
    say(m, c.t('settings.net_kept'));
    await Future<void>.delayed(const Duration(milliseconds: 1500));
    await widget.net.load();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final d = widget.net.network;
    final pending = d == null ? null : netPending(d);
    if (d == null || (pending == null && d['reverted'] != true)) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: (pending != null ? p.warn : p.danger).withValues(alpha: .12),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(pending != null ? c.t('settings.net_pending', {'s': pending}) : c.t('settings.net_reverted')),
        if (pending != null) ...[
          const SizedBox(height: 10),
          FilledButton(onPressed: busy ? null : _keep, child: Text(c.t('settings.net_keep'))),
        ],
      ]),
    );
  }
}

class _VlanEdit {
  _VlanEdit(Vlan v)
      : id = TextEditingController(text: v.id),
        address = TextEditingController(text: v.address),
        mode = v.mode;

  final TextEditingController id;
  final TextEditingController address;
  String mode;

  Vlan get vlan => Vlan(id: id.text, mode: mode, address: address.text);

  void dispose() {
    id.dispose();
    address.dispose();
  }
}

class _LayoutCard extends StatefulWidget {
  const _LayoutCard(this.c, this.net);

  final Controller c;
  final _Net net;

  @override
  State<_LayoutCard> createState() => _LayoutCardState();
}

class _LayoutCardState extends State<_LayoutCard> with AreaCalls {
  final used = <String>{};
  String bondMode = '';
  bool bridge = false;
  final vlans = <_VlanEdit>[];
  int _filled = -1;
  bool busy = false;

  @override
  Controller get c => widget.c;

  _Net get net => widget.net;

  @override
  void initState() {
    super.initState();
    net.addListener(_changed);
  }

  @override
  void dispose() {
    net.removeListener(_changed);
    for (final v in vlans) {
      v.dispose();
    }
    super.dispose();
  }

  void _changed() {
    if (mounted) setState(() {});
  }

  void _fill(Map d) {
    _filled = net.generation;
    used
      ..clear()
      ..addAll(textList(d['nics']));
    final modes = textList(d['bond_modes']);
    bondMode = '${d['bond_mode'] ?? ''}'.isNotEmpty ? '${d['bond_mode']}' : (modes.isEmpty ? '' : modes.first);
    bridge = d['bridge'] == true;
    for (final v in vlans) {
      v.dispose();
    }
    vlans
      ..clear()
      ..addAll([for (final v in (d['vlans'] is List ? (d['vlans'] as List).whereType<Map>() : const <Map>[])) _VlanEdit(Vlan.of(v))]);
  }

  Future<void> _wol(Map port, bool on) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.post('/api/settings/wol', {'port': port['name'], 'enabled': on}), m);
    if (r != null) say(m, c.t(on ? 'netlayout.wol_on' : 'netlayout.wol_off', {'port': port['name'], 'mac': port['mac']}));
    await net.load(fill: false);
  }

  Future<void> _apply(List<String> order) async {
    final m = ScaffoldMessenger.of(context);
    final body = layoutBody(nics: [for (final n in order) if (used.contains(n)) n], bondMode: bondMode, bridge: bridge, vlans: [for (final v in vlans) v.vlan]);
    if (body == null) {
      say(m, c.t('netlayout.no_port'), error: true);
      return;
    }
    if (!await ask(c.t('netlayout.confirm'), 'netlayout.apply', danger: true)) return;
    setState(() => busy = true);
    final r = await call(() => s.post('/api/settings/network/layout', body), m);
    if (mounted) setState(() => busy = false);
    if (r is Map) {
      say(m, c.t('settings.net_started', {'s': r['rollback_seconds']}));
      net.poll();
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final d = net.layout;
    if (d == null) return _Card(c.t('netlayout.title'), [Busy(c.t('common.loading'))]);
    if (_filled != net.generation) _fill(d);
    final ports = d['ports'] is List ? (d['ports'] as List).whereType<Map>().toList() : <Map>[];
    final order = [for (final port in ports) '${port['name']}'];
    final head = TextStyle(color: p.muted, fontSize: 12.5, fontWeight: FontWeight.w600);
    final pending = net.network == null ? null : netPending(net.network!);
    return _Card(c.t('netlayout.title'), [
      _PendingBox(c, net),
      _hint(context, layoutSummary(d, c.t)),
      SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: DataTable(
          headingRowHeight: 36,
          horizontalMargin: 0,
          columnSpacing: 24,
          columns: [for (final k in ['use', 'port', 'mac', 'link', 'wol']) DataColumn(label: Text(c.t('netlayout.$k'), style: head))],
          rows: [
            for (final port in ports)
              DataRow(cells: [
                DataCell(Checkbox(
                  value: used.contains('${port['name']}'),
                  onChanged: (v) => setState(() => v == true ? used.add('${port['name']}') : used.remove('${port['name']}')),
                )),
                DataCell(Text('${port['name']}', style: const TextStyle(fontWeight: FontWeight.w600))),
                DataCell(SelectableText('${port['mac'] ?? '–'}', style: const TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12.5))),
                DataCell(port['link'] == true
                    ? Tag(port['speed'] is num ? '${port['speed']} Mbit/s' : c.t('netlayout.up'), p.ok)
                    : Tag(c.t('netlayout.down'), p.muted)),
                DataCell(Tooltip(
                  message: port['wol_supported'] == true ? '' : c.t('netlayout.wol_unsupported'),
                  child: Checkbox(
                    value: port['wol'] == true || port['wol_at_boot'] == true,
                    onChanged: port['wol_supported'] == true ? (v) => _wol(port, v ?? false) : null,
                  ),
                )),
              ]),
          ],
        ),
      ),
      const SizedBox(height: 14),
      if (used.length > 1)
        _select(c.t('netlayout.bond_mode'), bondMode, [for (final m in textList(d['bond_modes'])) (m, c.t('netlayout.mode_$m'))], (v) => setState(() => bondMode = v)),
      _check(c.t('netlayout.bridge'), bridge, (v) => setState(() => bridge = v)),
      const SizedBox(height: 10),
      Text(c.t('netlayout.vlans'), style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 10),
      for (final v in vlans)
        Padding(
          padding: const EdgeInsets.only(bottom: 4),
          child: Wrap(spacing: 12, crossAxisAlignment: WrapCrossAlignment.center, children: [
            SizedBox(
              width: 120,
              child: _field(v.id, c.t('netlayout.vlan_id'), formatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(4)]),
            ),
            SizedBox(
              width: 200,
              child: _select(c.t('netlayout.vlan_mode'), v.mode, [for (final m in ['static', 'manual']) (m, c.t('netlayout.vlan_$m'))], (m) => setState(() => v.mode = m)),
            ),
            if (v.mode == 'static')
              SizedBox(width: 220, child: _field(v.address, c.t('netlayout.vlan_address'), hint: '10.0.20.5/24', formatters: [LengthLimitingTextInputFormatter(18)])),
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: TextButton(
                onPressed: () => setState(() {
                  vlans.remove(v);
                  v.dispose();
                }),
                child: Text(c.t('common.remove')),
              ),
            ),
          ]),
        ),
      Align(
        alignment: Alignment.centerLeft,
        child: OutlinedButton(onPressed: () => setState(() => vlans.add(_VlanEdit(Vlan()))), child: Text(c.t('netlayout.vlan_add'))),
      ),
      const SizedBox(height: 14),
      _hint(context, c.t('netlayout.hint')),
      _hint(context, c.t('netlayout.wol_hint')),
      FilledButton(onPressed: busy || pending != null ? null : () => _apply(order), child: Text(c.t('netlayout.apply'))),
    ]);
  }
}

// --- spindown -----------------------------------------------------------------------

class _SpindownCard extends StatefulWidget {
  const _SpindownCard(this.c);

  final Controller c;

  @override
  State<_SpindownCard> createState() => _SpindownCardState();
}

class _SpindownCardState extends State<_SpindownCard> with AreaCalls, _CardLoad {
  int fallback = 0;
  final disks = <String, int>{};

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/settings/spindown';

  @override
  void fill(Map data) {
    fallback = data['default'] is num ? (data['default'] as num).toInt() : 0;
    disks
      ..clear()
      ..addAll({
        for (final disk in (data['disks'] is List ? (data['disks'] as List).whereType<Map>() : const <Map>[]))
          if ('${disk['serial'] ?? ''}'.isNotEmpty) '${disk['serial']}': disk['minutes'] is num ? (disk['minutes'] as num).toInt() : -1,
      });
  }

  Future<void> _save() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/settings/spindown', {'default': fallback, 'disks': disks}, const Duration(minutes: 2)), m);
    if (mounted) setState(() => busy = false);
    if (r == null) return;
    final failed = spindownFailed(r, c.t);
    say(m, failed ?? c.t('settings.spindown_saved'), error: failed != null);
    await load(fill: true);
  }

  @override
  Widget build(BuildContext context) {
    final data = d;
    if (data == null) return _Card(c.t('settings.spindown_title'), [Busy(c.t('common.loading'))]);
    final choices = intList(data['choices']);
    final list = data['disks'] is List ? (data['disks'] as List).whereType<Map>().toList() : <Map>[];
    return _Card(c.t('settings.spindown_title'), [
      _select(c.t('settings.spindown_default'), fallback, [for (final m in choices) (m, spindownLabel(m, c.t))], (v) => setState(() => fallback = v)),
      if (list.isEmpty) _hint(context, c.t('settings.spindown_none')),
      for (final disk in list)
        _select<int>(
          diskLabel(disk, c.t),
          disks['${disk['serial'] ?? ''}'] ?? -1,
          [for (final m in [-1, ...choices]) (m, spindownLabel(m, c.t))],
          '${disk['serial'] ?? ''}'.isEmpty ? null : (v) => setState(() => disks['${disk['serial']}'] = v),
        ),
      _hint(context, c.t('settings.spindown_hint')),
      FilledButton(onPressed: busy ? null : _save, child: Text(c.t('common.save'))),
    ]);
  }
}

// --- power saving -------------------------------------------------------------------

class _PowerCard extends StatefulWidget {
  const _PowerCard(this.c);

  final Controller c;

  @override
  State<_PowerCard> createState() => _PowerCardState();
}

class _PowerCardState extends State<_PowerCard> with AreaCalls, _CardLoad {
  String governor = '';

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/settings/power';

  @override
  void fill(Map data) => governor = governorChoice(data);

  @override
  Widget build(BuildContext context) {
    final data = d;
    if (data == null) return _Card(c.t('power.title'), [Busy(c.t('common.loading'))]);
    final available = textList(data['available']);
    if (available.isEmpty) return _Card(c.t('power.title'), [_hint(context, c.t('power.none'))]);
    return _Card(c.t('power.title'), [
      _select(c.t('power.governor'), governor, [for (final g in available) (g, governorLabel(g, c.t))], (v) => setState(() => governor = v)),
      _hint(context, powerState(data, c.t)),
      _hint(context, c.t('power.hint')),
      FilledButton(
        onPressed: busy || governor.isEmpty ? null : () => save(() => s.post('/api/settings/power', {'governor': governor}), 'power.saved'),
        child: Text(c.t('common.save')),
      ),
    ]);
  }
}

// --- remote syslog ------------------------------------------------------------------

class _SyslogCard extends StatefulWidget {
  const _SyslogCard(this.c);

  final Controller c;

  @override
  State<_SyslogCard> createState() => _SyslogCardState();
}

class _SyslogCardState extends State<_SyslogCard> with AreaCalls, _CardLoad {
  bool enabled = false;
  String protocol = 'udp';
  String severity = 'info';
  final _host = TextEditingController();
  final _port = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/settings/syslog';

  @override
  void fill(Map data) {
    enabled = data['enabled'] == true;
    _host.text = '${data['host'] ?? ''}';
    _port.text = '${data['port'] ?? 514}';
    protocol = data['protocol'] == 'tcp' ? 'tcp' : 'udp';
    severity = syslogSeverities.contains(data['severity']) ? '${data['severity']}' : 'info';
  }

  @override
  void dispose() {
    _host.dispose();
    _port.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final data = d;
    if (data == null) return _Card(c.t('syslog.title'), [Busy(c.t('common.loading'))]);
    return _Card(c.t('syslog.title'), [
      _check(c.t('syslog.enabled'), enabled, (v) => setState(() => enabled = v)),
      const SizedBox(height: 6),
      _field(_host, c.t('syslog.host'), hint: c.t('syslog.host_hint')),
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(child: _field(_port, c.t('syslog.port'), formatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(5)])),
        const SizedBox(width: 12),
        Expanded(child: _select(c.t('syslog.protocol'), protocol, [('udp', c.t('syslog.udp')), ('tcp', c.t('syslog.tcp'))], (v) => setState(() => protocol = v))),
      ]),
      _select(c.t('syslog.severity'), severity, [for (final v in syslogSeverities) (v, c.t('syslog.sev_$v'))], (v) => setState(() => severity = v)),
      _hint(context, syslogState(data, c.t)),
      _hint(context, c.t('syslog.hint')),
      FilledButton(
        onPressed: busy
            ? null
            : () => save(
                  () => s.post('/api/settings/syslog',
                      syslogBody(enabled: enabled, host: _host.text, port: _port.text, protocol: protocol, severity: severity)),
                  'syslog.saved',
                ),
        child: Text(c.t('common.save')),
      ),
    ]);
  }
}

// --- devices ------------------------------------------------------------------------

class _DevicesCard extends StatefulWidget {
  const _DevicesCard(this.c);

  final Controller c;

  @override
  State<_DevicesCard> createState() => _DevicesCardState();
}

class _DevicesCardState extends State<_DevicesCard> with AreaCalls, _CardLoad {
  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/devices';

  @override
  void fill(Map data) {}

  Widget _table(List<String> heads, List<List<String>> rows) {
    final p = context.palette;
    if (rows.isEmpty) return _hint(context, c.t('devices.none'));
    final head = TextStyle(color: p.muted, fontSize: 12.5, fontWeight: FontWeight.w600);
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: DataTable(
        headingRowHeight: 36,
        dataRowMinHeight: 36,
        dataRowMaxHeight: 56,
        horizontalMargin: 0,
        columnSpacing: 24,
        columns: [for (final h in heads) DataColumn(label: Text(c.t('devices.$h'), style: head))],
        rows: [for (final r in rows) DataRow(cells: [for (final v in r) DataCell(SelectableText(v))])],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final data = d;
    if (data == null) return _Card(c.t('devices.title'), [Busy(c.t('common.loading'))]);
    return _Card(c.t('devices.title'), [
      Text(c.t('devices.pci'), style: const TextStyle(fontWeight: FontWeight.w600)),
      _table(['slot', 'class', 'device', 'id', 'driver', 'iommu'], pciRows(data)),
      const SizedBox(height: 18),
      Text(c.t('devices.usb'), style: const TextStyle(fontWeight: FontWeight.w600)),
      _table(['bus', 'device', 'id'], usbRows(data)),
      const SizedBox(height: 14),
      _hint(context, c.t(data['iommu'] == true ? 'devices.iommu_on' : 'devices.iommu_off')),
      OutlinedButton(onPressed: busy ? null : () => load(), child: Text(c.t('common.refresh'))),
    ]);
  }
}
