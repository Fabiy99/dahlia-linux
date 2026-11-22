// Security section of the settings: firewall with own rules, WireGuard and
// Tailscale, the account's password and second factor, dashboard accounts
// and API tokens (both for administrators). Same API as the dashboard.

import 'dart:async';
import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';
import 'package:url_launcher/url_launcher.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/security.dart';
import 'area.dart';
import 'cards.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class SecuritySection extends StatefulWidget {
  const SecuritySection(this.c, {super.key});

  final Controller c;

  @override
  State<SecuritySection> createState() => _SecuritySectionState();
}

class _SecuritySectionState extends State<SecuritySection> {
  bool admin = false;

  @override
  void initState() {
    super.initState();
    _role();
  }

  /// Accounts and tokens are for administrators only.
  Future<void> _role() async {
    try {
      final st = await widget.c.server?.get('/api/state');
      if (mounted && st is Map) setState(() => admin = st['role'] == 'admin');
    } on Exception {
      // Without the role the admin cards stay hidden.
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final half = grid.cols == 1 ? full : (full - 18) / 2;
    return Wrap(spacing: 18, runSpacing: 18, children: [
      SizedBox(width: full, child: _FirewallCard(c)),
      SizedBox(width: full, child: _WireguardCard(c)),
      SizedBox(width: half, child: _TailscaleCard(c)),
      SizedBox(width: half, child: _PasswordCard(c)),
      SizedBox(width: half, child: _TotpCard(c)),
      if (admin) SizedBox(width: half, child: _TokensCard(c)),
      if (admin) SizedBox(width: full, child: _UsersCard(c)),
    ]);
  }
}

/// Settings of a family account: its password and second factor.
class FamilyAccountPage extends StatelessWidget {
  const FamilyAccountPage(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) {
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final half = grid.cols == 1 ? full : (full - 18) / 2;
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      AreaTitle(c.t('nav.account')),
      const SizedBox(height: 28),
      Wrap(spacing: 18, runSpacing: 18, children: [
        SizedBox(width: half, child: _PasswordCard(c)),
        SizedBox(width: half, child: _TotpCard(c)),
      ]),
    ]);
  }
}

TextStyle _head(BuildContext context) => TextStyle(color: context.palette.muted, fontSize: 12.5, fontWeight: FontWeight.w600);

const _mono = TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12.5, height: 1.4);

Widget _codeBox(BuildContext context, String text) => Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: context.palette.sub, borderRadius: BorderRadius.circular(12)),
      child: SelectableText(text, style: _mono),
    );

Widget _qr(String svg) => Container(
      width: 200,
      height: 200,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12)),
      child: SvgPicture.string(svg, fit: BoxFit.contain),
    );

// --- firewall -------------------------------------------------------------------

class _FirewallCard extends StatefulWidget {
  const _FirewallCard(this.c);

  final Controller c;

  @override
  State<_FirewallCard> createState() => _FirewallCardState();
}

class _FirewallCardState extends State<_FirewallCard> with AreaCalls, CardLoad {
  bool enabled = true;
  final _networks = TextEditingController();
  String action = 'accept';
  String protocol = 'tcp';
  String sourceKind = 'local';
  final _ports = TextEditingController();
  final _network = TextEditingController();
  final _comment = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/settings/firewall';

  @override
  void fill(Map data) {
    enabled = data['enabled'] == true;
    _networks.text = data['networks'] is List ? (data['networks'] as List).join(', ') : '';
  }

  @override
  void dispose() {
    for (final t in [_networks, _ports, _network, _comment]) {
      t.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (!enabled && d?['enabled'] == true && !await ask(c.t('settings.firewall_disable_confirm'), 'settings.firewall_disable', danger: true)) return;
    await save(() => s.post('/api/settings/firewall', {'enabled': enabled, 'networks': splitNetworks(_networks.text)}), 'settings.firewall_saved');
  }

  /// Rules are saved as a whole list; the answer is the normalized list.
  Future<bool> _rules(List<Map> rules) async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/settings/firewall/rules', {'rules': rules}), m);
    if (mounted) setState(() => busy = false);
    if (r == null) return false;
    say(m, c.t('fwrules.saved'));
    await load();
    return true;
  }

