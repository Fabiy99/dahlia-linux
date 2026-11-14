// System section of the settings without widgets: server name, time,
// network and its layout, disk spindown, CPU governor, devices and remote
// syslog. Same rules as the dashboard's settings page.

import 'format.dart';

List<Map> _maps(Object? v) => v is List ? v.whereType<Map>().toList() : <Map>[];

String _text(Object? v) => v == null ? '' : '$v';

/// Same rule as the agent: one DNS label, lower case.
final hostnameRe = RegExp(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$');

/// The address the app reaches the server at after a rename, null when it
/// does not use the old .local name (an IP address or another name stays).
String? renamedHost(String host, String oldName, String newName) {
  final h = host.toLowerCase();
  final old = '${oldName.toLowerCase()}.local';
  if (oldName.isEmpty || oldName == newName) return null;
  if (h == old) return '$newName.local';
  if (h.startsWith('$old:')) return '$newName.local${h.substring(old.length)}';
  return null;
}

/// Line below the time settings: NTP state and the server's clock.
String timeState(Map d, Translate t) {
  var key = 'settings.ntp_off';
  if (d['ntp'] == true) key = d['synchronized'] == true ? 'settings.ntp_synced' : 'settings.ntp_waiting';
  return t(key, {'time': _text(d['time'])});
}

List<String> textList(Object? v) => [if (v is List) for (final x in v) '$x'];

/// Current addresses of the main interface.
String netCurrent(Map d, Translate t) {
  final current = textList(d['current']);
  final gw = _text(d['current_gateway']);
  return t('settings.net_current', {
    'iface': _text(d['iface']),
    'address': current.isEmpty ? '–' : current.join(', '),
    'gateway': gw.isEmpty ? '–' : gw,
  });
}

/// Seconds until a network change is reverted, null when none waits.
int? netPending(Map d) => d['pending'] is num ? (d['pending'] as num).toInt() : null;

/// Body of `POST /api/settings/network`; null when a static address lacks a field.
Map<String, String>? networkBody({required String mode, String address = '', String gateway = '', String dns = ''}) {
  if (mode != 'static') return {'mode': 'dhcp'};
  final a = address.trim(), g = gateway.trim(), n = dns.trim();
  if (a.isEmpty || g.isEmpty || n.isEmpty) return null;
  return {'mode': 'static', 'address': a, 'gateway': g, 'dns': n};
}

/// One line about the configured layout: main interface, bond, bridge, VLANs.
String layoutSummary(Map d, Translate t) {
  final vlans = _maps(d['vlans']);
  return [
    t('netlayout.main', {'iface': _text(d['main'])}),
    if (_text(d['bond_mode']).isNotEmpty)
      t('netlayout.has_bond', {'ports': textList(d['nics']).join(', '), 'mode': t('netlayout.mode_${d['bond_mode']}')}),
    if (d['bridge'] == true) t('netlayout.has_bridge'),
    if (vlans.isNotEmpty) t('netlayout.has_vlans', {'ids': vlans.map((v) => v['id']).join(', ')}),
  ].join(' · ');
}

/// A VLAN as edited in the form.
class Vlan {
  Vlan({this.id = '', this.mode = 'static', this.address = ''});

  factory Vlan.of(Map v) => Vlan(id: _text(v['id']), mode: v['mode'] == 'manual' ? 'manual' : 'static', address: _text(v['address']));

  String id;
  String mode;
  String address;
}

/// Body of `POST /api/settings/network/layout`; null without a port. A bond
/// mode is sent only for two or more ports, an address only for static VLANs.
Map<String, Object>? layoutBody({required List<String> nics, required String bondMode, required bool bridge, required List<Vlan> vlans}) {
  if (nics.isEmpty) return null;
  return {
    'nics': nics,
    'bond_mode': nics.length > 1 ? bondMode : '',
    'bridge': bridge,
    'vlans': [
      for (final v in vlans)
        {
          'id': int.tryParse(v.id.trim()) ?? 0,
          'mode': v.mode,
          if (v.mode == 'static') 'address': v.address.trim(),
        },
    ],
  };
}

/// Spindown choice: -1 the default, 0 never, minutes or hours.
String spindownLabel(int m, Translate t) {
  if (m == -1) return t('settings.spindown_inherit');
  if (m == 0) return t('settings.spindown_never');
  return m < 60 ? t('settings.minutes', {'n': m}) : t('settings.hours', {'n': m ~/ 60});
}

List<int> intList(Object? v) => [if (v is List) for (final x in v) if (x is num) x.toInt()];

/// Label of a spinning disk: path, model, size.
String diskLabel(Map d, Translate t) =>
    [_text(d['path']), _text(d['model']), if (d['size'] is num) bytes(d['size'] as num, t)].where((s) => s.isNotEmpty).join(' · ');

/// Message after saving spindown: null when every disk took it.
String? spindownFailed(Object? r, Translate t) {
  final failed = _maps(r is Map ? r['failed'] : null);
  if (failed.isEmpty) return null;
  return t('settings.spindown_failed', {'disks': failed.map((f) => '${f['path']}: ${f['error']}').join('\n')});
}

/// Governor name with its description, as the dashboard shows it.
String governorLabel(String g, Translate t) {
  final key = 'power.gov_$g';
  final text = t(key);
  return text == key ? g : '$text ($g)';
}

/// The governor preselected: the saved one, else the current, else the first.
String governorChoice(Map d) {
  final available = textList(d['available']);
  final saved = _text(d['saved']);
  if (saved.isNotEmpty) return saved;
  final current = _text(d['current']);
  if (available.contains(current)) return current;
  return available.isEmpty ? '' : available.first;
}

/// Current governor, driver and clock range.
String powerState(Map d, Translate t) {
  final mhz = intList(d['frequencies']);
  final current = _text(d['current']);
  return [
    t('power.current', {'governor': current.isEmpty ? '–' : current}),
    if (_text(d['driver']).isNotEmpty) t('power.driver', {'driver': d['driver']}),
    if (mhz.isNotEmpty) t('power.mhz', {'min': mhz.reduce((a, b) => a < b ? a : b), 'max': mhz.reduce((a, b) => a > b ? a : b)}),
  ].join(' · ');
}

String _cell(Object? v) => v == null || '$v'.isEmpty ? '–' : '$v';

/// PCI rows: slot, type, device, ID, driver, IOMMU group.
List<List<String>> pciRows(Map d) => [
      for (final p in _maps(d['pci']))
        [
          _cell(p['slot']),
          _cell(p['class']),
          _cell([_text(p['vendor']), _text(p['device'])].where((s) => s.isNotEmpty).join(' ')),
          _cell(p['id']),
          _cell(p['driver']),
          _cell(p['iommu_group']),
        ],
    ];

/// USB rows without the root hubs: bus:device, name, ID.
List<List<String>> usbRows(Map d) => [
      for (final u in _maps(d['usb']))
        if (u['hub'] != true) ['${_text(u['bus']).padLeft(3, '0')}:${_text(u['device']).padLeft(3, '0')}', _cell(u['name']), _cell(u['id'])],
    ];

const syslogSeverities = ['debug', 'info', 'notice', 'warning', 'err'];

String syslogState(Map d, Translate t) {
  if (d['enabled'] != true) return t('syslog.state_off');
  return t(d['running'] == true ? 'syslog.state_on' : 'syslog.state_down', {'host': _text(d['host']), 'port': _text(d['port'])});
}

/// Body of `POST /api/settings/syslog`; an empty port is 514, the agent checks the rest.
Map<String, Object> syslogBody({required bool enabled, required String host, required String port, required String protocol, required String severity}) {
  return {
    'enabled': enabled,
    'host': host.trim(),
    'port': int.tryParse(port.trim()) ?? 514,
    'protocol': protocol == 'tcp' ? 'tcp' : 'udp',
    'severity': syslogSeverities.contains(severity) ? severity : 'info',
  };
}
