import 'dart:convert';
import 'dart:io';

import 'package:dahlia/src/openpgp.dart';
import 'package:flutter_test/flutter_test.dart';

String fixture(String name) => File('test/fixtures/$name').readAsStringSync();

void main() {
  final data = File('test/fixtures/manifest.json').readAsBytesSync();
  final key = fixture('test-key.asc');

  test('archive key has one Ed25519 key', () {
    expect(ed25519Keys(File('assets/dahlia-archive-keyring.asc').readAsStringSync()), hasLength(1));
  });

  test('accepts gpg signatures over SHA-512 and SHA-256', () async {
    expect(await verifyDetached(data, fixture('manifest.json.sig'), key), isTrue);
    expect(await verifyDetached(data, fixture('manifest.json.sha256.sig'), key), isTrue);
  });

  test('rejects changed data, other keys and garbage', () async {
    final changed = [...data]..[2] ^= 1;
    expect(await verifyDetached(changed, fixture('manifest.json.sig'), key), isFalse);
    expect(await verifyDetached(data, fixture('manifest.json.sig'), fixture('other-key.asc')), isFalse);
    expect(await verifyDetached(data, 'nonsense', key), isFalse);
    expect(await verifyDetached(data, fixture('manifest.json.sig'), 'nonsense'), isFalse);
    // Last byte of the signature value flipped, armor without CRC line.
    final raw = dearmor(fixture('manifest.json.sig'));
    raw[raw.length - 1] ^= 1;
    final sig = '-----BEGIN PGP SIGNATURE-----\n\n${base64.encode(raw)}\n-----END PGP SIGNATURE-----\n';
    expect(await verifyDetached(data, sig, key), isFalse);
    raw[raw.length - 1] ^= 1;
    final good = '-----BEGIN PGP SIGNATURE-----\n\n${base64.encode(raw)}\n-----END PGP SIGNATURE-----\n';
    expect(await verifyDetached(data, good, key), isTrue);
  });
}