  Future<void> _add(List<Map> rules) async {
    final rule = newRule(action: action, protocol: protocol, ports: _ports.text, sourceKind: sourceKind, network: _network.text, comment: _comment.text);
    if (rule['action'] == 'accept' && rule['source'] == 'any' && !await ask(c.t('fwrules.any_confirm', {'ports': rule['ports']}), 'fwrules.add', danger: true)) return;
    if (await _rules([...rules, rule])) {
      _ports.clear();
      _network.clear();
      _comment.clear();
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return SettingsCard(c.t('settings.firewall_title'), [Busy(c.t('common.loading'))]);
    final rules = maps(data['rules']);
    return SettingsCard(c.t('settings.firewall_title'), [
      cardCheck(c.t('settings.firewall_enabled'), enabled, (v) => setState(() => enabled = v)),
      const SizedBox(height: 6),
      cardHint(context, c.t('settings.firewall_hint')),
      cardHint(context, firewallLocal(data, c.t)),
      cardHint(context, firewallPorts(data, c.t)),
      cardField(_networks, c.t('settings.firewall_networks'), hint: '192.168.50.0/24, 10.8.0.0/24'),
      Align(alignment: Alignment.centerLeft, child: FilledButton(onPressed: busy ? null : _save, child: Text(c.t('common.save')))),
      const SizedBox(height: 26),
      Text(c.t('fwrules.title'), style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 6),
      cardHint(context, c.t('fwrules.hint')),
      if (rules.isEmpty)
        cardHint(context, c.t('fwrules.none'))
      else
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: DataTable(
            headingRowHeight: 36,
            horizontalMargin: 0,
            columnSpacing: 24,
            columns: [
              for (final k in ['action', 'port', 'source', 'comment']) DataColumn(label: Text(c.t('fwrules.$k'), style: _head(context))),
              const DataColumn(label: SizedBox()),
            ],
            rows: [
              for (var i = 0; i < rules.length; i++)
                DataRow(cells: [
                  DataCell(Tag(c.t('fwrules.${rules[i]['action']}'), rules[i]['action'] == 'accept' ? p.ok : p.danger)),
                  DataCell(Text(rulePorts(rules[i], c.t))),
                  DataCell(Text(ruleSource(rules[i], c.t))),
                  DataCell(Text('${rules[i]['comment'] ?? ''}'.isEmpty ? '–' : '${rules[i]['comment']}')),
                  DataCell(Row(mainAxisSize: MainAxisSize.min, children: [
                    IconButton(
                      tooltip: c.t('fwrules.up'),
                      icon: const Icon(Icons.arrow_upward_rounded, size: 18),
                      onPressed: busy || i == 0 ? null : () => _rules(moveRule(rules, i, i - 1)),
                    ),
                    IconButton(
                      tooltip: c.t('fwrules.down'),
                      icon: const Icon(Icons.arrow_downward_rounded, size: 18),
                      onPressed: busy || i == rules.length - 1 ? null : () => _rules(moveRule(rules, i, i + 1)),
                    ),
                    TextButton(
                      style: TextButton.styleFrom(foregroundColor: p.danger),
                      onPressed: busy ? null : () => _rules([for (var j = 0; j < rules.length; j++) if (j != i) rules[j]]),
                      child: Text(c.t('shares.remove')),
                    ),
                  ])),
                ]),
            ],
          ),
        ),
      const SizedBox(height: 14),
      Wrap(spacing: 12, crossAxisAlignment: WrapCrossAlignment.start, children: [
        SizedBox(
          width: 150,
          child: cardSelect(c.t('fwrules.action'), action, [('accept', c.t('fwrules.accept')), ('drop', c.t('fwrules.drop'))], (v) => setState(() => action = v)),
        ),
        SizedBox(
          width: 130,
          child: cardSelect(c.t('fwrules.protocol'), protocol, [for (final v in ['tcp', 'udp', 'all']) (v, c.t('fwrules.$v'))], (v) => setState(() => protocol = v)),
        ),
        if (protocol != 'all') SizedBox(width: 160, child: cardField(_ports, c.t('fwrules.ports'), hint: '27015-27030', formatters: [LengthLimitingTextInputFormatter(11)])),
        SizedBox(
          width: 240,
          child: cardSelect(c.t('fwrules.source'), sourceKind, [for (final v in ['local', 'any', 'net']) (v, c.t('fwrules.$v'))], (v) => setState(() => sourceKind = v)),
        ),
        if (sourceKind == 'net') SizedBox(width: 200, child: cardField(_network, c.t('fwrules.network'), hint: '192.168.1.20')),
        SizedBox(width: 220, child: cardField(_comment, c.t('fwrules.comment'), formatters: [LengthLimitingTextInputFormatter(64)])),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: FilledButton(onPressed: busy ? null : () => _add(rules), child: Text(c.t('fwrules.add'))),
        ),
      ]),
    ]);
  }
}

// --- WireGuard ------------------------------------------------------------------

