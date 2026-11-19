// RFB (VNC, RFC 6143) client for the graphical console of a VM, the subset
// QEMU needs: version 3.8 (3.3 accepted), security type None (the port is
// only on 127.0.0.1 of the server, behind the dashboard's sign-in), 32-bit
// true colour, encodings Raw and CopyRect, the DesktopSize pseudo-encoding
// and QEMU's extended key events (scan codes, so the guest's keyboard
// layout applies and not the one QEMU guesses from a keysym).

import 'dart:convert';
import 'dart:typed_data';

class RfbError implements Exception {
  RfbError(this.message);

  final String message;

  @override
  String toString() => 'RfbError: $message';
}

const encRaw = 0;
const encCopyRect = 1;
const encDesktopSize = -223;
const encQemuExtendedKey = -258;

enum _Stage { version, securityTypes, securityType33, securityResult, failReason, serverInit, normal }

class RfbClient {
  RfbClient(this._send);

  final void Function(Uint8List data) _send;

  int width = 0;
  int height = 0;
  String name = '';

  /// Frame buffer, RGBA, width * height * 4.
  Uint8List pixels = Uint8List(0);

  /// The server sends scan codes along with keysyms (QEMU extended key events).
  bool extendedKeys = false;

  void Function()? onReady;

  /// A frame buffer update is complete.
  void Function()? onFrame;

  /// The size changed (also the first time).
  void Function()? onResize;

  void Function(String message)? onError;

  _Stage _stage = _Stage.version;
  bool _v33 = false;
  bool _failed = false;

  Uint8List _buf = Uint8List(64 * 1024);
  int _start = 0;
  int _end = 0;

  // A frame buffer update in progress: rectangles still to come.
  int _rects = 0;

  int get _avail => _end - _start;

  /// Bytes from the server.
  void add(List<int> data) {
    if (_failed) return;
    _append(data);
    try {
      while (_step()) {}
    } on RfbError catch (e) {
      _failed = true;
      onError?.call(e.message);
    }
  }

  void _append(List<int> data) {
    if (_end + data.length > _buf.length) {
      final need = _avail + data.length;
      final next = need > _buf.length ~/ 2 ? Uint8List(need * 2) : _buf;
      next.setRange(0, _avail, _buf, _start);
      _buf = next;
      _end = _avail;
      _start = 0;
    }
    _buf.setRange(_end, _end + data.length, data);
    _end += data.length;
  }

  int _u8(int at) => _buf[_start + at];
  int _u16(int at) => (_buf[_start + at] << 8) | _buf[_start + at + 1];
  int _u32(int at) => (_u16(at) << 16) | _u16(at + 2);
  int _s32(int at) => _u32(at).toSigned(32);

  void _skip(int n) => _start += n;

  bool _step() {
    switch (_stage) {
      case _Stage.version:
        if (_avail < 12) return false;
        final text = ascii.decode(Uint8List.sublistView(_buf, _start, _start + 12), allowInvalid: true);
        final m = RegExp(r'^RFB (\d{3})\.(\d{3})\n$').firstMatch(text);
        if (m == null) throw RfbError('not a VNC server');
        final minor = int.parse(m[1]!) > 3 ? 8 : int.parse(m[2]!);
        _skip(12);
        _v33 = minor < 7;
        _send(ascii.encode(_v33 ? 'RFB 003.003\n' : 'RFB 003.008\n'));
        _stage = _v33 ? _Stage.securityType33 : _Stage.securityTypes;
        return true;
      case _Stage.securityTypes:
        if (_avail < 1) return false;
        final n = _u8(0);
        if (n == 0) {
          _skip(1);
          _stage = _Stage.failReason;
          return true;
        }
        if (_avail < 1 + n) return false;
        final types = [for (var i = 0; i < n; i++) _u8(1 + i)];
        _skip(1 + n);
        if (!types.contains(1)) throw RfbError('the server asks for a password');
        _send(Uint8List.fromList([1]));
        _stage = _Stage.securityResult;
        return true;
      case _Stage.securityType33:
        if (_avail < 4) return false;
        final type = _u32(0);
        _skip(4);
        if (type == 0) {
          _stage = _Stage.failReason;
          return true;
        }
        if (type != 1) throw RfbError('the server asks for a password');
        _clientInit();
        return true;
      case _Stage.securityResult:
        if (_avail < 4) return false;
        final result = _u32(0);
        _skip(4);
        if (result != 0) {
          _stage = _Stage.failReason;
          return true;
        }
        _clientInit();
        return true;
      case _Stage.failReason:
        if (_avail < 4) return false;
        final len = _u32(0);
        if (_avail < 4 + len) return false;
        final reason = utf8.decode(Uint8List.sublistView(_buf, _start + 4, _start + 4 + len), allowMalformed: true);
        throw RfbError(reason.isEmpty ? 'refused' : reason);
      case _Stage.serverInit:
        if (_avail < 24) return false;
        final len = _u32(20);
        if (_avail < 24 + len) return false;
        final w = _u16(0);
        final h = _u16(2);
        name = utf8.decode(Uint8List.sublistView(_buf, _start + 24, _start + 24 + len), allowMalformed: true);
        _skip(24 + len);
        _resize(w, h);
        _setup();
        _stage = _Stage.normal;
        onReady?.call();
        requestUpdate(incremental: false);
        return true;
      case _Stage.normal:
        return _rects > 0 ? _rect() : _message();
    }
  }

