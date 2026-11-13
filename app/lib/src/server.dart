// Talks to a paired Dahlia server over HTTPS, trusting only the CA it was
// paired with (no system trust store, nothing to install on the PC).
//
// Every API response carries X-Dahlia-Version. The app is bound to exactly
// that version: on a difference it stops and updates itself first, no matter
// whether the server was updated from this app, from a phone or by the
// automatic updates (see VersionMismatch and lib/ui/update_page.dart).

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'dart:math';

import 'pairing.dart';

const versionHeader = 'x-dahlia-version';
const _cookie = 'dahlia_session';
const _timeout = Duration(seconds: 8);

class ApiError implements Exception {
  ApiError(this.status, this.key);

  /// HTTP status; 0 when the server could not be reached.
  final int status;

  /// Locale key from the server ("error.login_required") or of the app.
  final String key;

  @override
  String toString() => 'ApiError($status, $key)';
}

/// Thrown instead of a result when the server runs another version.
class VersionMismatch implements Exception {
  VersionMismatch(this.server);

  final String server;

  @override
  String toString() => 'VersionMismatch($server)';
}

HttpClient _insecure() => HttpClient()
  ..connectionTimeout = _timeout
  ..badCertificateCallback = (cert, host, port) => true;

HttpClient pinnedClient(String pem) {
  final ctx = SecurityContext(withTrustedRoots: false)
    ..setTrustedCertificatesBytes(utf8.encode(pem));
  return HttpClient(context: ctx)
    ..connectionTimeout = _timeout
    // Below the server's keep-alive (uvicorn 5 s, nginx 75 s), so a request
    // never goes out on a connection the server is just closing.
    ..idleTimeout = const Duration(seconds: 4);
}

Uri _url(String host, String path) => Uri.parse('https://$host$path');

Future<String> _readText(HttpClientResponse resp, int max) async {
  final out = BytesBuilder(copy: false);
  await for (final chunk in resp) {
    out.add(chunk);
    if (out.length > max) throw PairingError('not_dahlia');
  }
  return utf8.decode(out.takeBytes(), allowMalformed: true);
}

/// Downloads /dahlia-ca.crt without verification; the user checks the
/// fingerprint or the server proves the pairing code afterwards.
Future<String> fetchCa(String host) async {
  final http = _insecure();
  try {
    final resp = await (await http.getUrl(_url(host, '/dahlia-ca.crt'))).close().timeout(_timeout);
    if (resp.statusCode != 200) throw PairingError('not_dahlia');
    final pem = await _readText(resp, 64 * 1024);
    try {
      pemToDer(pem);
    } on PairingError {
      throw PairingError('not_dahlia');
    }
    return pem;
  } on IOException {
    throw PairingError('unreachable');
  } on TimeoutException {
    throw PairingError('unreachable');
  } finally {
    http.close(force: true);
  }
}

List<int> newNonce() {
  final r = Random.secure();
  return List<int>.generate(32, (_) => r.nextInt(256));
}

/// Asks the server for its proof over [nonce]. Without verification, like
/// fetchCa: the proof is what counts.
Future<String> requestProof(String host, List<int> nonce) async {
  final http = _insecure();
  try {
    final req = await http.postUrl(_url(host, '/api/pairing/proof'));
    req.headers
      ..set('X-Dahlia-Request', '1')
      ..contentType = ContentType.json;
    req.write(jsonEncode({'nonce': hex(nonce)}));
    final resp = await req.close().timeout(_timeout);
    if (resp.statusCode == 404) throw PairingError('code');
    if (resp.statusCode != 200) throw PairingError('not_dahlia');
    final body = jsonDecode(await _readText(resp, 64 * 1024));
    final p = body is Map ? body['proof'] : null;
    if (p is! String) throw PairingError('not_dahlia');
    return p;
  } on IOException {
    throw PairingError('unreachable');
  } on FormatException {
    throw PairingError('not_dahlia');
  } on TimeoutException {
    throw PairingError('unreachable');
  } finally {
    http.close(force: true);
  }
}

/// Is the server there, with the CA we paired? Throws "unreachable", or
/// "changed" when it answers with another certificate (reinstalled server,
/// or someone else at that address).
Future<void> checkPaired(String host, String pem) async {
  final pinned = pinnedClient(pem);
  try {
    final resp = await (await pinned.getUrl(_url(host, '/dahlia-ca.crt'))).close().timeout(_timeout);
    await resp.drain<void>();
    return;
  } on HandshakeException {
    // Falls through to the unpinned check.
  } on IOException {
    throw PairingError('unreachable');
  } on TimeoutException {
    throw PairingError('unreachable');
  } finally {
    pinned.close(force: true);
  }
  throw PairingError('changed');
}

/// API client for one paired server.
class Server {
  Server({
    required this.host,
    required this.pem,
    required this.appVersion,
    this.onVersion,
    HttpClient? client,
  }) : _http = client ?? pinnedClient(pem);

  final String host;
  final String pem;

  /// Version of this app; "dev" builds accept any server.
  final String appVersion;

  /// Called with the server's version whenever it differs from the app's.
  final void Function(String server)? onVersion;

  final HttpClient _http;
  String? session;

  /// Version the server named in its last response.
  String? serverVersion;

  bool get bound => appVersion != 'dev';

  Future<dynamic> get(String path) => _send('GET', path, null);

  Future<dynamic> post(String path, [Object? body]) => _send('POST', path, body ?? const {});

  Future<dynamic> _send(String method, String path, Object? body) async {
    final HttpClientResponse resp;
    final String text;
    try {
      final req = await _http.openUrl(method, _url(host, path));
      req.headers.set('X-Dahlia-Request', '1');
      if (session != null) req.cookies.add(Cookie(_cookie, session!));
      if (body != null) {
        req.headers.contentType = ContentType.json;
        req.write(jsonEncode(body));
      }
      resp = await req.close().timeout(const Duration(seconds: 30));
      text = await _readText(resp, 32 << 20);
    } on IOException {
      throw ApiError(0, 'error.unreachable');
    } on TimeoutException {
      throw ApiError(0, 'error.unreachable');
    } on PairingError {
      throw ApiError(0, 'error.unexpected');
    }
    for (final c in resp.cookies) {
      if (c.name == _cookie) session = c.value.isEmpty ? null : c.value;
    }
    final v = resp.headers.value(versionHeader);
    if (v != null) {
      serverVersion = v;
      if (bound && v != appVersion) {
        onVersion?.call(v);
        throw VersionMismatch(v);
      }
    }
    dynamic data;
    try {
      data = text.isEmpty ? null : jsonDecode(text);
    } on FormatException {
      data = null;
    }
    if (resp.statusCode >= 400) {
      final key = data is Map && data['error'] is String ? data['error'] as String : 'error.unexpected';
      throw ApiError(resp.statusCode, key);
    }
    return data;
  }

  void close() => _http.close(force: true);
}