/// VPN state for the WireGuard and Tailscale cards, asked again every 3 s
/// while Tailscale installs or waits for a login.
mixin _VpnLoad<T extends StatefulWidget> on State<T>, AreaCalls<T>, CardLoad<T> {
  Timer? _timer;

  @override
  String get path => '/api/vpn';

  @override
  Future<void> load({bool fill = false}) async {
    await super.load(fill: fill);
    _timer?.cancel();
    final ts = d?['tailscale'];
    if (mounted && ts is Map && tailscaleBusy(ts)) _timer = Timer(const Duration(seconds: 3), () => load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}

class _WireguardCard extends StatefulWidget {
  const _WireguardCard(this.c);

  final Controller c;

  @override
  State<_WireguardCard> createState() => _WireguardCardState();
}

class _WireguardCardState extends State<_WireguardCard> with AreaCalls, CardLoad {
  bool enabled = false;
  String route = 'lan';
  final _endpoint = TextEditingController();
  final _port = TextEditingController();
  final _dns = TextEditingController();
  final _subnet = TextEditingController();
  final _peer = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/vpn';

  Map get wg => d?['wireguard'] is Map ? d!['wireguard'] as Map : const {};

  @override
  void fill(Map data) {
    final w = data['wireguard'] is Map ? data['wireguard'] as Map : const {};
    enabled = w['enabled'] == true;
    route = w['route'] == 'all' ? 'all' : 'lan';
    _endpoint.text = '${w['endpoint'] ?? ''}';
    _port.text = '${w['port'] ?? ''}';
    _dns.text = '${w['dns'] ?? ''}';
    _subnet.text = '${w['subnet'] ?? ''}';
  }

  @override
  void dispose() {
    for (final t in [_endpoint, _port, _dns, _subnet, _peer]) {
      t.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final body = wireguardBody(enabled: enabled, port: _port.text, endpoint: _endpoint.text, route: route, dns: _dns.text, subnet: _subnet.text);
    if (body == null) return;
    await save(() => s.post('/api/vpn/wireguard', body), 'vpn.saved');
  }

  Future<void> _addPeer() async {
    final m = ScaffoldMessenger.of(context);
    final name = _peer.text.trim();
    if (name.isEmpty) return;
    setState(() => busy = true);
    final r = await call(() => s.post('/api/vpn/wireguard/peers', {'name': name}), m);
    if (mounted) setState(() => busy = false);
    if (r is! Map || !mounted) return;
    _peer.clear();
    await showDialog<void>(context: context, builder: (_) => _PeerDialog(c, r));
    await load();
  }

  Future<void> _removePeer(Map p) async {
    if (!await ask(c.t('vpn.peer_remove_confirm', {'name': p['name']}), 'vpn.peer_remove', danger: true)) return;
    await save(() => s.post('/api/vpn/wireguard/peers/delete', {'name': p['name']}), '');
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    if (d == null) return SettingsCard(c.t('vpn.wg_title'), [Busy(c.t('common.loading'))]);
    final w = wg;
    final peers = maps(w['peers']);
    final when = DateFormat.yMd(c.settings.lang).add_Hm();
    final canAdd = w['enabled'] == true && '${w['endpoint'] ?? ''}'.isNotEmpty;
    return SettingsCard(c.t('vpn.wg_title'), [
      cardHint(context, c.t('vpn.wg_hint')),
      cardCheck(c.t('vpn.wg_enabled'), enabled, (v) => setState(() => enabled = v)),
      const SizedBox(height: 8),
      cardField(_endpoint, c.t('vpn.endpoint'), hint: c.t('vpn.endpoint_placeholder')),
      Wrap(spacing: 12, children: [
        SizedBox(width: 140, child: cardField(_port, c.t('vpn.port'), formatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(5)])),
        SizedBox(
          width: 280,
          child: cardSelect(c.t('vpn.route'), route, [('lan', c.t('vpn.route_lan')), ('all', c.t('vpn.route_all'))], (v) => setState(() => route = v)),
        ),
        SizedBox(width: 220, child: cardField(_dns, c.t('vpn.dns'), hint: c.t('vpn.dns_placeholder'))),
        SizedBox(
          width: 200,
          child: Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: TextField(
              controller: _subnet,
              readOnly: peers.isNotEmpty,
              autocorrect: false,
              decoration: InputDecoration(labelText: c.t('vpn.subnet')),
            ),
          ),
        ),
      ]),
      cardHint(context, c.t('vpn.router', {'port': w['port']})),
      FilledButton(onPressed: busy ? null : _save, child: Text(c.t('common.save'))),
      const SizedBox(height: 26),
      Text(c.t('vpn.peers'), style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 8),
      if (peers.isEmpty)
        cardHint(context, c.t('vpn.no_peers'))
      else
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: DataTable(
            headingRowHeight: 36,
            horizontalMargin: 0,
            columnSpacing: 24,
            columns: [
              for (final k in ['peer_name', 'peer_address', 'peer_handshake', 'peer_traffic']) DataColumn(label: Text(c.t('vpn.$k'), style: _head(context))),
              const DataColumn(label: SizedBox()),
            ],
            rows: [
              for (final peer in peers)
                DataRow(cells: [
                  DataCell(Text('${peer['name']}', style: const TextStyle(fontWeight: FontWeight.w600))),
                  DataCell(Text('${peer['address'] ?? ''}')),
                  DataCell(Text(handshake(peer) == null ? c.t('vpn.never') : when.format(handshake(peer)!))),
                  DataCell(Text(peerTraffic(peer, c.t))),
                  DataCell(IconButton(
                    tooltip: c.t('vpn.peer_remove'),
                    icon: Icon(Icons.delete_outline_rounded, color: p.danger, size: 20),
                    onPressed: busy ? null : () => _removePeer(peer),
                  )),
                ]),
            ],
          ),
        ),
      const SizedBox(height: 12),
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child: cardField(_peer, c.t('vpn.peer_name'), hint: c.t('vpn.peer_placeholder'), formatters: [LengthLimitingTextInputFormatter(32)], onChanged: (_) => setState(() {})),
        ),
        const SizedBox(width: 12),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: OutlinedButton(onPressed: busy || !canAdd || _peer.text.trim().isEmpty ? null : _addPeer, child: Text(c.t('vpn.peer_add'))),
        ),
      ]),
    ]);
  }
}

