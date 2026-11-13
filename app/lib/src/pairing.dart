// Pairing with a server: address check, fingerprint and pairing-code proof.
//
// Every Dahlia server has its own local CA (/etc/dahlia/tls/ca.crt, served
// as /dahlia-ca.crt). The app downloads it once without verification and
// pins it after the user compared the SHA-256 fingerprint, or after the
// server proved it knows the pairing code shown in the dashboard. The code
// itself is never sent (see dahlia_dashboard/pairing.py).

import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

const _proofPrefix = 'dahlia-pairing\u0000';
const _codeAlphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const _codeLength = 10;

class PairingError implements Exception {
  PairingError(this.key);

  /// Locale key suffix: "address", "certificate", "code", ...
  final String key;

  @override
  String toString() => 'PairingError($key)';
}

/// Accepts a host name or IP address, optionally with a port; nothing else.
/// Returns "host" or "host:port" in lower case, IPv6 in brackets.
String normalizeHost(String input) {
  var s = input.trim();
  while (s.endsWith('/')) {
    s = s.substring(0, s.length - 1);
  }
  if (s.startsWith('https://')) s = s.substring(8);
  if (s.isEmpty || RegExp(r'[/@?#\s]').hasMatch(s) || s.contains('://')) {
    throw PairingError('address');
  }
  final Uri uri;
  try {
    uri = Uri.parse('https://$s/');
  } on FormatException {
    throw PairingError('address');
  }
  final host = uri.host.toLowerCase();
  if (host.isEmpty || !RegExp(r'^[a-z0-9.\-:\[\]]+$').hasMatch(host)) {
    throw PairingError('address');
  }
  final shown = host.contains(':') ? '[$host]' : host;
  return uri.hasPort && uri.port != 443 ? '$shown:${uri.port}' : shown;
}

/// DER bytes of the first certificate in a PEM text.
Uint8List pemToDer(String pem) {
  const begin = '-----BEGIN CERTIFICATE-----';
  const end = '-----END CERTIFICATE-----';
  final a = pem.indexOf(begin);
  final b = a < 0 ? -1 : pem.indexOf(end, a);
  if (a < 0 || b < 0) throw PairingError('certificate');
  final body = pem.substring(a + begin.length, b).replaceAll(RegExp(r'\s'), '');
  try {
    return base64.decode(body);
  } on FormatException {
    throw PairingError('certificate');
  }
}

/// SHA-256 over the DER form, as AB:CD:... (same format as the dashboard).
String fingerprint(List<int> der) => sha256
    .convert(der)
    .bytes
    .map((b) => b.toRadixString(16).padLeft(2, '0').toUpperCase())
    .join(':');

String hex(List<int> bytes) =>
    bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

/// Pairing code as the dashboard shows it (ABCDE-FGHJK), without dash and spaces.
String normalizeCode(String input) {
  final code = input.replaceAll(RegExp(r'[- ]'), '').toUpperCase();
  if (code.length != _codeLength ||
      !code.split('').every(_codeAlphabet.contains)) {
    throw PairingError('code');
  }
  return code;
}

/// HMAC-SHA256(code, PREFIX + nonce + sha256(CA)), hex.
String proof(String code, List<int> nonce, List<int> der) {
  final mac = Hmac(sha256, utf8.encode(code));
  final data = <int>[
    ...utf8.encode(_proofPrefix),
    ...nonce,
    ...sha256.convert(der).bytes,
  ];
  return hex(mac.convert(data).bytes);
}

/// Compares in constant time.
bool proofMatches(String expected, String got) {
  if (expected.length != got.length) return false;
  var diff = 0;
  for (var i = 0; i < expected.length; i++) {
    diff |= expected.codeUnitAt(i) ^ got.codeUnitAt(i);
  }
  return diff == 0;
}
