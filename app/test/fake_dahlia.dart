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
        r.write(jsonEncode({'user': c.isEmpty ? null : 'fabian', 'version': version}));
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
      case '/app-icons/jellyfin.png':
        r.add([0x89, 0x50, 0x4e, 0x47]);
      default:
        r.statusCode = 404;
    }
    await r.close();
  }
}