/// A new peer's configuration with QR code, shown once.
class _PeerDialog extends StatelessWidget {
  const _PeerDialog(this.c, this.r);

  final Controller c;
  final Map r;

  Future<void> _download(BuildContext context) async {
    final m = ScaffoldMessenger.of(context);
    final where = await getSaveLocation(suggestedName: peerFile('${r['name']}'));
    if (where == null) return;
    await File(where.path).writeAsString('${r['config'] ?? ''}');
    m.showSnackBar(SnackBar(content: Text(c.t('fm.downloaded', {'path': where.path}))));
  }

  @override
  Widget build(BuildContext context) {
    final svg = '${r['qr_svg'] ?? ''}';
    return AlertDialog(
      title: Text(c.t('vpn.peer_title', {'name': r['name']})),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            cardHint(context, c.t('vpn.peer_once')),
            Wrap(spacing: 16, runSpacing: 12, children: [
              if (svg.isNotEmpty) _qr(svg),
              SizedBox(width: 320, child: _codeBox(context, '${r['config'] ?? ''}')),
            ]),
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: () => _download(context), child: Text(c.t('vpn.peer_download'))),
        FilledButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.ok'))),
      ],
    );
  }
}

// --- Tailscale ------------------------------------------------------------------

class _TailscaleCard extends StatefulWidget {
  const _TailscaleCard(this.c);

  final Controller c;

  @override
  State<_TailscaleCard> createState() => _TailscaleCardState();
}

class _TailscaleCardState extends State<_TailscaleCard> with AreaCalls, CardLoad, _VpnLoad {
  bool routes = false;
  bool exitNode = false;

  @override
  Controller get c => widget.c;

  Map get ts => d?['tailscale'] is Map ? d!['tailscale'] as Map : const {};

  @override
  void fill(Map data) {
    final t = data['tailscale'] is Map ? data['tailscale'] as Map : const {};
    routes = t['routes'] == true;
    exitNode = t['exit_node'] == true;
  }

