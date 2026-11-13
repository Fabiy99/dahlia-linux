import 'package:dahlia/src/apps.dart';
import 'package:dahlia/src/server.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_dahlia.dart';

void main() {
  test('paths and folder of a share', () {
    expect(fullPath('/mnt/user', ''), '/mnt/user');
    expect(fullPath('/srv', 'media/films'), '/srv/media/films');
    expect(joinPath('', 'media'), 'media');
    expect(joinPath('media', 'films'), 'media/films');
    expect(const Place('/mnt/user', 'media/films').parent, 'media');
    expect(const Place('/mnt/user', 'media').parent, '');
    expect(defaultLocation(true), arrayRoot);
    expect(defaultLocation(false), systemRoot);
  });

  test('install body sends only changed folders', () {
    final chosen = {
      'media': const Place('/mnt/user', 'media'),
      'downloads': const Place('/mnt/user', 'stuff/dl'),
      'config': const Place('/srv', 'config'),
    };
    expect(installBody('/mnt/user', chosen), {
      'location': '/mnt/user',
      'folders': {'downloads': '/mnt/user/stuff/dl', 'config': '/srv/config'},
    });
    // With the location on the SSD, a folder kept on the array is a change.
    expect(installBody('/srv', chosen)['folders'], {
      'media': '/mnt/user/media',
      'downloads': '/mnt/user/stuff/dl',
    });
    expect(installBody('/srv', {}), {'location': '/srv', 'folders': <String, String>{}});
  });

  test('catalog search, categories and texts', () {
    final catalog = <Map>[
      {'id': 'jellyfin', 'name': 'Jellyfin', 'category': 'media', 'description': {'en': 'Films and series', 'de': 'Filme und Serien'}},
      {'id': 'immich', 'name': 'Immich', 'category': 'photos', 'description': {'en': 'Photo backup'}},
      {'id': 'plex', 'name': 'Plex', 'category': 'media', 'description': {'en': 'Media server'}},
    ];
    List<String> ids(List<Map> l) => [for (final a in l) '${a['id']}'];
    expect(ids(filterCatalog(catalog, '', '', 'en')), ['jellyfin', 'immich', 'plex']);
    expect(ids(filterCatalog(catalog, '', 'media', 'en')), ['jellyfin', 'plex']);
    expect(ids(filterCatalog(catalog, ' FILME ', '', 'de')), ['jellyfin']);
    expect(ids(filterCatalog(catalog, 'photo', '', 'de')), ['immich'], reason: 'falls back to English');
    expect(ids(filterCatalog(catalog, 'photo', 'media', 'en')), isEmpty);
    expect(categories(catalog), {'media': 2, 'photos': 1});
    expect(localized({'en': 'a', 'de': 'b'}, 'de'), 'b');
    expect(localized(null, 'de'), '');
  });

  test('helpers, ports and picker folders', () {
    final app = {
      'services': [
        {'name': 'app', 'image': 'nextcloud:30'},
        {'name': 'db', 'image': 'postgres:16'},
        {'name': 'redis', 'image': 'redis:7'},
      ],
      'lan_ports': [
        {'host': 53, 'protocol': 'udp'},
        {'host': 8080, 'protocol': 'tcp'},
      ],
    };
    expect(helpers(app), 'postgres:16, redis:7');
    expect(lanPorts(app), '53/udp, 8080/tcp');
    expect(helpers({}), '');
    expect(pickableDirs(['appdata', 'media'], ''), ['media']);
    expect(pickableDirs(['appdata', 'media'], 'media'), ['appdata', 'media']);
  });

  test('job is followed until it ends', () async {
    final fake = FakeDahlia();
    await fake.start();
    addTearDown(() => fake.http.close(force: true));
    final s = Server(host: fake.host, pem: fixture('ca.crt'), appVersion: '1.4.0');
    fake.jobs.addAll([
      {'id': 'jellyfin', 'active': 'activating'},
      {'id': 'jellyfin', 'active': 'active'},
      {'id': 'jellyfin', 'active': 'inactive', 'result': 'success'},
    ]);
    var ticks = 0;
    final job = await waitForJob(s, 'jellyfin', every: Duration.zero, tick: () => ticks++);
    expect(job?['result'], 'success');
    expect(ticks, 3);
    void result(Map? job, String op, String key, Map<String, Object?> vars) {
      final (k, v) = jobResult(job, op, 'jellyfin', 'Jellyfin');
      expect(k, key);
      expect(v, vars);
    }

    result(job, 'install', 'apps.install_done', {'name': 'Jellyfin'});

    fake.jobs
      ..clear()
      ..add({'active': 'active'});
    expect(await waitForJob(s, 'jellyfin', every: Duration.zero, limit: 2), isNull);
    result(null, 'update', 'apps.job_failed', {'name': 'Jellyfin', 'unit': 'dahlia-app-jellyfin'});
    result({'result': 'exit-code', 'error': 'pull failed'}, 'update', 'apps.job_failed_reason',
        {'name': 'Jellyfin', 'error': 'pull failed'});
  });

  test('icon bytes; missing file is null', () async {
    final fake = FakeDahlia();
    await fake.start();
    addTearDown(() => fake.http.close(force: true));
    final s = Server(host: fake.host, pem: fixture('ca.crt'), appVersion: '1.4.0');
    expect(await s.bytes('/app-icons/jellyfin.png'), [0x89, 0x50, 0x4e, 0x47]);
    expect(await s.bytes('/app-icons/none.png'), isNull);
    expect(await s.bytes('/app-icons/jellyfin.png', max: 2), isNull);
  });
}
