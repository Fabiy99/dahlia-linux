// What the app shows and why: pairing, version check, login, home. The UI
// (lib/ui/) only renders a Stage and calls these methods.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import 'manifest.dart';
import 'pairing.dart';
import 'server.dart';
import 'settings.dart';
import 'strings.dart';

enum Stage { starting, connect, fingerprint, connecting, login, update, home }

/// Area shown in Stage.home, picked in the top bar.
enum Area { home, apps }

typedef Assets = Future<String> Function(String path);

class Controller extends ChangeNotifier {
  Controller({
    required this.settingsFile,
    required this.loadAsset,
    Secrets? secrets,
    this.version = appVersion,
  }) : secrets = secrets ?? Secrets();

  final File settingsFile;
  final Assets loadAsset;
  final Secrets secrets;

  /// This app's version ("dev" in development builds).
  final String version;

  Settings settings = Settings();
  Strings strings = Strings('en', const {});
  Stage stage = Stage.starting;
  Area area = Area.home;

  /// Locale key of the last error, shown on the current stage.
  String? error;
  Map<String, Object?> errorVars = const {};

  Server? server;
  String? user;

  /// Host and CA while the user compares the fingerprint.
  String? pendingHost;
  String? pendingPem;

  /// Server version that made the app stop (Stage.update); "" = too old to say.
  String? serverVersion;

  /// Login needs the second factor.
  bool needsCode = false;
  bool busy = false;

  void go(Area a) {
    area = a;
    notifyListeners();
  }

  String t(String key, [Map<String, Object?> vars = const {}]) => strings.t(key, vars);

  void _set(Stage s, {String? err, Map<String, Object?> vars = const {}}) {
    stage = s;
    error = err;
    errorVars = vars;
    busy = false;
    notifyListeners();
  }

  Future<void> start() async {
    settings = Settings.load(settingsFile);
    await _loadStrings();
    if (settings.paired) {
      await connect();
    } else {
      _set(Stage.connect);
    }
  }

  Future<void> _loadStrings() async {
    final own = Strings.parse(await loadAsset('assets/locales/${settings.lang}.json'));
    strings = Strings(settings.lang, own);
  }

  Future<void> setLanguage(String lang) async {
    settings.lang = lang == 'de' ? 'de' : 'en';
    settings.save(settingsFile);
    await _loadStrings();
    await _loadServerStrings();
    notifyListeners();
  }

  /// The dashboard's own texts (error keys and the like) for the chosen language.
  Future<void> _loadServerStrings() async {
    final s = server;
    if (s == null) return;
    final http = pinnedClient(s.pem);
    try {
      final req = await http.getUrl(Uri.parse('https://${s.host}/locales/${settings.lang}.json'));
      final resp = await req.close().timeout(const Duration(seconds: 8));
      if (resp.statusCode == 200) {
        strings = strings.withServer(Strings.parse(await utf8.decodeStream(resp)));
      } else {
        await resp.drain<void>();
      }
    } on Object {
      // The app's own texts are enough to go on.
    } finally {
      http.close(force: true);
    }
  }

  // --- pairing ---------------------------------------------------------------

  /// Step 1: address typed or picked; downloads the CA for the fingerprint.
  Future<void> choose(String input) async {
    busy = true;
    error = null;
    notifyListeners();
    try {
      final host = normalizeHost(input);
      final pem = await fetchCa(host);
      pendingHost = host;
      pendingPem = pem;
      _set(Stage.fingerprint);
    } on PairingError catch (e) {
      _set(Stage.connect, err: 'error.${e.key}');
    }
  }

  String get pendingFingerprint => pendingPem == null ? '' : fingerprint(pemToDer(pendingPem!));

  /// Step 2a: the user compared the fingerprint.
  Future<void> confirmFingerprint() => _paired(pendingHost!, pendingPem!);

