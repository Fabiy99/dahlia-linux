import 'package:dahlia/src/backup.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  test('units count as active while running or starting', () {
    expect(unitActive({'active': 'activating'}), isTrue);
    expect(unitActive({'active': 'active'}), isTrue);
    expect(unitActive({'active': 'inactive'}), isFalse);
    expect(unitActive(null), isFalse);
  });

  test('target as one line', () {
    expect(targetText({'type': 'folder', 'path': '/mnt/external/usb'}), '/mnt/external/usb');
    expect(targetText({'type': 'sftp', 'host': 'nas', 'port': 22, 'user': 'dahlia', 'path': 'backup'}), 'sftp://dahlia@nas:22/backup');
    expect(targetText(null), '');
  });

  test('a saved folder target that is not mounted stays offered', () {
    final data = {
      'target': {'type': 'folder', 'path': '/mnt/external/old'},
      'candidates': [
        {'path': '/mnt/external/usb', 'avail': 2048}
      ],
    };
    final list = folderChoices(data);
    expect([for (final c in list) c['path']], ['/mnt/external/old', '/mnt/external/usb']);
    expect(choiceLabel(list.first, t), '/mnt/external/old (backup.not_mounted)');
    expect(choiceLabel(list.last, t), startsWith('/mnt/external/usb (backup.free size='));
    expect(folderChoices({'target': {'type': 'folder', 'path': '/mnt/external/usb'}, 'candidates': data['candidates']}).length, 1);
  });

  test('body: folder or SFTP, incomplete targets refused, folders kept', () {
    Map<String, Object>? body(String type, {String folder = '', String host = '', String user = '', String path = '', String port = '22'}) =>
        backupBody(type: type, folder: folder, host: host, port: port, user: user, path: path, stopApps: false, folders: ['/srv/extra']);
    expect(body('folder', folder: '/mnt/external/usb'), {
      'target': {'type': 'folder', 'path': '/mnt/external/usb'},
      'stop_apps': false,
      'folders': ['/srv/extra'],
      'machines': <String>[],
    });
    expect(body('folder'), isNull);
    expect(body('sftp', host: ' nas ', user: 'dahlia', path: 'backup', port: '2222')!['target'],
        {'type': 'sftp', 'host': 'nas', 'port': 2222, 'user': 'dahlia', 'path': 'backup'});
    expect((body('sftp', host: 'nas', user: 'u', path: 'p', port: '')!['target'] as Map)['port'], 22);
    expect(body('sftp', host: 'nas', path: 'p'), isNull, reason: 'SFTP needs a user');
  });

  test('restore choices and times', () {
    expect(restoreChoices([{'id': 'jellyfin', 'name': 'Jellyfin'}], t), [('config', 'backup.what_config'), ('jellyfin', 'Jellyfin')]);
    final withVms = {'paths': ['/etc/dahlia', '/var/lib/dahlia/backup-machines']};
    expect(restoreChoices(const [], t, snapshot: withVms, machines: ['ha']).last, ('vm:ha', 'backup.what_vm name=ha'));
    expect(restoreChoices(const [], t, snapshot: const {'paths': ['/etc/dahlia']}, machines: ['ha']).length, 1);
    expect(backupMachines({'instances': ['web', 'ha'], 'machines': ['old', 'ha']}), ['ha', 'old', 'web']);
    expect(parseTime('2026-11-14T12:00:00Z')!.toUtc(), DateTime.utc(2026, 11, 14, 12));
    expect(parseTime(0), DateTime.fromMillisecondsSinceEpoch(0));
    expect(parseTime(null), isNull);
  });
}
