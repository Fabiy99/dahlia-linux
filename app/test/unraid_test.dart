import 'package:dahlia/src/unraid.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  test('plan lines like the dashboard', () {
    final disk = {'name': 'disk1', 'role': 'data', 'state': 'skip', 'reason': 'encrypted', 'notes': []};
    expect(unraidLine('disks', disk, t),
        'unraid.disk slot=disk1 role=unraid.role_data: unraid.state_skip · unraid.reason_encrypted');
    final app = {
      'name': 'qbittorrent', 'state': 'ready', 'reason': '', 'image': 'lscr.io/linuxserver/qbittorrent:4.6.7',
      'notes': [{'key': 'port_changed', 'value': '8080->8100'}],
    };
    expect(unraidLine('containers', app, t),
        'qbittorrent: unraid.state_ready · unraid.note_port_changed value=8080->8100 · lscr.io/linuxserver/qbittorrent:4.6.7');
    expect(unraidLine('wireguard', {'state': 'ready', 'peers': ['a', 'b']}, t),
        'unraid.wireguard n=2: unraid.state_ready');
  });

  test('marks, sections, tags, visibility', () {
    expect([for (final s in ['ready', 'done', 'confirm', 'needs_tag', 'skip']) unraidMark(s)],
        ['ok', 'ok', 'info', 'info', 'bad']);
    expect(unraidItems({'wireguard': null}, 'wireguard'), isEmpty);
    expect(unraidItems({'disks': [{'name': 'disk1'}]}, 'disks').length, 1);
    expect(validTag('2025.11.3'), isTrue);
    expect(validTag('latest'), isFalse);
    expect(validTag('a b'), isFalse);
    expect(showUnraid({'found': false}), isFalse);
    expect(showUnraid({'found': true, 'dismissed': true, 'job': null}), isFalse);
    expect(showUnraid({'found': true, 'dismissed': true, 'job': {'state': 'done'}}), isTrue);
  });
}
