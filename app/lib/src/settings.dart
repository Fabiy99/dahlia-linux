// What the app remembers: the paired server, its CA, the account and the
// language in settings.json in the app's support directory; password and
// session in the system's secret store (Windows Credential Manager, macOS
// Keychain, Secret Service on Linux).

import 'dart:convert';
import 'dart:io';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class Settings {
  Settings({this.host, this.pem, this.username, this.lang = 'en', this.autoLogin = true});

  /// Paired server ("dahlia.local" or "host:port"); null before pairing.
  String? host;

  /// The server's CA (PEM), pinned for every connection.
  String? pem;
  String? username;

  /// "en" (default) or "de".
  String lang;

  /// Log in with the saved password when the session has ended.
  bool autoLogin;

  bool get paired => host != null && pem != null;

  Map<String, Object?> toJson() =>
      {'host': host, 'pem': pem, 'username': username, 'lang': lang, 'auto_login': autoLogin};

  factory Settings.fromJson(Object? j) {
    if (j is! Map) return Settings();
    String? s(String k) => j[k] is String ? j[k] as String : null;
    final lang = s('lang');
    return Settings(
      host: s('host'),
      pem: s('pem'),
      username: s('username'),
      lang: lang == 'de' ? 'de' : 'en',
      autoLogin: j['auto_login'] != false,
    );
  }

  static Settings load(File f) {
    try {
      return Settings.fromJson(jsonDecode(f.readAsStringSync()));
    } on FileSystemException {
      return Settings();
    } on FormatException {
      return Settings();
    }
  }

  /// Written to a temporary file first, so a crash never leaves half a file.
  void save(File f) {
    f.parent.createSync(recursive: true);
    final tmp = File('${f.path}.tmp');
    tmp.writeAsStringSync(const JsonEncoder.withIndent('  ').convert(toJson()));
    tmp.renameSync(f.path);
  }
}

/// Password and session per server. A missing or locked secret store only
/// means the app asks again; it never fails the login.
class Secrets {
  Secrets([FlutterSecureStorage? store]) : _store = store ?? const FlutterSecureStorage();

  final FlutterSecureStorage _store;

  /// Linux goes through secret-tool (package libsecret-tools): the plugin
  /// aborts the process when no Secret Service runs, the tool just fails.
  bool get _cli => Platform.isLinux;

  Future<String?> read(String host, String what) async {
    try {
      if (_cli) {
        final r = await Process.run('secret-tool', ['lookup', ...secretToolAttributes(host, what)]);
        final out = r.stdout as String;
        return r.exitCode == 0 && out.isNotEmpty ? out : null;
      }
      return await _store.read(key: 'dahlia:$host:$what');
    } on Exception {
      return null;
    }
  }

  Future<void> write(String host, String what, String? value) async {
    try {
      if (_cli) {
        if (value == null) {
          await Process.run('secret-tool', ['clear', ...secretToolAttributes(host, what)]);
        } else {
          final p = await Process.start(
              'secret-tool', ['store', '--label=Dahlia $host', ...secretToolAttributes(host, what)]);
          p.stdin.write(value);
          await p.stdin.close();
          await p.exitCode;
        }
        return;
      }
      if (value == null) {
        await _store.delete(key: 'dahlia:$host:$what');
      } else {
        await _store.write(key: 'dahlia:$host:$what', value: value);
      }
    } on Exception {
      // Not saved; the user logs in by hand next time.
    }
  }
}

List<String> secretToolAttributes(String host, String what) =>
    ['application', 'org.dahlialinux.dahlia', 'server', host, 'secret', what];
