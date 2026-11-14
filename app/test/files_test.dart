import 'dart:io';

import 'package:dahlia/src/apps.dart';
import 'package:dahlia/src/files.dart';
import 'package:dahlia/src/server.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_dahlia.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  final entries = <Map>[
    {'name': 'Films', 'kind': 'dir'},
    {'name': 'a b.txt', 'kind': 'file', 'size': 10},
    {'name': 'link', 'kind': 'link'},
  ];

  test('query, parent and selection order', () {
    expect(fmQuery({'root': '/mnt/user', 'path': 'a b/ä&c'}), 'root=%2Fmnt%2Fuser&path=a+b%2F%C3%A4%26c');
    expect(Uri.splitQueryString(fmQuery({'path': 'a b/ä&c'}))['path'], 'a b/ä&c');
    expect(parentOf('media/films/x.mkv'), 'media/films');
    expect(parentOf('x.mkv'), '');
    expect(selectedInOrder(entries, {'link', 'Films'}), ['Films', 'link']);
  });

  test('what the selection allows', () {
    var a = allowed(entries, ['a b.txt'], true);
    expect([a.download, a.rename, a.copy, a.move, a.delete], [true, true, true, true, true]);
    a = allowed(entries, ['Films'], true);
    expect(a.download, isFalse, reason: 'folders are not downloaded');
    a = allowed(entries, ['Films', 'a b.txt'], true);
    expect([a.download, a.rename, a.copy, a.move, a.delete], [false, false, true, true, true]);
    a = allowed(entries, ['a b.txt'], false);
    expect([a.download, a.rename, a.copy, a.move, a.delete], [true, false, true, false, false]);
    a = allowed(entries, [], true);
    expect([a.copy, a.move, a.delete], [false, false, false]);
  });

  test('job body, hint, progress and upload cases', () {
    expect(startBody('delete', '/mnt/user', 'media', ['a', 'b']),
        {'op': 'delete', 'root': '/mnt/user', 'paths': ['media/a', 'media/b']});
    expect(startBody('move', '/mnt/user', '', ['a'], const Place('/srv', 'x')),
        {'op': 'move', 'root': '/mnt/user', 'paths': ['a'], 'dest_root': '/srv', 'dest': 'x'});
    expect(folderHint({'mounted': false}, 'x'), 'fm.not_mounted');
    expect(folderHint({'mounted': true}, ''), 'fm.hint_root');
    expect(folderHint({'mounted': true, 'writable': false}, 'appdata/x'), 'fm.hint_appdata');
    expect(folderHint({'mounted': true, 'writable': true, 'truncated': true}, 'x'), 'fm.truncated');
    expect(folderHint({'mounted': true, 'writable': true}, 'x'), '');
    expect(jobText({'op': 'delete', 'done': 2, 'total': 5}, t), 'fm.running_delete fm.items done=2 total=5');
    expect(jobText({'op': 'copy', 'done': 1024, 'total': 2048}, t), 'fm.running_copy 1.0 unit.KB / 2.0 unit.KB');
    expect(uploadCase(entries, 'new.txt'), 'new');
    expect(uploadCase(entries, 'a b.txt'), 'overwrite');
    expect(uploadCase(entries, 'Films'), 'dir');
  });

  test('upload streams the file, download writes it via .part', () async {
    final fake = FakeDahlia();
    await fake.start();
    addTearDown(() => fake.http.close(force: true));
    final dir = await Directory.systemTemp.createTemp('dahlia-files');
    addTearDown(() => dir.delete(recursive: true));
    final src = File('${dir.path}/up.bin')..writeAsBytesSync(List.generate(300000, (i) => i % 251));
    final s = Server(host: fake.host, pem: fixture('ca.crt'), appVersion: '1.4.0');
    var last = 0;
    final r = await s.upload('/api/files/upload?${fmQuery({'root': '/mnt/user', 'path': 'x', 'name': 'up.bin'})}', src,
        progress: (sent, total) {
      expect(total, 300000);
      last = sent;
    });
    expect(last, 300000);
    expect(r, {'name': 'up.bin', 'size': 300000});
    expect(fake.uploaded, src.readAsBytesSync());

    final dest = File('${dir.path}/down.bin');
    var got = 0;
    await s.download('/api/files/download?root=/mnt/user&path=x/up.bin', dest, progress: (g, total) => got = g);
    expect(dest.readAsBytesSync(), src.readAsBytesSync());
    expect(got, 300000);
    expect(File('${dest.path}.part').existsSync(), isFalse);

    await expectLater(s.download('/api/files/download?root=/mnt/user&path=missing', File('${dir.path}/m.bin')),
        throwsA(isA<ApiError>().having((e) => e.key, 'key', 'error.not_found')));
    expect(File('${dir.path}/m.bin').existsSync(), isFalse);
    expect(File('${dir.path}/m.bin.part').existsSync(), isFalse);
  });
}