  Future<void> _logout() async {
    if (!await ask(c.t('vpn.ts_logout_confirm'), 'vpn.ts_logout', danger: true)) return;
    await save(() => s.post('/api/vpn/tailscale/logout'), '');
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    if (d == null) return SettingsCard(c.t('vpn.ts_title'), [Busy(c.t('common.loading'))]);
    final t = ts;
    final installed = t['installed'] == true;
    final running = t['state'] == 'Running';
    final (lines, link) = tailscaleLines(t, c.t);
    Color color(String level) => switch (level) {
          'ok' => p.ok,
          'crit' => p.danger,
          _ => p.muted,
        };
    return SettingsCard(c.t('vpn.ts_title'), [
      cardHint(context, c.t('vpn.ts_hint')),
      for (final (level, text) in lines)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(level == 'ok' ? Icons.check_circle_rounded : (level == 'crit' ? Icons.error_rounded : Icons.info_outline_rounded), size: 18, color: color(level)),
            const SizedBox(width: 8),
            Expanded(child: SelectableText(text)),
          ]),
        ),
      if (link != null)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: OutlinedButton.icon(
            onPressed: () => launchUrl(Uri.parse(link)),
            icon: const Icon(Icons.open_in_new_rounded, size: 18),
            label: Text(c.t('vpn.ts_login_link')),
          ),
        ),
      const SizedBox(height: 4),
      cardCheck(c.t('vpn.ts_routes'), routes, installed ? (v) => setState(() => routes = v) : null),
      cardCheck(c.t('vpn.ts_exit'), exitNode, installed ? (v) => setState(() => exitNode = v) : null),
      const SizedBox(height: 10),
      Wrap(spacing: 10, runSpacing: 10, children: [
        if (!installed)
          FilledButton(
            onPressed: busy || unitRunning(t['install_job']) ? null : () => save(() => s.post('/api/vpn/tailscale/install'), 'vpn.ts_installing'),
            child: Text(c.t('vpn.ts_install')),
          ),
        if (installed)
          FilledButton(
            onPressed: busy ? null : () => save(() => s.post('/api/vpn/tailscale/up', {'routes': routes, 'exit_node': exitNode}), ''),
            child: Text(c.t(running ? 'common.save' : 'vpn.ts_up')),
          ),
        if (installed && running)
          OutlinedButton(
            style: OutlinedButton.styleFrom(foregroundColor: p.danger),
            onPressed: busy ? null : _logout,
            child: Text(c.t('vpn.ts_logout')),
          ),
      ]),
    ]);
  }
}

// --- password -------------------------------------------------------------------

class _PasswordCard extends StatefulWidget {
  const _PasswordCard(this.c);

  final Controller c;

  @override
  State<_PasswordCard> createState() => _PasswordCardState();
}

class _PasswordCardState extends State<_PasswordCard> with AreaCalls {
  final _old = TextEditingController();
  final _new = TextEditingController();
  final _again = TextEditingController();
  bool busy = false;

  @override
  Controller get c => widget.c;

  @override
  void dispose() {
    for (final t in [_old, _new, _again]) {
      t.dispose();
    }
    super.dispose();
  }

  Future<void> _change() async {
    final m = ScaffoldMessenger.of(context);
    if (_new.text != _again.text) {
      say(m, c.t('error.password_mismatch'), error: true);
      return;
    }
    setState(() => busy = true);
    final password = _new.text;
    final r = await call(() => s.post('/api/account/password', {'old': _old.text, 'new': password}), m);
    if (mounted) setState(() => busy = false);
    if (r == null) return;
    for (final t in [_old, _new, _again]) {
      t.clear();
    }
    await c.passwordChanged(password);
    say(m, c.t('account.changed'));
  }

  @override
  Widget build(BuildContext context) {
    final ready = _old.text.isNotEmpty && _new.text.isNotEmpty && _again.text.isNotEmpty;
    void changed(String _) => setState(() {});
    return SettingsCard(c.t('account.password_title'), [
      cardField(_old, c.t('account.old'), obscure: true, onChanged: changed),
      cardField(_new, c.t('field.password'), obscure: true, onChanged: changed),
      cardField(_again, c.t('field.password_repeat'), obscure: true, onChanged: changed),
      FilledButton(onPressed: busy || !ready ? null : _change, child: Text(c.t('account.submit'))),
    ]);
  }
}

// --- second factor --------------------------------------------------------------

class _TotpCard extends StatefulWidget {
  const _TotpCard(this.c);

  final Controller c;

  @override
  State<_TotpCard> createState() => _TotpCardState();
}

class _TotpCardState extends State<_TotpCard> with AreaCalls, CardLoad {
  final _password = TextEditingController();
  final _code = TextEditingController();

  /// Setup in progress: key and QR code from the server.
  Map? setup;

  /// Recovery codes, shown once after the setup.
  List<String>? codes;

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/account/totp';

  @override
  void fill(Map data) {}

  @override
  void dispose() {
    _password.dispose();
    _code.dispose();
    super.dispose();
  }

