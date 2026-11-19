import 'dart:convert';
import 'dart:typed_data';

import 'package:dahlia/src/rfb.dart';
import 'package:flutter_test/flutter_test.dart';

Uint8List u16(int v) => Uint8List(2)..buffer.asByteData().setUint16(0, v);
Uint8List u32(int v) => Uint8List(4)..buffer.asByteData().setUint32(0, v);
Uint8List s32(int v) => Uint8List(4)..buffer.asByteData().setInt32(0, v);

List<int> rect(int x, int y, int w, int h, int enc, [List<int> body = const []]) =>
    [...u16(x), ...u16(y), ...u16(w), ...u16(h), ...s32(enc), ...body];

List<int> serverInit(int w, int h, String name) =>
    [...u16(w), ...u16(h), ...List.filled(16, 0), ...u32(name.length), ...utf8.encode(name)];

void main() {
  late List<Uint8List> sent;
  late RfbClient c;
  late List<String> events;

  setUp(() {
    sent = [];
    events = [];
    c = RfbClient(sent.add)
      ..onReady = (() => events.add('ready'))
      ..onFrame = (() => events.add('frame'))
      ..onResize = (() => events.add('resize ${c.width}x${c.height}'))
      ..onError = ((m) => events.add('error $m'));
  });

  void handshake() {
    c.add(ascii.encode('RFB 003.008\n'));
    c.add([2, 2, 1]); // VNC auth and None
    c.add(u32(0));
    c.add(serverInit(4, 2, 'win'));
  }

  test('handshake: version 3.8, no password, our pixel format and encodings', () {
    // Bytes may come in any pieces.
    for (final b in ascii.encode('RFB 003.008\n')) {
      c.add([b]);
    }
    expect(ascii.decode(sent.single), 'RFB 003.008\n');
    c.add([2, 2, 1]);
    expect(sent.last, [1]);
    c.add([...u32(0), ...serverInit(4, 2, 'win').sublist(0, 10)]);
    expect(sent.last, [1]); // ClientInit: shared
    expect(events, isEmpty);
    c.add(serverInit(4, 2, 'win').sublist(10));
    expect([c.width, c.height, c.name], [4, 2, 'win']);
    expect(events, ['resize 4x2', 'ready']);
    final pf = sent[sent.length - 3];
    expect([pf[0], pf[4], pf[5], pf[7], pf[14], pf[15], pf[16]], [0, 32, 24, 1, 0, 8, 16]);
    final enc = sent[sent.length - 2].buffer.asByteData();
    expect([for (var i = 0; i < 4; i++) enc.getInt32(4 + 4 * i)], [0, 1, -223, -258]);
    expect(sent.last, [3, 0, 0, 0, 0, 0, 0, 4, 0, 2]); // full update request
  });

  test('raw and copy rectangles fill the frame buffer, then the next request', () {
    handshake();
    sent.clear();
    c.add([0, 0, ...u16(2)]);
    c.add(rect(0, 0, 2, 1, encRaw, [10, 20, 30, 0, 40, 50, 60, 0]));
    expect(events.last, 'ready');
    c.add(rect(2, 1, 2, 1, encCopyRect, [...u16(0), ...u16(0)]));
    expect(events.last, 'frame');
    expect(c.pixels.sublist(0, 8), [10, 20, 30, 255, 40, 50, 60, 255]);
    expect(c.pixels.sublist(24, 32), [10, 20, 30, 255, 40, 50, 60, 255]);
    expect(sent.last, [3, 1, 0, 0, 0, 0, 0, 4, 0, 2]); // incremental
  });

  test('desktop size, bell, clipboard and extended keys', () {
    handshake();
    c.add([2, 3, 0, 0, 0, ...u32(3), ...ascii.encode('abc')]);
    c.add([0, 0, ...u16(2), ...rect(0, 0, 800, 600, encDesktopSize), ...rect(0, 0, 0, 0, encQemuExtendedKey)]);
    expect([c.width, c.height, c.pixels.length, c.extendedKeys], [800, 600, 800 * 600 * 4, true]);
    sent.clear();
    c.key(0x61, true, scanCode: 0x1e);
    expect(sent.last, [255, 0, 0, 1, 0, 0, 0, 0x61, 0, 0, 0, 0x1e]);
    c.ctrlAltDel();
    expect(sent.length, 7);
    expect(sent[3].sublist(8), [0, 0, 0, 0xd3]); // Delete: 0xe0 0x53
    c.pointer(900, -5, 1);
    expect(sent.last, [5, 1, ...u16(799), ...u16(0)]);
  });

  test('a server that wants a password or is not VNC is refused', () {
    c.add(ascii.encode('RFB 003.008\n'));
    c.add([1, 2]);
    expect(events, ['error the server asks for a password']);
    c = RfbClient(sent.add)..onError = ((m) => events.add(m));
    c.add(ascii.encode('HTTP/1.1 400'));
    expect(events.last, 'not a VNC server');
    c = RfbClient(sent.add)..onError = ((m) => events.add(m));
    c.add([...ascii.encode('RFB 003.008\n'), 0, ...u32(4), ...ascii.encode('busy')]);
    expect(events.last, 'busy');
  });

  test('keysyms: special keys by position, others by character', () {
    expect(keysymFor(0x00070028, '\r', 0x10000000d), 0xff0d);
    expect(keysymFor(0x00070004, 'a', 0x61), 0x61);
    expect(keysymFor(0x00070004, '\x01', 0x61), 0x61); // with Ctrl
    expect(keysymFor(0x00070034, 'ä', 0xe4), 0xe4);
    expect(keysymFor(0x00070008, '€', 0x65), 0x010020ac);
    expect(keysymFor(0x00070000, null, 0x100000000), isNull);
    expect(hidScanCodes[0x0007004c], 0xe053);
  });
}
