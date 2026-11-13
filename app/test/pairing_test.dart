import 'package:dahlia/src/pairing.dart';
import 'package:flutter_test/flutter_test.dart';

const pem = '-----BEGIN CERTIFICATE-----\nAAEC\nAw==\n-----END CERTIFICATE-----\n';

void main() {
  test('host accepts names, IPs and ports', () {
    expect(normalizeHost(' dahlia.local '), 'dahlia.local');
    expect(normalizeHost('https://192.168.1.20/'), '192.168.1.20');
    expect(normalizeHost('nas:8443'), 'nas:8443');
    expect(normalizeHost('NAS.local'), 'nas.local');
    expect(normalizeHost('nas.local:443'), 'nas.local');
    expect(normalizeHost('[fd00::1]'), '[fd00::1]');
  });

  test('host rejects everything else', () {
    for (final bad in ['', 'a/b', 'user@nas', 'nas?x=1', 'nas#x', 'two words', 'http://nas']) {
      expect(() => normalizeHost(bad), throwsA(isA<PairingError>()), reason: bad);
    }
  });

  test('pem and fingerprint', () {
    final der = pemToDer(pem);
    expect(der, [0, 1, 2, 3]);
    expect(
      fingerprint(der),
      '05:4E:DE:C1:D0:21:1F:62:4F:ED:0C:BC:A9:D4:F9:40:0B:0E:49:1C:43:74:2A:F2:C5:B0:AB:EB:F0:C9:90:D8',
    );
    expect(() => pemToDer('no certificate'), throwsA(isA<PairingError>()));
  });

  test('pairing code input', () {
    expect(normalizeCode('abcde-fghjk'), 'ABCDEFGHJK');
    expect(normalizeCode(' ABCDE FGHJK '.trim()), 'ABCDEFGHJK');
    for (final bad in ['', 'ABCDE', 'ABCDE-FGHJ0', 'ABCDE-FGHJKL', 'ÄBCDE-FGHJK']) {
      expect(() => normalizeCode(bad), throwsA(isA<PairingError>()), reason: bad);
    }
  });

  test('proof matches the dashboard', () {
    // tests/test_pairing.py computes the same value with Python's hmac.
    final nonce = List<int>.generate(32, (i) => i);
    final der = List<int>.generate(50, (i) => i);
    final p = proof('ABCDEFGHJK', nonce, der);
    expect(p, 'f613c2eb6eadd6c08d622c35bb99c8980791618551b1d44c250fed92c5e47a46');
    expect(proofMatches(p, proof('ABCDEFGHJK', nonce, der)), isTrue);
    expect(proofMatches(p, proof('ABCDEFGHJL', nonce, der)), isFalse);
    expect(proofMatches(p, proof('ABCDEFGHJK', nonce, der.sublist(1))), isFalse);
    expect(proofMatches(p, ''), isFalse);
  });
}