  Future<void> _begin() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/account/totp/begin', {'password': _password.text}), m);
    if (mounted) setState(() => busy = false);
    if (r is! Map) return;
    _password.clear();
    setState(() => setup = r);
  }

  Future<void> _confirm() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/account/totp/confirm', {'code': _code.text.trim()}), m);
    if (mounted) setState(() => busy = false);
    if (r is! Map) return;
    _code.clear();
    setState(() {
      setup = null;
      codes = [if (r['recovery_codes'] is List) for (final x in r['recovery_codes'] as List) '$x'];
    });
    say(m, c.t('totp.enabled'));
    await load();
  }

  Future<void> _disable() async {
    if (!await ask(c.t('totp.disable_confirm'), 'totp.disable', danger: true)) return;
    final password = _password.text, code = _code.text.trim();
    await save(() => s.post('/api/account/totp/disable', {'password': password, 'code': code}), 'totp.disabled');
    _password.clear();
    _code.clear();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return SettingsCard(c.t('totp.title'), [Busy(c.t('common.loading'))]);
    final on = data['enabled'] == true;
    final st = setup;
    final rc = codes;
    void changed(String _) => setState(() {});
    return SettingsCard(c.t('totp.title'), [
      cardHint(context, c.t('totp.hint')),
      Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Text(totpState(data, c.t), style: TextStyle(color: on ? p.ok : p.muted, fontWeight: FontWeight.w600)),
      ),
      if (rc != null) ...[
        cardHint(context, c.t('totp.recovery_hint')),
        _codeBox(context, rc.join('\n')),
        Wrap(spacing: 10, children: [
          OutlinedButton(onPressed: () => Clipboard.setData(ClipboardData(text: rc.join('\n'))), child: Text(c.t('tokens.copy'))),
          FilledButton(onPressed: () => setState(() => codes = null), child: Text(c.t('totp.recovery_done'))),
        ]),
      ] else if (st != null) ...[
        cardHint(context, c.t('totp.scan')),
        Wrap(spacing: 16, runSpacing: 12, crossAxisAlignment: WrapCrossAlignment.center, children: [
          if ('${st['qr_svg'] ?? ''}'.isNotEmpty) _qr('${st['qr_svg']}'),
          Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text(c.t('totp.secret')),
            const SizedBox(height: 4),
            SelectableText(groupSecret('${st['secret'] ?? ''}'), style: _mono),
          ]),
        ]),
        const SizedBox(height: 14),
        cardField(_code, c.t('totp.code'), formatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9 ]')), LengthLimitingTextInputFormatter(7)], onChanged: changed),
        Wrap(spacing: 10, children: [
          FilledButton(onPressed: busy || _code.text.trim().length < 6 ? null : _confirm, child: Text(c.t('totp.confirm'))),
          TextButton(onPressed: () => setState(() => setup = null), child: Text(c.t('common.cancel'))),
        ]),
      ] else if (!on) ...[
        cardField(_password, c.t('account.old'), obscure: true, onChanged: changed),
        FilledButton(onPressed: busy || _password.text.isEmpty ? null : _begin, child: Text(c.t('totp.enable'))),
      ] else ...[
        cardField(_password, c.t('account.old'), obscure: true, onChanged: changed),
        cardField(_code, c.t('totp.disable_code'), onChanged: changed),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: p.danger, foregroundColor: p.accentFg),
          onPressed: busy || _password.text.isEmpty || _code.text.trim().isEmpty ? null : _disable,
          child: Text(c.t('totp.disable')),
        ),
      ],
    ]);
  }
}

// --- API tokens -----------------------------------------------------------------

class _TokensCard extends StatefulWidget {
  const _TokensCard(this.c);

  final Controller c;

  @override
  State<_TokensCard> createState() => _TokensCardState();
}

class _TokensCardState extends State<_TokensCard> with AreaCalls, CardLoad {
  final _name = TextEditingController();

  /// A token just created, shown once.
  String? created;

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/tokens';

  @override
  void fill(Map data) {}

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/tokens', {'name': _name.text.trim()}), m);
    if (mounted) setState(() => busy = false);
    if (r is! Map) return;
    _name.clear();
    setState(() => created = '${r['token'] ?? ''}');
    await load();
  }

  Future<void> _remove(Map tok) async {
    if (!await ask(c.t('tokens.confirm_remove', {'name': tok['name']}), 'tokens.remove', danger: true)) return;
    setState(() => created = null);
    await save(() => s.post('/api/tokens/${Uri.encodeComponent('${tok['id']}')}/remove'), '');
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return SettingsCard(c.t('tokens.title'), [Busy(c.t('common.loading'))]);
    final list = maps(data['tokens']);
    final fmt = DateFormat.yMd(c.settings.lang).add_Hm();
    String when(Object? sec) => sec is num && sec > 0 ? fmt.format(DateTime.fromMillisecondsSinceEpoch((sec * 1000).round())) : c.t('tokens.never');
    final token = created;
    return SettingsCard(c.t('tokens.title'), [
      cardHint(context, c.t('tokens.hint')),
      if (list.isEmpty) cardHint(context, c.t('tokens.none')),
      for (final tok in list)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Row(children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('${tok['name']}', style: const TextStyle(fontWeight: FontWeight.w600)),
                Text(c.t('tokens.meta', {'created': when(tok['created']), 'used': when(tok['last_used'])}), style: TextStyle(color: p.muted, fontSize: 12.5)),
              ]),
            ),
            TextButton(
              style: TextButton.styleFrom(foregroundColor: p.danger),
              onPressed: busy ? null : () => _remove(tok),
              child: Text(c.t('tokens.remove')),
            ),
          ]),
        ),
      if (token != null) ...[
        const SizedBox(height: 6),
        Text(c.t('tokens.new_hint')),
        const SizedBox(height: 8),
        _codeBox(context, token),
        _codeBox(context, tokenExample(token, c.server?.host ?? '')),
        OutlinedButton(
          onPressed: () async {
            final m = ScaffoldMessenger.of(context);
            await Clipboard.setData(ClipboardData(text: token));
            say(m, c.t('tokens.copied'));
          },
          child: Text(c.t('tokens.copy')),
        ),
        const SizedBox(height: 14),
      ],
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child: cardField(_name, c.t('tokens.name'), hint: c.t('tokens.name_placeholder'), formatters: [LengthLimitingTextInputFormatter(64)], onChanged: (_) => setState(() {})),
        ),
        const SizedBox(width: 12),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: FilledButton(onPressed: busy || _name.text.trim().isEmpty ? null : _create, child: Text(c.t('tokens.create'))),
        ),
      ]),
    ]);
  }
}

