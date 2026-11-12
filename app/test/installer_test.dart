import 'package:dahlia/src/installer.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('windows runs the installer silently and detached', () {
    final s = windowsSteps(r'C:\Temp\Dahlia-1.4.0-setup.exe');
    expect(s.single.detached, isTrue);
    expect(s.single.args, contains('/VERYSILENT'));
  });

  test('macOS swaps the bundle after the app has quit', () {
    expect(macBundle('/Applications/Dahlia.app/Contents/MacOS/dahlia'), '/Applications/Dahlia.app');
    expect(macBundle('/usr/local/bin/dahlia'), isNull);
    final s = macSteps('/tmp/d.zip', '/Applications/Dahlia.app', 42);
    expect(s.map((e) => e.exe), ['/bin/rm', '/usr/bin/ditto', '/bin/sh']);
    expect(s[1].args, ['-x', '-k', '/tmp/d.zip', '/Applications/Dahlia.app.update']);
    expect(s.last.detached, isTrue);
    expect(s.last.args.sublist(3), ['42', '/Applications/Dahlia.app', '/Applications/Dahlia.app.update', '/tmp/d.zip']);
  });

  test('linux installs the exact version with apt-get, also downgrades', () {
    final s = linuxSteps('/tmp/dahlia_1.4.0_amd64.deb');
    expect(s.first.toString(), 'pkexec /usr/bin/apt-get install -y --allow-downgrades /tmp/dahlia_1.4.0_amd64.deb');
    expect(s.last.detached, isTrue);
  });
}
