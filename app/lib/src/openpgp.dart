// Verifies detached OpenPGP signatures (RFC 4880, v4) made with the Ed25519
// key of the Dahlia apt repository (tools/sign-app-manifests.sh). Only what
// gpg produces for that key is accepted: armored public key with EdDSA
// (algorithm 22, curve Ed25519) and binary-document signatures over SHA-256
// or SHA-512.

import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:cryptography/cryptography.dart';

const _ed25519Oid = [0x2B, 0x06, 0x01, 0x04, 0x01, 0xDA, 0x47, 0x0F, 0x01];
const _algoEdDsa = 22;
const _hashSha256 = 8;
const _hashSha512 = 10;

class _Packet {
  _Packet(this.tag, this.body);
  final int tag;
  final Uint8List body;
}

/// Base64 body of an ASCII-armored block; the CRC line is checked if present.
Uint8List dearmor(String text) {
  final lines = const LineSplitter().convert(text).map((l) => l.trim()).toList();
  final start = lines.indexWhere((l) => l.startsWith('-----BEGIN PGP '));
  if (start < 0) throw const FormatException('armor');
  final blank = lines.indexWhere((l) => l.isEmpty, start);
  final end = lines.indexWhere((l) => l.startsWith('-----END PGP '), start);
  if (blank < 0 || end < 0 || blank > end) throw const FormatException('armor');
  final body = StringBuffer();
  String? crc;
  for (final l in lines.sublist(blank + 1, end)) {
    if (l.startsWith('=') && l.length == 5) {
      crc = l.substring(1);
    } else {
      body.write(l);
    }
  }
  final data = base64.decode(body.toString());
  if (crc != null) {
    final c = _crc24(data);
    final want = base64.decode(crc);
    if (want.length != 3 ||
        want[0] != (c >> 16) & 0xff ||
        want[1] != (c >> 8) & 0xff ||
        want[2] != c & 0xff) {
      throw const FormatException('crc');
    }
  }
  return data;
}

int _crc24(List<int> data) {
  var crc = 0xB704CE;
  for (final b in data) {
    crc ^= b << 16;
    for (var i = 0; i < 8; i++) {
      crc <<= 1;
      if (crc & 0x1000000 != 0) crc ^= 0x1864CFB;
    }
  }
  return crc & 0xFFFFFF;
}

List<_Packet> _packets(Uint8List data) {
  final out = <_Packet>[];
  var i = 0;
  while (i < data.length) {
    final h = data[i++];
    if (h & 0x80 == 0) throw const FormatException('packet');
    int tag;
    int len;
    if (h & 0x40 != 0) {
      tag = h & 0x3f;
      final a = data[i++];
      if (a < 192) {
        len = a;
      } else if (a < 224) {
        len = ((a - 192) << 8) + data[i++] + 192;
      } else if (a == 255) {
        len = (data[i] << 24) | (data[i + 1] << 16) | (data[i + 2] << 8) | data[i + 3];
        i += 4;
      } else {
        throw const FormatException('partial length');
      }
    } else {
      tag = (h >> 2) & 0x0f;
      switch (h & 3) {
        case 0:
          len = data[i];
          i += 1;
        case 1:
          len = (data[i] << 8) | data[i + 1];
          i += 2;
        case 2:
          len = (data[i] << 24) | (data[i + 1] << 16) | (data[i + 2] << 8) | data[i + 3];
          i += 4;
        default:
          throw const FormatException('indeterminate length');
      }
    }
    if (i + len > data.length) throw const FormatException('truncated');
    out.add(_Packet(tag, Uint8List.sublistView(data, i, i + len)));
    i += len;
  }
  return out;
}

/// Reads an MPI at [pos]; returns the value bytes and the next position.
(Uint8List, int) _mpi(Uint8List b, int pos) {
  final bits = (b[pos] << 8) | b[pos + 1];
  final n = (bits + 7) >> 3;
  final end = pos + 2 + n;
  if (end > b.length) throw const FormatException('mpi');
  return (Uint8List.sublistView(b, pos + 2, end), end);
}

bool _sameBytes(List<int> a, List<int> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

/// The Ed25519 public keys (primary and subkeys) of an armored public key.
List<Uint8List> ed25519Keys(String armored) {
  final keys = <Uint8List>[];
  for (final p in _packets(dearmor(armored))) {
    if (p.tag != 6 && p.tag != 14) continue;
    final b = p.body;
    if (b.length < 7 || b[0] != 4 || b[5] != _algoEdDsa) continue;
    final oidLen = b[6];
    if (!_sameBytes(Uint8List.sublistView(b, 7, 7 + oidLen), _ed25519Oid)) continue;
    final (point, _) = _mpi(b, 7 + oidLen);
    if (point.length == 33 && point[0] == 0x40) {
      keys.add(Uint8List.fromList(point.sublist(1)));
    }
  }
  return keys;
}

Uint8List _pad32(Uint8List v) {
  if (v.length > 32) throw const FormatException('signature');
  return Uint8List(32)..setRange(32 - v.length, 32, v);
}

/// True if [signature] (armored, detached) is a valid signature over [data]
/// by one of the Ed25519 keys in [publicKey] (armored).
Future<bool> verifyDetached(List<int> data, String signature, String publicKey) async {
  final List<Uint8List> keys;
  final List<_Packet> packets;
  try {
    keys = ed25519Keys(publicKey);
    packets = _packets(dearmor(signature));
  } on FormatException {
    return false;
  } on RangeError {
    return false;
  }
  for (final p in packets.where((p) => p.tag == 2)) {
    try {
      if (await _verifyOne(data, p.body, keys)) return true;
    } on FormatException {
      continue;
    } on RangeError {
      continue;
    }
  }
  return false;
}

Future<bool> _verifyOne(List<int> data, Uint8List s, List<Uint8List> keys) async {
  // v4, binary document, EdDSA.
  if (s[0] != 4 || s[1] != 0x00 || s[2] != _algoEdDsa) return false;
  final hashAlgo = s[3];
  final hashedLen = (s[4] << 8) | s[5];
  final trailerEnd = 6 + hashedLen;
  final unhashedLen = (s[trailerEnd] << 8) | s[trailerEnd + 1];
  var pos = trailerEnd + 2 + unhashedLen;
  final left16 = Uint8List.sublistView(s, pos, pos + 2);
  pos += 2;
  final (r, p2) = _mpi(s, pos);
  final (sv, _) = _mpi(s, p2);

  final trailer = <int>[
    0x04, 0xFF,
    (trailerEnd >> 24) & 0xff, (trailerEnd >> 16) & 0xff,
    (trailerEnd >> 8) & 0xff, trailerEnd & 0xff,
  ];
  final input = <int>[...data, ...s.sublist(0, trailerEnd), ...trailer];
  final List<int> digest;
  if (hashAlgo == _hashSha512) {
    digest = sha512.convert(input).bytes;
  } else if (hashAlgo == _hashSha256) {
    digest = sha256.convert(input).bytes;
  } else {
    return false;
  }
  if (digest[0] != left16[0] || digest[1] != left16[1]) return false;

  final sig = [..._pad32(r), ..._pad32(sv)];
  final ed = Ed25519();
  for (final k in keys) {
    final ok = await ed.verify(
      digest,
      signature: Signature(sig, publicKey: SimplePublicKey(k, type: KeyPairType.ed25519)),
    );
    if (ok) return true;
  }
  return false;
}