  void _clientInit() {
    _send(Uint8List.fromList([1])); // shared
    _stage = _Stage.serverInit;
  }

  void _setup() {
    // SetPixelFormat: 32 bpp, depth 24, little endian, true colour, R at bit 0,
    // G at 8, B at 16, so the bytes arrive as R, G, B, X.
    final pf = ByteData(20)
      ..setUint8(0, 0)
      ..setUint8(4, 32)
      ..setUint8(5, 24)
      ..setUint8(6, 0)
      ..setUint8(7, 1)
      ..setUint16(8, 255)
      ..setUint16(10, 255)
      ..setUint16(12, 255)
      ..setUint8(14, 0)
      ..setUint8(15, 8)
      ..setUint8(16, 16);
    _send(pf.buffer.asUint8List());
    const encodings = [encRaw, encCopyRect, encDesktopSize, encQemuExtendedKey];
    final se = ByteData(4 + 4 * encodings.length)
      ..setUint8(0, 2)
      ..setUint16(2, encodings.length);
    for (var i = 0; i < encodings.length; i++) {
      se.setInt32(4 + 4 * i, encodings[i]);
    }
    _send(se.buffer.asUint8List());
  }

  void _resize(int w, int h) {
    width = w;
    height = h;
    pixels = Uint8List(w * h * 4);
    for (var i = 3; i < pixels.length; i += 4) {
      pixels[i] = 255;
    }
    onResize?.call();
  }

  bool _message() {
    if (_avail < 1) return false;
    switch (_u8(0)) {
      case 0: // FramebufferUpdate
        if (_avail < 4) return false;
        _rects = _u16(2);
        _skip(4);
        if (_rects == 0) _frameDone();
        return true;
      case 1: // SetColourMapEntries: not used with true colour
        if (_avail < 6) return false;
        final n = _u16(4);
        if (_avail < 6 + 6 * n) return false;
        _skip(6 + 6 * n);
        return true;
      case 2: // Bell
        _skip(1);
        return true;
      case 3: // ServerCutText
        if (_avail < 8) return false;
        final len = _u32(4);
        if (_avail < 8 + len) return false;
        _skip(8 + len);
        return true;
      default:
        throw RfbError('unknown message ${_u8(0)}');
    }
  }

  bool _rect() {
    if (_avail < 12) return false;
    final x = _u16(0);
    final y = _u16(2);
    final w = _u16(4);
    final h = _u16(6);
    final enc = _s32(8);
    switch (enc) {
      case encRaw:
        final size = w * h * 4;
        if (_avail < 12 + size) return false;
        if (x + w > width || y + h > height) throw RfbError('rectangle outside the screen');
        final src = _start + 12;
        for (var row = 0; row < h; row++) {
          final from = src + row * w * 4;
          final to = ((y + row) * width + x) * 4;
          pixels.setRange(to, to + w * 4, _buf, from);
          for (var i = to + 3; i < to + w * 4; i += 4) {
            pixels[i] = 255;
          }
        }
        _skip(12 + size);
      case encCopyRect:
        if (_avail < 16) return false;
        final sx = _u16(12);
        final sy = _u16(14);
        _skip(16);
        if (x + w > width || y + h > height || sx + w > width || sy + h > height) {
          throw RfbError('rectangle outside the screen');
        }
        final copy = Uint8List(w * h * 4);
        for (var row = 0; row < h; row++) {
          final from = ((sy + row) * width + sx) * 4;
          copy.setRange(row * w * 4, (row + 1) * w * 4, pixels, from);
        }
        for (var row = 0; row < h; row++) {
          final to = ((y + row) * width + x) * 4;
          pixels.setRange(to, to + w * 4, copy, row * w * 4);
        }
      case encDesktopSize:
        _skip(12);
        _resize(w, h);
      case encQemuExtendedKey:
        _skip(12);
        extendedKeys = true;
      default:
        throw RfbError('unsupported encoding $enc');
    }
    _rects--;
    if (_rects == 0) _frameDone();
    return true;
  }

