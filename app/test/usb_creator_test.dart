import 'dart:io';

import 'package:dahlia/src/usb_creator.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('release: the ISO and its checksum file', () {
    Map asset(String name, [int size = 1]) =>
        {'name': name, 'size': size, 'browser_download_url': 'https://github.com/Fabiy99/dahlia-linux/releases/download/v1.6.0/$name'};
    final r = parseRelease({
      'tag_name': 'v1.6.0',
      'assets': [asset('Dahlia-1.6.0-setup.exe'), asset('dahlia-linux-1.6.0-amd64.iso', 912), asset('dahlia-linux-1.6.0-amd64.iso.sha256')],
    })!;
    expect([r.version, r.name, r.size], ['1.6.0', 'dahlia-linux-1.6.0-amd64.iso', 912]);
    expect(r.sumUrl.path, endsWith('.iso.sha256'));
    expect(parseRelease({'tag_name': 'v1.6.0', 'assets': [asset('dahlia-linux-1.6.0-amd64.iso')]}), isNull);
    expect(parseRelease({'message': 'Not Found'}), isNull);
  });

  test('sha256sum lines', () {
    final h = 'A' * 64;
    expect(parseSha256('$h  dahlia-linux-1.6.0-amd64.iso\n', 'dahlia-linux-1.6.0-amd64.iso'), 'a' * 64);
    expect(parseSha256('$h *dahlia-linux-1.6.0-amd64.iso', 'dahlia-linux-1.6.0-amd64.iso'), 'a' * 64);
    expect(parseSha256('$h  other.iso', 'dahlia-linux-1.6.0-amd64.iso'), isNull);
  });

  test('Get-Disk output: one disk or several', () {
    final one = parseDisks('{"Number":2,"FriendlyName":"SanDisk Ultra ","Size":32017047552}');
    expect([one.single.number, one.single.name, one.single.size], [2, 'SanDisk Ultra', 32017047552]);
    expect(parseDisks('[{"Number":2,"Size":1},{"Number":3,"FriendlyName":"x","Size":0}]').map((d) => d.number), [2]);
    expect(parseDisks(''), isEmpty);
    expect(parseDisks('error'), isEmpty);
  });

  test('progress reports', () {
    final p = parseProgress('{"state":"writing","done":50,"total":200,"message":""}')!;
    expect([p.state, p.fraction], ['writing', .25]);
    expect(parseProgress('{"state":"preparing","done":0,"total":0}')!.fraction, isNull);
    expect(parseProgress('half a json'), isNull);
  });

  test('elevation: paths in double quotes, each argument quoted for the command line', () {
    final args = elevateArgs(r"C:\Users\Max O'Neil\AppData\Local\Temp\w.ps1", const UsbDisk(3, 'Stick', 8000),
        r'D:\ISO\dahlia.iso', r'C:\t\p.json');
    expect(args.take(3), ['-NoProfile', '-NonInteractive', '-Command']);
    final cmd = args.last;
    expect(cmd, startsWith('Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -ArgumentList '));
    expect(cmd, contains(r"""'"C:\Users\Max O''Neil\AppData\Local\Temp\w.ps1"'"""));
    expect(cmd, contains("'-Disk','3','-Size','8000'"));
  });

  test('the writer script is shipped and checks bus and size', () {
    final s = File('assets/usb-writer.ps1').readAsStringSync();
    expect(s, contains(r"[string]$Bus = 'USB'"));
    expect(s, contains(r'if ($d.Size -ne $Size)'));
    expect(s, contains('PhysicalDrive'));
  });

  test('download: follows the body, returns the SHA-256', () async {
    final server = await HttpServer.bind('127.0.0.1', 0);
    server.listen((req) {
      req.response
        ..write('dahlia')
        ..close();
    });
    final dir = await Directory.systemTemp.createTemp('usb');
    final seen = <int>[];
    final sum = await download(HttpClient(), Uri.parse('http://127.0.0.1:${server.port}/x.iso'), File('${dir.path}/x.iso'), seen.add);
    expect(sum, 'ea9f2e882b7f8fcd25da648b32a36c6c7ba9a06f8af3a9b47e87b088a65a316e');
    expect(await fileSha256(File('${dir.path}/x.iso')), sum);
    expect(seen.last, 6);
    await server.close(force: true);
    await dir.delete(recursive: true);
  });
}
