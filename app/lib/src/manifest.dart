// Update channel: one signed manifest per release on the apt repository's
// GitHub Pages site, app/<version>.json plus app/<version>.json.sig
// (tools/sign-app-manifests.sh). The signature is made with the apt
// repository key; the app carries its public half
// (assets/dahlia-archive-keyring.asc), so a manifest is trusted exactly like
// the server's packages.

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import 'openpgp.dart';

/// Same address as the apt repository (archive-keyring's dahlia.sources).
const channelBase = 'https://fabiy99.github.io/dahlia-linux/app/';

/// Version of this build, set by CI with --dart-define=DAHLIA_VERSION=x.y.z.
/// A build without it ("dev") never updates itself.
const appVersion = String.fromEnvironment('DAHLIA_VERSION', defaultValue: 'dev');

final _versionRe = RegExp(r'^\d+\.\d+\.\d+$');

bool isReleaseVersion(String v) => _versionRe.hasMatch(v);

class UpdateError implements Exception {
  UpdateError(this.key);

  /// Locale key suffix: "no_manifest", "signature", "download", "checksum", ...
  final String key;

  @override
  String toString() => 'UpdateError($key)';
}

class Asset {
  Asset({required this.name, required this.url, required this.sha256, required this.size});

  final String name;
  final Uri url;
  final String sha256;
  final int size;
}

class Manifest {
  Manifest(this.version, this.files);

  final String version;

  /// Platform key ("windows-x64", "macos", "linux-x64") -> installer.
  final Map<String, Asset> files;

  /// Parses and checks a manifest; [expected] is the version that was asked for.
  factory Manifest.parse(String text, String expected) {
    final Object? j;
    try {
      j = jsonDecode(text);
    } on FormatException {
      throw UpdateError('manifest');
    }
    if (j is! Map || j['version'] != expected || j['files'] is! Map) {
      throw UpdateError('manifest');
    }
    final files = <String, Asset>{};
    (j['files'] as Map).forEach((k, v) {
      if (k is! String || v is! Map) throw UpdateError('manifest');
      final name = v['name'], url = v['url'], sum = v['sha256'], size = v['size'];
      if (name is! String ||
          url is! String ||
          sum is! String ||
          size is! int ||
          !RegExp(r'^[0-9a-f]{64}$').hasMatch(sum) ||
          !url.startsWith('https://')) {
        throw UpdateError('manifest');
      }
      files[k] = Asset(name: name, url: Uri.parse(url), sha256: sum, size: size);
    });
    return Manifest(expected, files);
  }

  Asset? forThisPlatform() => files[platformKey()];
}

String platformKey() {
  if (Platform.isWindows) return 'windows-x64';
  if (Platform.isMacOS) return 'macos';
  return 'linux-x64';
}

Uri manifestUrl(String version, [String base = channelBase]) =>
    Uri.parse('$base$version.json');

/// Fetches and verifies the manifest for exactly [version].
Future<Manifest> fetchManifest(
  String version, {
  required String publicKey,
  HttpClient? client,
  String base = channelBase,
}) async {
  if (!isReleaseVersion(version)) throw UpdateError('manifest');
  final http = client ?? HttpClient();
  final url = manifestUrl(version, base);
  try {
    final body = await _get(http, url);
    final sig = await _get(http, Uri.parse('$url.sig'));
    if (body == null || sig == null) throw UpdateError('no_manifest');
    if (!await verifyDetached(body, utf8.decode(sig), publicKey)) {
      throw UpdateError('signature');
    }
    return Manifest.parse(utf8.decode(body), version);
  } on SocketException {
    throw UpdateError('offline');
  } on HttpException {
    throw UpdateError('offline');
  } finally {
    if (client == null) http.close();
  }
}

/// Body of a GET, null on 404.
Future<List<int>?> _get(HttpClient http, Uri url) async {
  final req = await http.getUrl(url);
  final resp = await req.close();
  if (resp.statusCode == 404) {
    await resp.drain<void>();
    return null;
  }
  if (resp.statusCode != 200) {
    await resp.drain<void>();
    throw UpdateError('offline');
  }
  final out = BytesBuilder(copy: false);
  await for (final chunk in resp) {
    out.add(chunk);
    if (out.length > 1 << 20) throw UpdateError('manifest');
  }
  return out.takeBytes();
}

/// Downloads [asset] to [target], reporting progress 0..1, and checks the
/// SHA-256 from the signed manifest. A mismatch deletes the file.
Future<void> download(
  Asset asset,
  File target, {
  void Function(double)? progress,
  HttpClient? client,
}) async {
  final http = client ?? HttpClient();
  final sink = target.openWrite();
  final hashOut = _DigestSink();
  final hasher = sha256.startChunkedConversion(hashOut);
  var got = 0;
  try {
    final req = await http.getUrl(asset.url);
    final resp = await req.close();
    if (resp.statusCode != 200) throw UpdateError('download');
    await for (final chunk in resp) {
      got += chunk.length;
      if (got > asset.size) throw UpdateError('checksum');
      hasher.add(chunk);
      sink.add(chunk);
      progress?.call(got / asset.size);
    }
    hasher.close();
    await sink.close();
    if (got != asset.size || hashOut.value.toString() != asset.sha256) {
      throw UpdateError('checksum');
    }
  } on UpdateError {
    await _discard(sink, target);
    rethrow;
  } on IOException {
    await _discard(sink, target);
    throw UpdateError('download');
  } finally {
    if (client == null) http.close();
  }
}

Future<void> _discard(IOSink sink, File f) async {
  try {
    await sink.close();
  } on IOException {
    // Already closed.
  }
  if (await f.exists()) await f.delete();
}

class _DigestSink implements Sink<Digest> {
  late Digest value;

  @override
  void add(Digest data) => value = data;

  @override
  void close() {}
}
