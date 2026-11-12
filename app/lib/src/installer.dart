// Installs a downloaded, checked update and starts the new version.
//
// Windows: the Inno Setup installer, per user and silent (no UAC prompt);
//          it starts the app again when done.
// macOS:   the zipped Dahlia.app replaces the running bundle once this
//          process has exited, then opens again.
// Linux:   the .deb through apt-get; pkexec asks for the password.

import 'dart:io';

import 'manifest.dart';

class Step {
  const Step(this.exe, this.args, {this.detached = false});

  final String exe;
  final List<String> args;

  /// Started without waiting; the app quits right after.
  final bool detached;

  @override
  String toString() => [exe, ...args].join(' ');
}

/// Windows: the installer closes and restarts the app itself.
List<Step> windowsSteps(String installer) => [
      Step(installer, ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS'],
          detached: true),
    ];

/// macOS: unpack next to the running bundle, swap once this process is gone.
List<Step> macSteps(String zip, String bundle, int pid) {
  final staging = '$bundle.update';
  final script = 'while kill -0 "\$1" 2>/dev/null; do sleep 0.2; done; '
      'rm -rf "\$2.old" && mv "\$2" "\$2.old" && mv "\$3"/*.app "\$2" && '
      'rm -rf "\$2.old" "\$3" "\$4"; open "\$2"';
  return [
    Step('/bin/rm', ['-rf', staging]),
    Step('/usr/bin/ditto', ['-x', '-k', zip, staging]),
    Step('/bin/sh', ['-c', script, 'sh', '$pid', bundle, staging, zip], detached: true),
  ];
}

/// Linux: apt-get installs the exact version (also a downgrade, the server
/// decides), then the new binary starts.
List<Step> linuxSteps(String deb) => [
      Step('pkexec', ['/usr/bin/apt-get', 'install', '-y', '--allow-downgrades', deb]),
      const Step('/usr/bin/dahlia', [], detached: true),
    ];

/// The .app bundle the running binary belongs to (…/Dahlia.app/Contents/MacOS/dahlia).
String? macBundle(String executable) {
  final parts = executable.split('/');
  final i = parts.lastIndexWhere((p) => p.endsWith('.app'));
  return i < 0 ? null : parts.sublist(0, i + 1).join('/');
}

/// Can this copy update itself? A development run or a copy outside the
/// installed location cannot.
bool canInstall() {
  if (!isReleaseVersion(appVersion)) return false;
  if (Platform.isMacOS) return macBundle(Platform.resolvedExecutable) != null;
  if (Platform.isLinux) return Platform.resolvedExecutable == '/opt/dahlia/dahlia';
  return Platform.isWindows;
}

List<Step> stepsFor(File file) {
  if (Platform.isWindows) return windowsSteps(file.path);
  if (Platform.isMacOS) {
    final bundle = macBundle(Platform.resolvedExecutable);
    if (bundle == null) throw UpdateError('install');
    return macSteps(file.path, bundle, pid);
  }
  return linuxSteps(file.path);
}

/// Runs the steps and quits the app. Throws UpdateError('install') if a step fails.
Future<Never> installAndRestart(File file) async {
  for (final s in stepsFor(file)) {
    if (s.detached) {
      await Process.start(s.exe, s.args, mode: ProcessStartMode.detached);
      continue;
    }
    final r = await Process.run(s.exe, s.args);
    if (r.exitCode != 0) throw UpdateError('install');
  }
  exit(0);
}
