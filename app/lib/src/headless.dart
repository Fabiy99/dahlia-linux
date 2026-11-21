// Install without a screen (USB Creator, Windows): the app fills slots in a
// copy of the installer ISO (iso/build-iso.sh) and then serves the
// installer on the server over HTTP. The installer (iso/installer/headless.sh)
// reports the disks, the person picks the system disk here, the account,
// its password and a setup code go back in the answer. At the end the
// installer reports the SHA-256 of the server's CA, so the app pairs and
// finishes the setup on its own.
//
// Nothing secret goes on the stick: it holds the server name, time zone,
// keyboard layout, the PC's addresses and a random token.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

const headlessPort = 8485;
const slotMarker = '#DAHLIA-SLOT:';
const slotSizes = {'headless': 4096, 'grub': 512, 'isolinux': 512};

/// grub entry 4 and the isolinux label of the install without a screen.
const grubSlot = 'set default=4\nset timeout=1\n';
const isolinuxSlot = 'default dahlia-headless\ntimeout 10\n';

const keymaps = ['de', 'ch', 'at', 'us', 'gb', 'fr', 'nl', 'it', 'es'];
const timezones = [
  'Europe/Berlin', 'Europe/Vienna', 'Europe/Zurich', 'Europe/Amsterdam', 'Europe/Brussels',
  'Europe/Paris', 'Europe/London', 'Europe/Rome', 'Europe/Madrid', 'America/New_York',
];