  void _frameDone() {
    onFrame?.call();
    requestUpdate();
  }

  void requestUpdate({bool incremental = true}) {
    if (_stage != _Stage.normal) return;
    final m = ByteData(10)
      ..setUint8(0, 3)
      ..setUint8(1, incremental ? 1 : 0)
      ..setUint16(6, width)
      ..setUint16(8, height);
    _send(m.buffer.asUint8List());
  }

  /// A key; scanCode (XT, extended keys with 0xe0 in the high byte) when known.
  void key(int keysym, bool down, {int? scanCode}) {
    if (_stage != _Stage.normal) return;
    if (extendedKeys && scanCode != null) {
      // QEMU: 0xe0 prefixed codes are sent as 0x80 | code.
      final code = scanCode > 0xff ? 0x80 | (scanCode & 0x7f) : scanCode;
      final m = ByteData(12)
        ..setUint8(0, 255)
        ..setUint8(1, 0)
        ..setUint16(2, down ? 1 : 0)
        ..setUint32(4, keysym)
        ..setUint32(8, code);
      _send(m.buffer.asUint8List());
      return;
    }
    final m = ByteData(8)
      ..setUint8(0, 4)
      ..setUint8(1, down ? 1 : 0)
      ..setUint32(4, keysym);
    _send(m.buffer.asUint8List());
  }

  /// Buttons: bit 0 left, 1 middle, 2 right, 3 wheel up, 4 wheel down.
  void pointer(int x, int y, int buttons) {
    if (_stage != _Stage.normal) return;
    final m = ByteData(6)
      ..setUint8(0, 5)
      ..setUint8(1, buttons)
      ..setUint16(2, x.clamp(0, width > 0 ? width - 1 : 0))
      ..setUint16(4, y.clamp(0, height > 0 ? height - 1 : 0));
    _send(m.buffer.asUint8List());
  }

  /// Ctrl+Alt+Del, which the desktop would catch itself.
  void ctrlAltDel() {
    const keys = [(0xffe3, 0x1d), (0xffe9, 0x38), (0xffff, 0xe053)];
    for (final (sym, code) in keys) {
      key(sym, true, scanCode: code);
    }
    for (final (sym, code) in keys.reversed) {
      key(sym, false, scanCode: code);
    }
  }
}

// --- keys -------------------------------------------------------------------------

/// X keysyms of keys without a character, by USB HID usage (Flutter's PhysicalKeyboardKey.usbHidUsage).
final hidKeysyms = <int, int>{
  0x00070028: 0xff0d, // Enter
  0x00070029: 0xff1b, // Escape
  0x0007002a: 0xff08, // Backspace
  0x0007002b: 0xff09, // Tab
  0x00070039: 0xffe5, // Caps Lock
  0x00070046: 0xff61, // Print
  0x00070047: 0xff14, // Scroll Lock
  0x00070048: 0xff13, // Pause
  0x00070049: 0xff63, // Insert
  0x0007004a: 0xff50, // Home
  0x0007004b: 0xff55, // Page Up
  0x0007004c: 0xffff, // Delete
  0x0007004d: 0xff57, // End
  0x0007004e: 0xff56, // Page Down
  0x0007004f: 0xff53, // Right
  0x00070050: 0xff51, // Left
  0x00070051: 0xff54, // Down
  0x00070052: 0xff52, // Up
  0x00070053: 0xff7f, // Num Lock
  0x00070058: 0xff8d, // Numpad Enter
  0x00070065: 0xff67, // Menu
  0x000700e0: 0xffe3, // Control L
  0x000700e1: 0xffe1, // Shift L
  0x000700e2: 0xffe9, // Alt L
  0x000700e3: 0xffeb, // Super L
  0x000700e4: 0xffe4, // Control R
  0x000700e5: 0xffe2, // Shift R
  0x000700e6: 0xfe03, // AltGr
  0x000700e7: 0xffec, // Super R
  for (var i = 0; i < 12; i++) 0x0007003a + i: 0xffbe + i, // F1-F12
};

