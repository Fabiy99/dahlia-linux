import 'package:dahlia/src/system.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

/// Knows the governor descriptions the dashboard has, like the real strings.
String withGovernors(String key, [Map<String, Object?> vars = const {}]) => key == 'power.gov_powersave' ? 'Power saving' : key;

void main() {
  test('server names and following a rename', () {
    expect(hostnameRe.hasMatch('dahlia'), isTrue);
    expect(hostnameRe.hasMatch('nas-2'), isTrue);
    expect(hostnameRe.hasMatch('Dahlia'), isFalse);
    expect(hostnameRe.hasMatch('-nas'), isFalse);
    expect(hostnameRe.hasMatch('a.b'), isFalse);
    expect(renamedHost('dahlia.local', 'dahlia', 'nas'), 'nas.local');
    expect(renamedHost('Dahlia.local:8443', 'dahlia', 'nas'), 'nas.local:8443');
    expect(renamedHost('192.168.1.20', 'dahlia', 'nas'), isNull);
    expect(renamedHost('other.local', 'dahlia', 'nas'), isNull);
    expect(renamedHost('dahlia.local', 'dahlia', 'dahlia'), isNull);
  });

  test('time state follows NTP', () {
    expect(timeState({'ntp': false, 'time': 'x'}, t), 'settings.ntp_off time=x');
    expect(timeState({'ntp': true, 'synchronized': true, 'time': 'x'}, t), 'settings.ntp_synced time=x');
    expect(timeState({'ntp': true, 'synchronized': false, 'time': 'x'}, t), 'settings.ntp_waiting time=x');
  });

  test('network: current addresses, pending change, body', () {
    expect(netCurrent({'iface': 'eth0', 'current': ['10.0.0.5/24'], 'current_gateway': '10.0.0.1'}, t),
        'settings.net_current iface=eth0 address=10.0.0.5/24 gateway=10.0.0.1');
    expect(netCurrent({'iface': 'eth0', 'current': [], 'current_gateway': ''}, t), 'settings.net_current iface=eth0 address=– gateway=–');
    expect(netPending({'pending': 170}), 170);
    expect(netPending({'pending': null}), isNull);
    expect(networkBody(mode: 'dhcp', address: '1.2.3.4/24'), {'mode': 'dhcp'});
    expect(networkBody(mode: 'static', address: ' 10.0.0.5/24 ', gateway: '10.0.0.1', dns: '10.0.0.1'),
        {'mode': 'static', 'address': '10.0.0.5/24', 'gateway': '10.0.0.1', 'dns': '10.0.0.1'});
    expect(networkBody(mode: 'static', address: '10.0.0.5/24', gateway: '', dns: '1.1.1.1'), isNull);
  });

  test('layout summary and body', () {
    expect(layoutSummary({'main': 'eth0', 'nics': ['eth0'], 'bond_mode': '', 'bridge': false, 'vlans': []}, t), 'netlayout.main iface=eth0');
    expect(
      layoutSummary({'main': 'bond0', 'nics': ['eth0', 'eth1'], 'bond_mode': 'active-backup', 'bridge': true, 'vlans': [{'id': 20}, {'id': 30}]}, t),
      'netlayout.main iface=bond0 · netlayout.has_bond ports=eth0, eth1 mode=netlayout.mode_active-backup · netlayout.has_bridge · netlayout.has_vlans ids=20, 30',
    );
    expect(layoutBody(nics: [], bondMode: 'active-backup', bridge: false, vlans: []), isNull);
    expect(layoutBody(nics: ['eth0'], bondMode: 'active-backup', bridge: true, vlans: [Vlan(id: '20', address: '10.0.20.5/24'), Vlan(id: '30', mode: 'manual', address: 'x')]), {
      'nics': ['eth0'],
      'bond_mode': '',
      'bridge': true,
      'vlans': [
        {'id': 20, 'mode': 'static', 'address': '10.0.20.5/24'},
        {'id': 30, 'mode': 'manual'},
      ],
    });
    expect(layoutBody(nics: ['eth0', 'eth1'], bondMode: '802.3ad', bridge: false, vlans: [])!['bond_mode'], '802.3ad');
    final v = Vlan.of({'id': 40, 'mode': 'manual', 'address': ''});
    expect((v.id, v.mode), ('40', 'manual'));
  });

  test('spindown labels and failures', () {
    expect(spindownLabel(-1, t), 'settings.spindown_inherit');
    expect(spindownLabel(0, t), 'settings.spindown_never');
    expect(spindownLabel(30, t), 'settings.minutes n=30');
    expect(spindownLabel(120, t), 'settings.hours n=2');
    expect(spindownFailed({'applied': [], 'failed': []}, t), isNull);
    expect(spindownFailed({'failed': [{'path': '/dev/sdb', 'error': 'not supported'}]}, t), 'settings.spindown_failed disks=/dev/sdb: not supported');
    expect(diskLabel({'path': '/dev/sdb', 'model': '', 'size': null}, t), '/dev/sdb');
  });

  test('governor label, choice and state', () {
    expect(governorLabel('powersave', withGovernors), 'Power saving (powersave)');
    expect(governorLabel('weird', withGovernors), 'weird');
    expect(governorChoice({'available': ['powersave', 'performance'], 'saved': 'performance', 'current': 'powersave'}), 'performance');
    expect(governorChoice({'available': ['powersave', 'performance'], 'saved': null, 'current': 'performance'}), 'performance');
    expect(governorChoice({'available': ['powersave'], 'saved': null, 'current': 'a,b'}), 'powersave');
    expect(powerState({'current': 'powersave', 'driver': 'intel_pstate', 'frequencies': [800, 3400, 1200]}, t),
        'power.current governor=powersave · power.driver driver=intel_pstate · power.mhz min=800 max=3400');
    expect(powerState({'current': null, 'driver': null, 'frequencies': []}, t), 'power.current governor=–');
  });

  test('device rows', () {
    final d = {
      'pci': [
        {'slot': '00:02.0', 'class': 'VGA compatible controller', 'vendor': 'Intel', 'device': 'UHD 630', 'id': '8086:3e92', 'driver': 'i915', 'iommu_group': 1},
        {'slot': '00:1f.0', 'class': 'ISA bridge', 'vendor': '', 'device': '', 'id': null, 'driver': null, 'iommu_group': null},
      ],
      'usb': [
        {'bus': 1, 'device': 1, 'id': '1d6b:0002', 'name': 'root hub', 'hub': true},
        {'bus': 1, 'device': 3, 'id': '051d:0002', 'name': 'APC UPS', 'hub': false},
      ],
    };
    expect(pciRows(d), [
      ['00:02.0', 'VGA compatible controller', 'Intel UHD 630', '8086:3e92', 'i915', '1'],
      ['00:1f.0', 'ISA bridge', '–', '–', '–', '–'],
    ]);
    expect(usbRows(d), [
      ['001:003', 'APC UPS', '051d:0002'],
    ]);
  });

  test('syslog state and body', () {
    expect(syslogState({'enabled': false}, t), 'syslog.state_off');
    expect(syslogState({'enabled': true, 'running': true, 'host': 'nas', 'port': 514}, t), 'syslog.state_on host=nas port=514');
    expect(syslogState({'enabled': true, 'running': false, 'host': 'nas', 'port': 514}, t), 'syslog.state_down host=nas port=514');
    expect(syslogBody(enabled: true, host: ' nas.local ', port: '', protocol: 'tcp', severity: 'warning'),
        {'enabled': true, 'host': 'nas.local', 'port': 514, 'protocol': 'tcp', 'severity': 'warning'});
    expect(syslogBody(enabled: false, host: '', port: '1514', protocol: 'x', severity: 'x'),
        {'enabled': false, 'host': '', 'port': 1514, 'protocol': 'udp', 'severity': 'info'});
  });
}
