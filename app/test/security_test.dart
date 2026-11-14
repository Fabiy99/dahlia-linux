import 'package:dahlia/src/security.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  test('firewall lines', () {
    final d = {
      'enabled': true,
      'local': ['10.0.0.0/8'],
      'subnets': ['192.168.178.0/24'],
      'ports': [
        {'port': 22, 'protocol': 'tcp', 'service': 'ssh'},
        {'port': 8096, 'protocol': 'tcp', 'service': 'app:jellyfin'},
      ],
      'wireguard': {'port': 51820},
    };
    expect(firewallLocal(d, t), 'settings.firewall_local networks=10.0.0.0/8, 192.168.178.0/24');
    expect(firewallPorts(d, t),
        'settings.firewall_ports ports=22/tcp firewall.ssh, 8096/tcp jellyfin, settings.firewall_wireguard port=51820');
    expect(firewallPorts({'enabled': false}, t), 'settings.firewall_off');
    expect(splitNetworks(' 10.8.0.0/24,192.168.50.0/24  10.9.0.0/24 '), ['10.8.0.0/24', '192.168.50.0/24', '10.9.0.0/24']);
    expect(splitNetworks(''), isEmpty);
  });

  test('own rules', () {
    expect(ruleSource({'source': 'any'}, t), 'fwrules.any');
    expect(ruleSource({'source': '192.168.1.20'}, t), '192.168.1.20');
    expect(rulePorts({'protocol': 'all'}, t), 'fwrules.all');
    expect(rulePorts({'protocol': 'udp', 'ports': '27015-27030'}, t), '27015-27030/udp');
    expect(newRule(action: 'drop', protocol: 'all', ports: '80', sourceKind: 'net', network: ' 192.168.1.9 ', comment: ' TV '),
        {'action': 'drop', 'protocol': 'all', 'ports': '', 'source': '192.168.1.9', 'comment': 'TV'});
    expect(newRule(action: 'accept', protocol: 'tcp', ports: '25565', sourceKind: 'local', network: 'x', comment: '')['source'], 'local');
    final rules = [{'n': 1}, {'n': 2}, {'n': 3}];
    expect([for (final r in moveRule(rules, 2, 1)) r['n']], [1, 3, 2]);
    expect([for (final r in moveRule(rules, 0, 1)) r['n']], [2, 1, 3]);
  });

  test('WireGuard', () {
    expect(unitRunning({'active': 'activating', 'sub': 'start'}), isTrue);
    expect(unitRunning({'active': 'active', 'sub': 'running'}), isTrue);
    expect(unitRunning({'active': 'active', 'sub': 'exited'}), isFalse);
    expect(unitRunning(null), isFalse);
    expect(wireguardBody(enabled: true, port: ' 51820 ', endpoint: ' home.example ', route: 'lan', dns: '', subnet: '10.8.0.0/24'),
        {'enabled': true, 'port': 51820, 'endpoint': 'home.example', 'route': 'lan', 'dns': '', 'subnet': '10.8.0.0/24'});
    expect(wireguardBody(enabled: true, port: '', endpoint: '', route: 'lan', dns: '', subnet: ''), isNull);
    expect(handshake({'handshake': 0}), isNull);
    expect(handshake({'handshake': 1700000000}), DateTime.fromMillisecondsSinceEpoch(1700000000000));
    expect(peerTraffic({}, t), '–');
    expect(peerFile('Fabians Handy/1'), 'Fabians-Handy-1.conf');
  });

  test('Tailscale lines and polling', () {
    expect(tailscaleLines({'installed': false, 'install_job': {'active': 'activating'}}, t).$1, [('info', 'vpn.ts_installing')]);
    expect(tailscaleLines({'installed': false, 'install_job': {'active': 'failed', 'result': 'exit-code'}}, t).$1,
        [('info', 'vpn.ts_not_installed'), ('crit', 'vpn.ts_install_failed')]);
    final running = tailscaleLines({'installed': true, 'state': 'Running', 'tailnet': 'fabi.ts.net', 'dns_name': 'dahlia.fabi.ts.net', 'addresses': ['100.64.0.1'], 'routes': true}, t);
    expect(running.$1, [
      ('ok', 'vpn.ts_running tailnet=fabi.ts.net'),
      ('ok', 'dahlia.fabi.ts.net'),
      ('ok', '100.64.0.1'),
      ('info', 'vpn.ts_routes_hint'),
    ]);
    expect(running.$2, isNull);
    final login = tailscaleLines({'installed': true, 'state': 'NeedsLogin', 'auth_url': 'https://login.tailscale.com/a/x'}, t);
    expect(login.$2, 'https://login.tailscale.com/a/x');
    expect(tailscaleBusy({'installed': true, 'state': 'NeedsLogin', 'auth_url': 'https://login.tailscale.com/a/x'}), isTrue);
    expect(tailscaleBusy({'installed': true, 'state': 'Running', 'auth_url': null}), isFalse);
  });

  test('second factor, accounts, tokens', () {
    expect(totpState({'enabled': true, 'recovery_left': 8}, t), 'totp.on n=8');
    expect(totpState({'enabled': false}, t), 'totp.off');
    expect(groupSecret('JBSWY3DPEHPK3PXPA'), 'JBSW Y3DP EHPK 3PXP A');
    expect(accountNameRe.hasMatch('anna'), isTrue);
    expect(accountNameRe.hasMatch('a'), isFalse);
    expect(accountNameRe.hasMatch('1anna'), isFalse);
    expect(tokenExample('abc', 'dahlia.local'), 'curl -H "Authorization: Bearer abc" https://dahlia.local/api/stats');
  });
}