// --- dashboard accounts ---------------------------------------------------------

class _UsersCard extends StatefulWidget {
  const _UsersCard(this.c);

  final Controller c;

  @override
  State<_UsersCard> createState() => _UsersCardState();
}

class _UsersCardState extends State<_UsersCard> with AreaCalls, CardLoad {
  final _name = TextEditingController();
  final _password = TextEditingController();
  final _reset = TextEditingController();
  String role = 'viewer';

  /// Account whose password is being set.
  String? resetting;

  @override
  Controller get c => widget.c;

  @override
  String get path => '/api/users';

  @override
  void fill(Map data) {}

  @override
  void dispose() {
    for (final t in [_name, _password, _reset]) {
      t.dispose();
    }
    super.dispose();
  }

  String _user(String name) => '/api/users/${Uri.encodeComponent(name)}';

  Future<void> _add() async {
    final name = _name.text.trim();
    final password = _password.text;
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/users', {'name': name, 'password': password, 'role': role}), m);
    if (mounted) setState(() => busy = false);
    await load();
    if (r is! Map || !mounted) return;
    final shown = displayName(name.toLowerCase());
    // A family account gets an SMB user of the same name and password.
    final smb = r['smb'];
    say(
      m,
      smb == 'created'
          ? '${c.t('users.added', {'name': shown})} ${c.t('users.family_smb', {'name': shown})}'
          : c.t('users.added', {'name': shown}),
    );
    if (smb == 'failed') say(m, c.t('users.family_smb_failed', {'name': shown}), error: true);
    _name.clear();
    _password.clear();
    setState(() {});
  }

