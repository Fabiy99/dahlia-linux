import 'dart:convert';
import 'dart:io';

import 'package:dahlia/src/pairing.dart';

String fixture(String name) => File('test/fixtures/$name').readAsStringSync();

/// A fake Dahlia server: TLS with a certificate from test/fixtures/ca.crt.
class FakeDahlia {
  late HttpServer http;
  String version = '1.4.0';
  final requests = <HttpRequest>[];

  /// Answers of `/api/apps/<id>/job`, one per poll; the last one repeats.
  final jobs = <Map>[];

  /// Body of the last file manager upload, served again by the download.
  List<int> uploaded = const [];

  /// Package names /api/updates/pending offers.
  List<String> pending = const [];

  /// Start screen as /api/home answers it; POST merges the sent parts.
  Map<String, Object?> home = {
    'tiles': [
      {'id': 'weather', 'size': 'm', 'on': true},
      {'id': 'apps', 'size': 'l', 'on': true},
      {'id': 'cpu', 'size': 's', 'on': false},
    ],
    'look': {'theme': 'dark', 'accent': 'teal', 'background': 'still', 'weather_fx': false},
    'places': [],
  };

  /// Role /api/state reports for the signed-in account (null: no role field).
  String? role;

  /// Start screen of a family account (/api/family/home).
  Map<String, Object?> familyHome = {
    'tiles': [],
    'look': {'theme': 'light', 'accent': 'amber', 'background': 'off', 'weather_fx': true},
    'places': [],
  };

  /// Notifications /api/notifications offers.
  List<Map> notices = const [];

  String get host => '127.0.0.1:${http.port}';

  Future<void> start() async {
    final ctx = SecurityContext()
      ..useCertificateChain('test/fixtures/server-chain.crt')
      ..usePrivateKey('test/fixtures/server.key');
    http = await HttpServer.bindSecure(InternetAddress.loopbackIPv4, 0, ctx);
    http.listen(handle);
  }

  Future<void> handle(HttpRequest req) async {
    requests.add(req);
    final r = req.response..headers.set('X-Dahlia-Version', version);
    switch (req.uri.path) {
      case '/dahlia-ca.crt':
        r.write(fixture('ca.crt'));
      case '/api/login':
        final body = jsonDecode(await utf8.decoder.bind(req).join());
        if (body['password'] == 'pw') {
          r.cookies.add(Cookie('dahlia_session', 'tok'));
          r.write('{"user":"fabian"}');
        } else {
          r.statusCode = 401;
          r.write('{"error":"error.invalid_credentials"}');
        }
      case '/api/state':
        final c = req.cookies.where((c) => c.name == 'dahlia_session');
        r.write(jsonEncode({'user': c.isEmpty ? null : 'fabian', 'version': version, 'role': ?(c.isEmpty ? null : role)}));
      case '/api/family':
        r.write(jsonEncode({
          'name': 'mia',
          'host': 'dahlia.local',
          'apps': [
            {'id': 'immich', 'name': 'Immich', 'status': 'running', 'public_port': 2283},
          ],
          'shares': [
            {'name': 'fotos', 'comment': '', 'access': 'rw'},
          ],
          'smb': true,
          'home': familyHome,
        }));
      case '/api/family/home':
        familyHome = {...familyHome, ...jsonDecode(await utf8.decoder.bind(req).join()) as Map<String, Object?>};
        r.write(jsonEncode(familyHome));
      case '/api/pairing/proof':
        final body = jsonDecode(await utf8.decoder.bind(req).join());
        final nonce = [for (var i = 0; i < 64; i += 2) int.parse(body['nonce'].substring(i, i + 2), radix: 16)];
        r.write(jsonEncode({'proof': proof('ABCDEFGHJK', nonce, pemToDer(fixture('ca.crt')))}));
      case final p when p.startsWith('/api/apps/') && p.endsWith('/job'):
        r.write(jsonEncode(jobs.length > 1 ? jobs.removeAt(0) : jobs.first));
      case '/api/files/upload':
        uploaded = await req.expand((c) => c).toList();
        r.write(jsonEncode({'name': req.uri.queryParameters['name'], 'size': uploaded.length}));
      case '/api/files/download':
        if (req.uri.queryParameters['path'] == 'missing') {
          r.statusCode = 404;
          r.write('{"error":"error.not_found","detail":"no such file"}');
        } else {
          r.contentLength = uploaded.length;
          r.add(uploaded);
        }
      case '/api/storage/remove':
        r.statusCode = 422;
        r.write('{"error":"error.agent","detail":"/mnt/cache still holds files, use --force"}');
      case '/api/remotes/nas/remove':
        final body = jsonDecode(await utf8.decoder.bind(req).join());
        if (body['force'] == true) {
          r.write('{"name":"nas"}');
        } else {
          r.statusCode = 422;
          r.write('{"error":"error.agent","detail":"used by apps: jellyfin, sonarr; remove with --force"}');
        }
      case '/api/home':
        if (req.method == 'POST') home = {...home, ...jsonDecode(await utf8.decoder.bind(req).join()) as Map<String, Object?>};
        r.write(jsonEncode(home));
      case '/api/home/reset':
        home = {...home, 'look': {'theme': 'auto', 'accent': 'dahlia', 'background': 'bloom', 'weather_fx': true}};
        r.write(jsonEncode(home));
      case '/api/notifications':
        r.write(jsonEncode({'items': notices}));
      case '/api/updates/pending':
        r.write(jsonEncode({'packages': [for (final n in pending) {'name': n}], 'running': false}));
      case '/app-icons/jellyfin.png':
        r.add([0x89, 0x50, 0x4e, 0x47]);
      default:
        r.statusCode = 404;
    }
    await r.close();
  }
}

