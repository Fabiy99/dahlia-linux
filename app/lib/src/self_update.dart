// Brings the app to exactly the server's version: signed manifest, download
// with checksum, install, restart (manifest.dart, installer.dart).

import 'dart:io';

import 'package:flutter/foundation.dart';

import 'installer.dart';
import 'manifest.dart';

enum UpdatePhase { checking, downloading, installing, failed, manual }

class SelfUpdate extends ChangeNotifier {
  SelfUpdate({
    required this.target,
    required this.loadKey,
    this.fetch = fetchManifest,
    this.fetchFile = download,
    this.install = installAndRestart,
    bool Function()? installable,
  }) : installable = installable ?? canInstall;

  /// The server's version.
  final String target;
  final Future<String> Function() loadKey;
  final Future<Manifest> Function(String version, {required String publicKey}) fetch;
  final Future<void> Function(Asset asset, File target, {void Function(double)? progress}) fetchFile;
  final Future<Never> Function(File file) install;
  final bool Function() installable;

  UpdatePhase phase = UpdatePhase.checking;
  double progress = 0;

  /// Locale key suffix of the failure ("no_manifest", "checksum", ...).
  String? error;

  /// Where to get the app by hand.
  Uri get releasePage => Uri.parse('https://github.com/Fabiy99/dahlia-linux/releases/tag/v$target');

  void _to(UpdatePhase p, [String? err]) {
    phase = p;
    error = err;
    notifyListeners();
  }

  Future<void> run() async {
    if (!installable()) {
      _to(UpdatePhase.manual);
      return;
    }
    _to(UpdatePhase.checking);
    try {
      final manifest = await fetch(target, publicKey: await loadKey());
      final asset = manifest.forThisPlatform();
      if (asset == null) throw UpdateError('platform');
      final dir = await Directory.systemTemp.createTemp('dahlia-update');
      final file = File('${dir.path}/${asset.name.replaceAll(RegExp(r'[/\\]'), '_')}');
      progress = 0;
      _to(UpdatePhase.downloading);
      await fetchFile(asset, file, progress: (p) {
        progress = p;
        notifyListeners();
      });
      _to(UpdatePhase.installing);
      await install(file);
    } on UpdateError catch (e) {
      _to(UpdatePhase.failed, e.key);
    }
  }
}