  /// Apps a family account sees on its start screen.
  Future<void> _apps(Map u) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.get('/api/apps?catalog=false'), m);
    if (r is! Map || !mounted) return;
    final installed = maps(r['installed']);
    final name = '${u['name']}';
    final chosen = await showDialog<Set<String>>(
      context: context,
      builder: (context) => _AppsDialog(c: c, name: name, installed: installed,
          chosen: {for (final a in u['apps'] is List ? u['apps'] as List : const []) '$a'}),
    );
    if (chosen == null) return;
    final ok = await call(() => s.post('${_user(name)}/apps', {'apps': chosen.toList()..sort()}), m);
    if (ok != null) say(m, c.t('users.apps_saved', {'name': displayName(name)}));
    await load();
  }

  Future<void> _role(Map u, String to) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => s.post('${_user('${u['name']}')}/role', {'role': to}), m);
    if (r != null) say(m, c.t('users.role_saved', {'name': displayName('${u['name']}')}));
    await load();
  }

  Future<void> _setPassword() async {
    final m = ScaffoldMessenger.of(context);
    final name = resetting;
    if (name == null) return;
    final password = _reset.text;
    setState(() => busy = true);
    final r = await call(() => s.post('${_user(name)}/password', {'password': password}), m);
    if (mounted) setState(() => busy = false);
    if (r == null) return;
    if (name == c.user) await c.passwordChanged(password);
    _reset.clear();
    setState(() => resetting = null);
    say(m, c.t('users.reset_done', {'name': displayName(name)}));
  }

  Future<void> _remove(Map u) async {
    final name = '${u['name']}';
    if (!await ask(c.t('users.confirm_remove', {'name': displayName(name)}), 'users.remove', danger: true)) return;
    await save(() => s.post('${_user(name)}/remove'), '');
    if (mounted && !maps(d?['users']).any((x) => x['name'] == name)) {
      say(ScaffoldMessenger.of(context), c.t('users.removed', {'name': displayName(name)}));
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return SettingsCard(c.t('users.title'), [Busy(c.t('common.loading'))]);
    final list = maps(data['users']);
    final roles = [for (final r in ['admin', 'viewer', 'family']) (r, c.t('role_name.$r'))];
    final canAdd = accountNameRe.hasMatch(_name.text.trim()) && _password.text.length >= minPassword;
    return SettingsCard(c.t('users.title'), [
      cardHint(context, c.t('users.hint')),
      SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: DataTable(
          headingRowHeight: 36,
          dataRowMinHeight: 52,
          dataRowMaxHeight: 64,
          horizontalMargin: 0,
          columnSpacing: 24,
          columns: [
            for (final k in ['name', 'role', 'totp']) DataColumn(label: Text(c.t('users.$k'), style: _head(context))),
            const DataColumn(label: SizedBox()),
          ],
          rows: [
            for (final u in list)
              DataRow(cells: [
                DataCell(Text(displayName('${u['name']}'), style: const TextStyle(fontWeight: FontWeight.w600))),
                DataCell(DropdownButton<String>(
                  value: const ['admin', 'family'].contains(u['role']) ? '${u['role']}' : 'viewer',
                  underline: const SizedBox(),
                  borderRadius: BorderRadius.circular(12),
                  items: [for (final (v, text) in roles) DropdownMenuItem(value: v, child: Text(text))],
                  onChanged: (v) => v == null || v == u['role'] ? null : _role(u, v),
                )),
                DataCell(u['totp'] == true ? Tag(c.t('users.totp_on'), p.ok) : const Text('–')),
                DataCell(Row(mainAxisSize: MainAxisSize.min, children: [
                  if (u['role'] == 'family') TextButton(onPressed: () => _apps(u), child: Text(c.t('users.apps'))),
                  TextButton(onPressed: () => setState(() => resetting = '${u['name']}'), child: Text(c.t('users.reset'))),
                  if (u['name'] != c.user)
                    TextButton(
                      style: TextButton.styleFrom(foregroundColor: p.danger),
                      onPressed: busy ? null : () => _remove(u),
                      child: Text(c.t('users.remove')),
                    ),
                ])),
              ]),
          ],
        ),
      ),
      if (resetting != null) ...[
        const SizedBox(height: 14),
        cardHint(context, c.t('users.reset_prompt', {'name': displayName(resetting!)})),
        cardField(_reset, c.t('field.password'), obscure: true, onChanged: (_) => setState(() {})),
        Wrap(spacing: 10, children: [
          FilledButton(onPressed: busy || _reset.text.length < minPassword ? null : _setPassword, child: Text(c.t('users.reset'))),
          TextButton(
            onPressed: () => setState(() {
              resetting = null;
              _reset.clear();
            }),
            child: Text(c.t('common.cancel')),
          ),
        ]),
      ],
      const SizedBox(height: 18),
      cardHint(context, c.t('users.family_hint')),
      Wrap(spacing: 12, crossAxisAlignment: WrapCrossAlignment.start, children: [
        SizedBox(width: 220, child: cardField(_name, c.t('users.name'), formatters: [LengthLimitingTextInputFormatter(32)], onChanged: (_) => setState(() {}))),
        SizedBox(width: 260, child: cardField(_password, c.t('field.password'), obscure: true, onChanged: (_) => setState(() {}))),
        SizedBox(width: 200, child: cardSelect(c.t('users.role'), role, roles, (v) => setState(() => role = v))),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: FilledButton(onPressed: busy || !canAdd ? null : _add, child: Text(c.t('users.add'))),
        ),
      ]),
    ]);
  }
}

/// Check list of the installed apps; pops with the chosen ids.
class _AppsDialog extends StatefulWidget {
  const _AppsDialog({required this.c, required this.name, required this.installed, required this.chosen});

  final Controller c;
  final String name;
  final List<Map> installed;
  final Set<String> chosen;

  @override
  State<_AppsDialog> createState() => _AppsDialogState();
}

class _AppsDialogState extends State<_AppsDialog> {
  late final Set<String> chosen = {...widget.chosen};

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    return AlertDialog(
      title: Text(c.t('users.apps_prompt', {'name': displayName(widget.name)})),
      content: SizedBox(
        width: 420,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            if (widget.installed.isEmpty) Text(c.t('apps.none_installed')),
            for (final a in widget.installed)
              CheckboxListTile(
                value: chosen.contains('${a['id']}'),
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                title: Text('${a['name'] ?? a['id']}'),
                onChanged: (v) => setState(() => v == true ? chosen.add('${a['id']}') : chosen.remove('${a['id']}')),
              ),
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(onPressed: () => Navigator.pop(context, chosen), child: Text(c.t('common.save'))),
      ],
    );
  }
}