  /// Step 2b: pairing code from the dashboard; the server has to prove it.
  Future<void> pairWithCode(String input) async {
    busy = true;
    error = null;
    notifyListeners();
    try {
      final code = normalizeCode(input);
      final nonce = newNonce();
      final got = await requestProof(pendingHost!, nonce);
      if (!proofMatches(proof(code, nonce, pemToDer(pendingPem!)), got)) {
        throw PairingError('proof');
      }
      await _paired(pendingHost!, pendingPem!);
    } on PairingError catch (e) {
      _set(Stage.fingerprint, err: 'error.${e.key}');
    }
  }

  Future<void> _paired(String host, String pem) async {
    if (settings.host != host) settings.username = null;
    settings
      ..host = host
      ..pem = pem;
    settings.save(settingsFile);
    await connect();
  }

  void back() => _set(Stage.connect);

  /// "Other server": keeps nothing of the old pairing but the language.
  Future<void> otherServer() async {
    server?.close();
    server = null;
    user = null;
    settings = Settings(lang: settings.lang);
    settings.save(settingsFile);
    _set(Stage.connect);
  }

  // --- connection, version, login ----------------------------------------------

  Future<void> connect() async {
    final host = settings.host!;
    _set(Stage.connecting);
    try {
      await checkPaired(host, settings.pem!);
    } on PairingError catch (e) {
      _set(Stage.connect, err: 'error.${e.key}');
      return;
    }
    server?.close();
    final s = Server(host: host, pem: settings.pem!, appVersion: version, onVersion: _mismatch);
    server = s;
    s.session = await secrets.read(host, 'session');
    await _loadServerStrings();
    try {
      final state = await s.get('/api/state');
      if (s.serverVersion == null && s.bound) {
        // Dahlia before 1.4.0 names no version; there is no app for it.
        _mismatch('');
        return;
      }
      user = state is Map ? state['user'] as String? : null;
      if (user != null) {
        _set(Stage.home);
        return;
      }
      await _autoLogin();
    } on VersionMismatch {
      return;
    } on ApiError catch (e) {
      _set(Stage.connect, err: e.key);
    }
  }

  Future<void> _autoLogin() async {
    final name = settings.username;
    final pw = name == null || !settings.autoLogin ? null : await secrets.read(settings.host!, 'password');
    if (name != null && pw != null) {
      await login(name, pw, save: true);
      return;
    }
    _set(Stage.login);
  }

  Future<void> login(String name, String password, {String code = '', bool save = true}) async {
    final s = server!;
    busy = true;
    error = null;
    notifyListeners();
    try {
      final r = await s.post('/api/login', {
        'username': name.trim(),
        'password': password,
        'code': code,
        'remember': save,
      });
      user = r is Map ? r['user'] as String? : name.trim();
      settings
        ..username = user
        ..autoLogin = save;
      settings.save(settingsFile);
      await secrets.write(s.host, 'session', save ? s.session : null);
      await secrets.write(s.host, 'password', save ? password : null);
      needsCode = false;
      _set(Stage.home);
    } on VersionMismatch {
      return;
    } on ApiError catch (e) {
      if (e.key == 'error.totp_required') needsCode = true;
      if (e.status == 0) {
        _set(Stage.connect, err: e.key);
      } else {
        _set(Stage.login, err: e.key);
      }
    }
  }

  Future<void> logout() async {
    final s = server;
    if (s != null) {
      try {
        await s.post('/api/logout');
      } on Exception {
        // Signed out locally anyway.
      }
      s.session = null;
      await secrets.write(s.host, 'session', null);
      await secrets.write(s.host, 'password', null);
    }
    user = null;
    area = Area.home;
    _set(Stage.login);
  }

  /// A call failed because the session ended: log in again.
  Future<void> sessionEnded() async {
    user = null;
    await secrets.write(settings.host!, 'session', null);
    await _autoLogin();
  }

  // --- version binding ---------------------------------------------------------

  void _mismatch(String server) {
    serverVersion = server;
    _set(Stage.update);
  }

  @override
  void dispose() {
    server?.close();
    super.dispose();
  }
}
