// Security section of the settings without widgets: firewall and own rules,
// WireGuard and Tailscale, the second factor, dashboard accounts and API
// tokens. Same rules as the dashboard's settings page.

import 'format.dart';

List<Map> maps(Object? v) => v is List ? v.whereType<Map>().toList() : <Map>[];

String _text(Object? v) => v == null ? '' : '$v';

/// Networks that may always reach the server.
String firewallLocal(Map d, Translate t) => t('settings.firewall_local', {
      'networks': [for (final key in ['local', 'subnets']) if (d[key] is List) ...(d[key] as List).map((e) => '$e')].join(', '),
    });

/// Ports open for those networks, or the warning when the firewall is off.
String firewallPorts(Map d, Translate t) {
  if (d['enabled'] != true) return t('settings.firewall_off');
  final ports = [
    for (final p in maps(d['ports']))
      '${p['port']}/${p['protocol']} ${_text(p['service']).startsWith('app:') ? _text(p['service']).substring(4) : t('firewall.${p['service']}')}',
    if (d['wireguard'] is Map) t('settings.firewall_wireguard', {'port': (d['wireguard'] as Map)['port']}),
  ];
  return t('settings.firewall_ports', {'ports': ports.join(', ')});
}

/// Extra networks as typed: separated by commas or spaces.
List<String> splitNetworks(String text) => text.split(RegExp(r'[\s,]+')).where((s) => s.isNotEmpty).toList();

String ruleSource(Map r, Translate t) => r['source'] == 'local' || r['source'] == 'any' ? t('fwrules.${r['source']}') : _text(r['source']);

String rulePorts(Map r, Translate t) => r['protocol'] == 'all' ? t('fwrules.all') : '${r['ports']}/${r['protocol']}';

/// A new own rule from the form; source kind 'net' takes the typed address.
Map<String, String> newRule({required String action, required String protocol, required String ports, required String sourceKind, required String network, required String comment}) => {
      'action': action,
      'protocol': protocol,
      'ports': protocol == 'all' ? '' : ports.trim(),
      'source': sourceKind == 'net' ? network.trim() : sourceKind,
      'comment': comment.trim(),
    };

/// The rules with [i] swapped against [to].
List<Map> moveRule(List<Map> rules, int i, int to) => [
      for (var j = 0; j < rules.length; j++) j == i ? rules[to] : (j == to ? rules[i] : rules[j]),
    ];

/// A unit (install or login job) that is still doing something.
bool unitRunning(Object? s) =>
    s is Map && (const ['activating', 'deactivating'].contains(s['active']) || (s['active'] == 'active' && s['sub'] != 'exited'));

/// Body of `POST /api/vpn/wireguard`; null for a port that is not a number.
Map<String, Object>? wireguardBody({required bool enabled, required String port, required String endpoint, required String route, required String dns, required String subnet}) {
  final p = int.tryParse(port.trim());
  if (p == null) return null;
  return {'enabled': enabled, 'port': p, 'endpoint': endpoint.trim(), 'route': route, 'dns': dns.trim(), 'subnet': subnet.trim()};
}

/// When a peer last connected, null for never.
DateTime? handshake(Map p) => p['handshake'] is num && p['handshake'] != 0
    ? DateTime.fromMillisecondsSinceEpoch(((p['handshake'] as num) * 1000).round())
    : null;

String peerTraffic(Map p, Translate t) =>
    p['rx'] is num && p['tx'] is num ? '↓ ${bytes(p['rx'] as num, t)} ↑ ${bytes(p['tx'] as num, t)}' : '–';

/// File name for a peer's configuration.
String peerFile(String name) => '${name.replaceAll(RegExp(r'[^A-Za-z0-9._-]+'), '-')}.conf';

/// Lines about Tailscale: level ('ok', 'info', 'crit') and text; the login
/// link is returned apart, as it opens in the browser.
(List<(String, String)>, String?) tailscaleLines(Map ts, Translate t) {
  final installing = unitRunning(ts['install_job']);
  final waiting = unitRunning(ts['login_job']);
  final lines = <(String, String)>[];
  String? link;
  if (ts['installed'] != true) {
    lines.add(('info', t(installing ? 'vpn.ts_installing' : 'vpn.ts_not_installed')));
    final job = ts['install_job'];
    if (job is Map && _text(job['result']).isNotEmpty && job['result'] != 'success' && !installing) {
      lines.add(('crit', t('vpn.ts_install_failed')));
    }
  } else if (ts['state'] == 'Running') {
    final tailnet = _text(ts['tailnet']);
    lines.add(('ok', t('vpn.ts_running', {'tailnet': tailnet.isEmpty ? '-' : tailnet})));
    if (_text(ts['dns_name']).isNotEmpty) lines.add(('ok', _text(ts['dns_name'])));
    final addresses = ts['addresses'] is List ? (ts['addresses'] as List).join(', ') : '';
    if (addresses.isNotEmpty) lines.add(('ok', addresses));
    if (ts['routes'] == true) lines.add(('info', t('vpn.ts_routes_hint')));
  } else if (_text(ts['auth_url']).isNotEmpty) {
    lines.add(('info', t('vpn.ts_login')));
    link = _text(ts['auth_url']);
  } else {
    lines.add(('info', t(waiting ? 'vpn.ts_waiting' : 'vpn.ts_logged_out')));
  }
  return (lines, link);
}

/// Tailscale is installing or waits for a login: ask again soon.
bool tailscaleBusy(Map ts) =>
    unitRunning(ts['install_job']) ||
    unitRunning(ts['login_job']) ||
    (ts['installed'] == true && ts['state'] != 'Running' && _text(ts['auth_url']).isNotEmpty);

String totpState(Map d, Translate t) => d['enabled'] == true ? t('totp.on', {'n': d['recovery_left'] ?? 0}) : t('totp.off');

/// The key in groups of four, easier to type.
String groupSecret(String secret) => [for (var i = 0; i < secret.length; i += 4) secret.substring(i, i + 4 > secret.length ? secret.length : i + 4)].join(' ');

/// Same rule as the dashboard's form: letter first, 2 to 32 characters.
final accountNameRe = RegExp(r'^[A-Za-z][A-Za-z0-9_\-]{1,31}$');

const minPassword = 10;

/// Example call with a new token.
String tokenExample(String token, String host) => 'curl -H "Authorization: Bearer $token" https://$host/api/stats';