/// XT scan codes (set 1) by USB HID usage; 0xe0xx for extended keys.
const hidScanCodes = <int, int>{
  // Letters a-z
  0x00070004: 0x1e, 0x00070005: 0x30, 0x00070006: 0x2e, 0x00070007: 0x20, 0x00070008: 0x12,
  0x00070009: 0x21, 0x0007000a: 0x22, 0x0007000b: 0x23, 0x0007000c: 0x17, 0x0007000d: 0x24,
  0x0007000e: 0x25, 0x0007000f: 0x26, 0x00070010: 0x32, 0x00070011: 0x31, 0x00070012: 0x18,
  0x00070013: 0x19, 0x00070014: 0x10, 0x00070015: 0x13, 0x00070016: 0x1f, 0x00070017: 0x14,
  0x00070018: 0x16, 0x00070019: 0x2f, 0x0007001a: 0x11, 0x0007001b: 0x2d, 0x0007001c: 0x15,
  0x0007001d: 0x2c,
  // 1-9, 0
  0x0007001e: 0x02, 0x0007001f: 0x03, 0x00070020: 0x04, 0x00070021: 0x05, 0x00070022: 0x06,
  0x00070023: 0x07, 0x00070024: 0x08, 0x00070025: 0x09, 0x00070026: 0x0a, 0x00070027: 0x0b,
  0x00070028: 0x1c, 0x00070029: 0x01, 0x0007002a: 0x0e, 0x0007002b: 0x0f, 0x0007002c: 0x39,
  0x0007002d: 0x0c, 0x0007002e: 0x0d, 0x0007002f: 0x1a, 0x00070030: 0x1b, 0x00070031: 0x2b,
  0x00070033: 0x27, 0x00070034: 0x28, 0x00070035: 0x29, 0x00070036: 0x33, 0x00070037: 0x34,
  0x00070038: 0x35, 0x00070039: 0x3a,
  // F1-F10, F11, F12
  0x0007003a: 0x3b, 0x0007003b: 0x3c, 0x0007003c: 0x3d, 0x0007003d: 0x3e, 0x0007003e: 0x3f,
  0x0007003f: 0x40, 0x00070040: 0x41, 0x00070041: 0x42, 0x00070042: 0x43, 0x00070043: 0x44,
  0x00070044: 0x57, 0x00070045: 0x58,
  0x00070046: 0xe037, 0x00070047: 0x46, 0x00070049: 0xe052, 0x0007004a: 0xe047,
  0x0007004b: 0xe049, 0x0007004c: 0xe053, 0x0007004d: 0xe04f, 0x0007004e: 0xe051,
  0x0007004f: 0xe04d, 0x00070050: 0xe04b, 0x00070051: 0xe050, 0x00070052: 0xe048,
  // Numpad
  0x00070053: 0x45, 0x00070054: 0xe035, 0x00070055: 0x37, 0x00070056: 0x4a, 0x00070057: 0x4e,
  0x00070058: 0xe01c, 0x00070059: 0x4f, 0x0007005a: 0x50, 0x0007005b: 0x51, 0x0007005c: 0x4b,
  0x0007005d: 0x4c, 0x0007005e: 0x4d, 0x0007005f: 0x47, 0x00070060: 0x48, 0x00070061: 0x49,
  0x00070062: 0x52, 0x00070063: 0x53,
  0x00070064: 0x56, // the key left of Z on ISO keyboards (< > |)
  0x00070065: 0xe05d,
  // Modifiers
  0x000700e0: 0x1d, 0x000700e1: 0x2a, 0x000700e2: 0x38, 0x000700e3: 0xe05b,
  0x000700e4: 0xe01d, 0x000700e5: 0x36, 0x000700e6: 0xe038, 0x000700e7: 0xe05c,
};

/// Keysym of a key: the special keys by their position, others by the character.
/// [character] is what the key typed, [keyId] Flutter's logical key id.
int? keysymFor(int hidUsage, String? character, int keyId) {
  final special = hidKeysyms[hidUsage];
  if (special != null) return special;
  int? cp;
  if (character != null && character.runes.length == 1 && character.runes.first >= 0x20) {
    cp = character.runes.first;
  } else if (keyId >= 0x20 && keyId < 0x110000) {
    // With Ctrl held, the character is a control code: take the key's own.
    cp = keyId;
  }
  if (cp == null || cp == 0x7f) return null;
  return cp < 0x100 ? cp : 0x01000000 | cp;
}
