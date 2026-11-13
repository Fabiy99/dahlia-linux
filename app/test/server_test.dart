
import 'package:dahlia/src/pairing.dart';
import 'package:dahlia/src/server.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_dahlia.dart';

void main() {
  late FakeDahlia fake;
  setUp(() async {
    fake = FakeDahlia();
    await fake.start();
  });
  tearDown(() => fake.http.close(force: true));

  test('pairing: CA download, proof, pinned check', () async {
    final pem = await fetchCa(fake.host);
    expect(fingerprint(pemToDer(pem)), fingerprint(pemToDer(fixture('ca.crt'))));
    final nonce = newNonce();
    final got = await requestProof(fake.host, nonce);
    expect(proofMatches(proof('ABCDEFGHJK', nonce, pemToDer(pem)), got), isTrue);
    await checkPaired(fake.host, pem);
    await expectLater(
      checkPaired(fake.host, fixture('other-ca.crt')),
      throwsA(isA<PairingError>().having((e) => e.key, 'key', 'changed')),
    );
    await expectLater(
      checkPaired('127.0.0.1:1', pem),
      throwsA(isA<PairingError>().having((e) => e.key, 'key', 'unreachable')),
    );
  });

  test('session cookie and errors', () async {
    final s = Server(host: fake.host, pem: fixture('ca.crt'), appVersion: '1.4.0');
    expect((await s.get('/api/state'))['user'], isNull);
    await expectLater(
      s.post('/api/login', {'username': 'fabian', 'password': 'x'}),
      throwsA(isA<ApiError>().having((e) => e.key, 'key', 'error.invalid_credentials')),
    );
    await s.post('/api/login', {'username': 'fabian', 'password': 'pw'});
    expect((await s.get('/api/state'))['user'], 'fabian');
    expect(fake.requests.every((r) => r.headers.value('X-Dahlia-Request') == '1'), isTrue);
    s.close();
  });

  test('another server version stops every call', () async {
    final seen = <String>[];
    final s = Server(host: fake.host, pem: fixture('ca.crt'), appVersion: '1.4.0', onVersion: seen.add);
    await s.get('/api/state');
    fake.version = '1.5.0';
    await expectLater(s.get('/api/state'), throwsA(isA<VersionMismatch>()));
    expect(seen, ['1.5.0']);
    expect(s.serverVersion, '1.5.0');
    final dev = Server(host: fake.host, pem: fixture('ca.crt'), appVersion: 'dev');
    expect((await dev.get('/api/state'))['version'], '1.5.0');
    s.close();
    dev.close();
  });

  test('the pinned client refuses other CAs', () async {
    final s = Server(host: fake.host, pem: fixture('other-ca.crt'), appVersion: '1.4.0');
    await expectLater(s.get('/api/state'), throwsA(isA<ApiError>().having((e) => e.status, 'status', 0)));
    s.close();
  });
}