final _hostRe = RegExp(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$');
final _accountRe = RegExp(r'^[a-z][a-z0-9_-]{1,31}$');
const _reserved = {
  'root', 'nobody', 'daemon', 'bin', 'sys', 'adm', 'sudo', 'users', 'staff', 'docker', 'sshd',
  'messagebus', 'dahlia',
};

/// Same rules as valid_hostname in iso/installer/lib.sh.
bool validHostname(String s) => _hostRe.hasMatch(s);

/// Same rules as valid_account in iso/installer/lib.sh.
bool validAccount(String s) =>
    _accountRe.hasMatch(s) && !_reserved.contains(s) && !s.startsWith('dahlia-') && !s.startsWith('systemd-');

bool validPassword(String s) => s.length >= 8 && s.length <= 128 && !s.contains(RegExp(r'[\x00-\x1f\x7f]'));

String _hex(List<int> bytes) => [for (final b in bytes) b.toRadixString(16).padLeft(2, '0')].join();

String newToken([Random? rnd]) {
  final r = rnd ?? Random.secure();
  return _hex([for (var i = 0; i < 16; i++) r.nextInt(256)]);
}

/// Setup code as the dashboard makes it (setupcode.py): ABCDE-FGHJK.
String newSetupCode([Random? rnd]) {
  const alphabet = '23456789ABCDEFGHJKMNPQRSTUVWXYZ';
  final r = rnd ?? Random.secure();
  String part() => String.fromCharCodes([for (var i = 0; i < 5; i++) alphabet.codeUnitAt(r.nextInt(alphabet.length))]);
  return '${part()}-${part()}';
}

/// [body] in a slot of [size] bytes: the rest is "#" lines, as build-iso.sh fills it.
String slotText(String body, int size) {
  final out = StringBuffer(body.endsWith('\n') || body.isEmpty ? body : '$body\n');
  if (utf8.encode(out.toString()).length > size) throw ArgumentError('slot content too long');
  while (out.length < size) {
    final left = size - out.length;
    out.write(left > 1 ? '${'#' * min(63, left - 1)}\n' : '\n');
  }
  return out.toString();
}

/// headless.cfg for headless.sh: KEY=value lines.
String headlessConfig({
  required String hostname,
  required String timezone,
  required String keymap,
  required List<String> pcs,
  required String token,
}) =>
    'HOSTNAME=$hostname\nTIMEZONE=$timezone\nKEYMAP=$keymap\nPC=${pcs.join(' ')}\nTOKEN=$token\n';

/// The three slot contents, ready to patch.
Map<String, String> headlessSlots(String config) => {
      'headless': slotText(config, slotSizes['headless']!),
      'grub': slotText(grubSlot, slotSizes['grub']!),
      'isolinux': slotText(isolinuxSlot, slotSizes['isolinux']!),
    };

/// Offsets of the slots in an ISO image; files start on 2048-byte sectors.
Future<Map<String, int>> findSlots(RandomAccessFile f) async {
  const sector = 2048;
  const chunk = sector * 512;
  final marker = ascii.encode(slotMarker);
  final found = <String, int>{};
  final length = await f.length();
  for (var pos = 0; pos < length; pos += chunk) {
    await f.setPosition(pos);
    final data = await f.read(chunk);
    for (var off = 0; off + 64 <= data.length; off += sector) {
      var match = true;
      for (var i = 0; i < marker.length; i++) {
        if (data[off + i] != marker[i]) {
          match = false;
          break;
        }
      }
      if (!match) continue;
      final head = ascii.decode(data.sublist(off, off + 64), allowInvalid: true).split('\n').first;
      final parts = head.substring(slotMarker.length).split(':');
      if (parts.length == 2 && slotSizes[parts[0]] == int.tryParse(parts[1])) found[parts[0]] = pos + off;
    }
  }
  return found;
}

class SlotError implements Exception {
  SlotError(this.missing);

  final List<String> missing;
}

/// Copies [iso] to [to] and fills the slots there; the original stays as it is.
Future<void> patchIso(File iso, File to, Map<String, String> slots) async {
  await iso.copy(to.path);
  final f = await to.open(mode: FileMode.append);
  try {
    final at = await findSlots(f);
    final missing = [for (final k in slots.keys) if (!at.containsKey(k)) k];
    if (missing.isNotEmpty) throw SlotError(missing);
    for (final e in slots.entries) {
      final bytes = utf8.encode(e.value);
      if (bytes.length != slotSizes[e.key]) throw ArgumentError('slot ${e.key} has the wrong size');
      await f.setPosition(at[e.key]!);
      await f.writeFrom(bytes);
    }
  } finally {
    await f.close();
  }
}

/// The PC's LAN addresses (IPv4, no loopback or link-local) with the port.
Future<List<String>> localEndpoints(int port) async {
  final out = <String>[];
  for (final i in await NetworkInterface.list(type: InternetAddressType.IPv4)) {
    for (final a in i.addresses) {
      if (a.isLoopback || a.isLinkLocal) continue;
      out.add('${a.address}:$port');
    }
  }
  return out;
}

class HeadlessDisk {
  const HeadlessDisk(this.path, this.size, this.model, this.serial, this.hasData);

  final String path;
  final int size;
  final String model;
  final String serial;
  final bool hasData;

  /// What the person types to confirm a disk with data: serial, else the name.
  String get confirmWord => serial.isNotEmpty ? serial : path.replaceFirst('/dev/', '');
}

/// "device.bytes.model.serial.data" joined by "~" (headless.sh).
List<HeadlessDisk> parseDiskList(String text) => [
      for (final rec in text.split('~'))
        if (rec.split('.').length == 5 && RegExp(r'^/dev/[a-z0-9]+$').hasMatch(rec.split('.')[0]))
          HeadlessDisk(
            rec.split('.')[0],
            int.tryParse(rec.split('.')[1]) ?? 0,
            rec.split('.')[2].replaceAll('_', ' '),
            rec.split('.')[3],
            rec.split('.')[4] == '1',
          ),
    ];

String answerText({
  required HeadlessDisk disk,
  required String account,
  required String password,
  required String code,
  String confirm = '',
}) =>
    'DISK=${disk.path}\nACCOUNT=$account\nPASSWORD=${_hex(utf8.encode(password))}\n'
    'SETUP_CODE=$code\nCONFIRM=$confirm\n';

/// The installer's fingerprint (sha256sum, lower-case hex) against the app's (AB:CD:...).
bool sameFingerprint(String installer, String app) =>
    installer.isNotEmpty && installer.toLowerCase() == app.replaceAll(':', '').toLowerCase();

/// Steps the installer reports, in order (headless.sh, late-command.sh).
const headlessSteps = ['hello', 'partition', 'packages', 'finished'];

/// HTTP endpoint for headless.sh. One install at a time, bound to [token].
class HeadlessServer {
  HeadlessServer(this.token, {this.onChange});

  final String token;
  final void Function()? onChange;
  HttpServer? _server;
  List<HeadlessDisk>? disks;
  String? _answer;
  String step = '';
  String? rejected;
  String fingerprint = '';

  /// Address of the server during the install; after the reboot it usually keeps it.
  String? installerIp;

  int? get port => _server?.port;

  Future<int> start({int port = headlessPort}) async {
    for (var p = port; p < port + 10; p++) {
      try {
        _server = await HttpServer.bind(InternetAddress.anyIPv4, p);
        break;
      } on SocketException {
        continue;
      }
    }
    final s = _server;
    if (s == null) throw const SocketException('no free port');
    unawaited(s.forEach(_handle));
    return s.port;
  }

  Future<void> stop() async => _server?.close(force: true);

  /// The person's choice; the installer fetches it on its next poll.
  void answer(String text) {
    _answer = text;
    rejected = null;
    onChange?.call();
  }

  bool get answered => _answer != null;

  /// Handles one request; public for tests.
  Future<void> handleRequest(Uri uri, String? remote, HttpResponse resp) async {
    final seg = uri.pathSegments;
    if (seg.length != 3 || seg[0] != 'headless' || seg[1] != token) {
      resp.statusCode = HttpStatus.notFound;
      await resp.close();
      return;
    }
    switch (seg[2]) {
      case 'hello':
        disks = parseDiskList(uri.queryParameters['disks'] ?? '');
        installerIp = remote;
        step = 'hello';
        resp.write('ok\n');
      case 'answer':
        final a = _answer;
        if (a == null) {
          resp.statusCode = HttpStatus.noContent;
        } else {
          resp.write(a);
        }
      case 'progress':
        final s = uri.queryParameters['step'] ?? '';
        if (s.startsWith('rejected_')) {
          rejected = s.substring(9);
          _answer = null;
        } else if (RegExp(r'^[a-z_]{1,20}$').hasMatch(s)) {
          step = s;
        }
        final fp = uri.queryParameters['fingerprint'] ?? '';
        if (RegExp(r'^[0-9a-f]{64}$').hasMatch(fp)) fingerprint = fp;
        resp.write('ok\n');
      default:
        resp.statusCode = HttpStatus.notFound;
    }
    await resp.close();
    onChange?.call();
  }

  Future<void> _handle(HttpRequest req) async {
    try {
      await handleRequest(req.uri, req.connectionInfo?.remoteAddress.address, req.response);
    } on Exception {
      await req.response.close().catchError((_) {});
    }
  }
}
