import 'dart:io';

import 'package:dahlia/src/anchor.dart';
import 'package:dahlia/src/pairing.dart';
import 'package:dahlia/src/server.dart';
import 'package:flutter_test/flutter_test.dart';

String fixture(String name) => File('test/fixtures/$name').readAsStringSync();

/// TLS handshake against a local server, checked as if it answered at [host].
Future<String> handshake(int port, String host, List<int> trusted) async {
  final ctx = SecurityContext(withTrustedRoots: false)..setTrustedCertificatesBytes(trusted);
  try {
    final s = await SecureSocket.secure(await Socket.connect(InternetAddress.loopbackIPv4, port), host: host, context: ctx);
    s.destroy();
    return 'ok';
  } on HandshakeException catch (e) {
    return '$e';
  }
}

void main() {
  late SecureServerSocket server;

  setUpAll(() async {
    // CA with the same name constraints as a Dahlia server's
    // (DNS local, the private IPv4 ranges), server certificate for
    // dahlia.local and 192.168.178.58.
    final ctx = SecurityContext()
      ..useCertificateChainBytes(File('test/fixtures/constrained-server.crt').readAsBytesSync())
      ..usePrivateKeyBytes(File('test/fixtures/constrained-server.key').readAsBytesSync());
    server = await SecureServerSocket.bind(InternetAddress.loopbackIPv4, 0, ctx);
    server.listen((s) => s.destroy(), onError: (_) {});
  });

  tearDownAll(() => server.close());

  test('the CA as it is fails in Dart, the anchor works', () async {
    final pem = fixture('constrained-ca.crt');
    expect(await handshake(server.port, '192.168.178.58', pem.codeUnits), contains('name constraint'));
    expect(await handshake(server.port, '192.168.178.58', trustAnchor(pem)), 'ok');
    expect(await handshake(server.port, 'dahlia.local', trustAnchor(pem)), 'ok');
    expect(await handshake(server.port, '192.168.178.99', trustAnchor(pem)), contains('mismatch'));
  });

  test('another CA is still rejected', () async {
    expect(await handshake(server.port, 'dahlia.local', trustAnchor(fixture('other-ca.crt'))), isNot('ok'));
  });

  test('right CA, address not in the certificate', () async {
    final ctx = SecurityContext()
      ..useCertificateChainBytes(File('test/fixtures/constrained-server.crt').readAsBytesSync())
      ..usePrivateKeyBytes(File('test/fixtures/constrained-server.key').readAsBytesSync());
    final http = await HttpServer.bindSecure(InternetAddress.loopbackIPv4, 0, ctx);
    http.listen((r) => r.response
      ..write(fixture('constrained-ca.crt'))
      ..close());
    final host = '127.0.0.1:${http.port}';
    await expectLater(checkPaired(host, fixture('constrained-ca.crt')),
        throwsA(isA<PairingError>().having((e) => e.key, 'key', 'cert_address')));
    await expectLater(checkPaired(host, fixture('other-ca.crt')),
        throwsA(isA<PairingError>().having((e) => e.key, 'key', 'changed')));
    await http.close(force: true);
  });

  test('only nameConstraints is removed', () {
    final der = pemToDer(fixture('constrained-ca.crt'));
    final out = withoutNameConstraints(der);
    expect(out.length, lessThan(der.length));
    expect(withoutNameConstraints(out), out);
    final plain = pemToDer(fixture('ca.crt'));
    expect(withoutNameConstraints(plain), plain);
  });
}
