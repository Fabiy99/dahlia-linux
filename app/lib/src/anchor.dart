// Trust anchor for the pinned Dahlia CA.
//
// The server's CA carries name constraints (DNS "local" and the private IPv4
// ranges) so that browsers which import it trust it for nothing else. The
// BoringSSL in Dart rejects IP address constraints ("unsupported name
// constraint type") and with them every certificate of the server. The app
// trusts this CA for one server only, so the constraints add nothing here:
// the anchor is the same certificate with the nameConstraints extension
// removed. Subject, key and key identifier stay; a trust anchor's own
// signature is not checked, so the old signature can stay as well.

import 'dart:convert';
import 'dart:typed_data';

import 'pairing.dart';

const _nameConstraintsOid = [0x06, 0x03, 0x55, 0x1d, 0x1e];

class _Tlv {
  _Tlv(this.tag, this.body, this.end);

  final int tag;
  final Uint8List body;
  final int end;
}

_Tlv _read(Uint8List b, int at) {
  final tag = b[at];
  var len = b[at + 1];
  var p = at + 2;
  if (len & 0x80 != 0) {
    final n = len & 0x7f;
    if (n == 0 || n > 3) throw const FormatException('DER length');
    len = 0;
    for (var i = 0; i < n; i++) {
      len = (len << 8) | b[p++];
    }
  }
  if (p + len > b.length) throw const FormatException('DER truncated');
  return _Tlv(tag, Uint8List.sublistView(b, p, p + len), p + len);
}

List<_Tlv> _children(Uint8List body) {
  final out = <_Tlv>[];
  var p = 0;
  while (p < body.length) {
    final t = _read(body, p);
    out.add(t);
    p = t.end;
  }
  return out;
}

Uint8List _encode(int tag, List<int> body) {
  final len = body.length;
  final head = <int>[tag];
  if (len < 0x80) {
    head.add(len);
  } else {
    final bytes = <int>[];
    for (var v = len; v > 0; v >>= 8) {
      bytes.insert(0, v & 0xff);
    }
    head
      ..add(0x80 | bytes.length)
      ..addAll(bytes);
  }
  return Uint8List.fromList([...head, ...body]);
}

Uint8List _whole(_Tlv t) => _encode(t.tag, t.body);

bool _isNameConstraints(_Tlv ext) {
  final oid = _children(ext.body).first;
  final enc = _whole(oid);
  if (enc.length != _nameConstraintsOid.length) return false;
  for (var i = 0; i < enc.length; i++) {
    if (enc[i] != _nameConstraintsOid[i]) return false;
  }
  return true;
}

/// DER of the CA certificate without its nameConstraints extension; the
/// certificate unchanged when it has none.
Uint8List withoutNameConstraints(Uint8List der) {
  final cert = _read(der, 0);
  final parts = _children(cert.body);
  final tbs = parts[0];
  final fields = _children(tbs.body);
  var changed = false;
  final newFields = <int>[];
  for (final f in fields) {
    if (f.tag != 0xa3) {
      newFields.addAll(_whole(f));
      continue;
    }
    final exts = _children(_read(f.body, 0).body);
    final kept = [for (final e in exts) if (!_isNameConstraints(e)) e];
    changed = kept.length != exts.length;
    newFields.addAll(_encode(0xa3, _encode(0x30, [for (final e in kept) ..._whole(e)])));
  }
  if (!changed) return der;
  return _encode(0x30, [..._encode(0x30, newFields), for (final p in parts.skip(1)) ..._whole(p)]);
}

/// PEM bytes for SecurityContext.setTrustedCertificatesBytes.
Uint8List trustAnchor(String pem) {
  final der = withoutNameConstraints(pemToDer(pem));
  final b64 = base64.encode(der);
  final lines = [for (var i = 0; i < b64.length; i += 64) b64.substring(i, i + 64 > b64.length ? b64.length : i + 64)];
  return utf8.encode('-----BEGIN CERTIFICATE-----\n${lines.join('\n')}\n-----END CERTIFICATE-----\n');
}
