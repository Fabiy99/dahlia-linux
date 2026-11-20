// USB Creator (Windows): downloads the installer ISO of the latest release
// from GitHub, checks it against the release's .sha256 file and writes it to
// a USB stick. Listing USB disks needs no rights; writing runs in an
// elevated PowerShell (UAC prompt), which reports its progress as JSON in a
// file the app polls. The writer refuses disks that are not on USB or whose
// size changed since the user picked them, and reads the stick back to
// compare it with the ISO.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';

const releasesUrl = 'https://api.github.com/repos/Fabiy99/dahlia-linux/releases/latest';

class IsoRelease {
  const IsoRelease({required this.version, required this.name, required this.url, required this.size, required this.sumUrl});

  final String version;
  final String name;
  final Uri url;
  final int size;
  final Uri sumUrl;
}

/// The ISO and its checksum file in a release from the GitHub API; null if missing.
IsoRelease? parseRelease(Object? json) {
  if (json is! Map || json['assets'] is! List) return null;
  final assets = (json['assets'] as List).whereType<Map>();
  Map? iso;
  Map? sum;
  for (final a in assets) {
    final name = '${a['name'] ?? ''}';
    if (RegExp(r'^dahlia-linux-[0-9.]+-amd64\.iso$').hasMatch(name)) iso = a;
  }
  if (iso == null) return null;
  for (final a in assets) {
    if (a['name'] == '${iso['name']}.sha256') sum = a;
  }
  final url = Uri.tryParse('${iso['browser_download_url'] ?? ''}');
  final sumUrl = Uri.tryParse('${sum?['browser_download_url'] ?? ''}');
  if (sum == null || url == null || sumUrl == null || url.scheme != 'https' || sumUrl.scheme != 'https') return null;
  final tag = '${json['tag_name'] ?? ''}';
  return IsoRelease(
    version: tag.startsWith('v') ? tag.substring(1) : tag,
    name: '${iso['name']}',
    url: url,
    size: iso['size'] is int ? iso['size'] as int : 0,
    sumUrl: sumUrl,
  );
}

/// SHA-256 for [file] from a sha256sum line (hex digest, two spaces, name); null if absent.
String? parseSha256(String text, String file) {
  for (final line in const LineSplitter().convert(text)) {
    final m = RegExp(r'^([0-9a-fA-F]{64}) [ *]?(.+)$').firstMatch(line.trim());
    if (m != null && m[2]!.trim() == file) return m[1]!.toLowerCase();
  }
  return null;
}

class UsbDisk {
  const UsbDisk(this.number, this.name, this.size);

  final int number;
  final String name;
  final int size;
}

/// Output of Get-Disk | ConvertTo-Json: one object or a list.
List<UsbDisk> parseDisks(String text) {
  Object? data;
  try {
    data = jsonDecode(text.trim().isEmpty ? '[]' : text);
  } on FormatException {
    return const [];
  }
  final list = data is List ? data : [data];
  return [
    for (final d in list.whereType<Map>())
      if (d['Number'] is int && d['Size'] is int && (d['Size'] as int) > 0)
        UsbDisk(d['Number'] as int, '${d['FriendlyName'] ?? ''}'.trim(), d['Size'] as int),
  ];
}

class WriteProgress {
  const WriteProgress(this.state, {this.done = 0, this.total = 0, this.message = ''});

  /// preparing, writing, verifying, done or error.
  final String state;
  final int done;
  final int total;
  final String message;

  double? get fraction => total > 0 ? (done / total).clamp(0, 1).toDouble() : null;
}

WriteProgress? parseProgress(String text) {
  try {
    final m = jsonDecode(text);
    if (m is! Map || m['state'] is! String) return null;
    return WriteProgress(
      m['state'] as String,
      done: (m['done'] as num?)?.toInt() ?? 0,
      total: (m['total'] as num?)?.toInt() ?? 0,
      message: '${m['message'] ?? ''}',
    );
  } on FormatException {
    return null;
  }
}

/// PowerShell that lists the USB disks (no rights needed).
const listDisksScript = r"Get-Disk | Where-Object BusType -eq 'USB' | "
    r'Select-Object Number,FriendlyName,Size | ConvertTo-Json -Compress';

/// The elevated writer, assets/usb-writer.ps1 (also run by CI against a virtual disk).
const writerAsset = 'assets/usb-writer.ps1';


/// Arguments for powershell.exe that start the writer elevated and wait for it.
List<String> elevateArgs(String script, UsbDisk disk, String iso, String progress) {
  // Start-Process joins the arguments with spaces: paths go in double quotes,
  // each argument in single quotes for the -Command line.
  String q(String s) => "'${s.replaceAll("'", "''")}'";
  final inner = [
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', '"$script"',
    '-Disk', '${disk.number}', '-Size', '${disk.size}', '-Iso', '"$iso"', '-Progress', '"$progress"',
  ];
  return [
    '-NoProfile',
    '-NonInteractive',
    '-Command',
    'Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -ArgumentList ${inner.map(q).join(',')}',
  ];
}

class UsbError implements Exception {
  UsbError(this.key, [this.detail = '']);

  /// Locale key.
  final String key;
  final String detail;
}

/// Downloads [url] to [to], calling [onProgress] with the bytes so far; returns the SHA-256.
Future<String> download(HttpClient http, Uri url, File to, void Function(int done) onProgress) async {
  final req = await http.getUrl(url);
  req.followRedirects = true;
  req.maxRedirects = 5;
  final resp = await req.close();
  if (resp.statusCode != 200) {
    await resp.drain<void>();
    throw UsbError('usb.download_failed', 'HTTP ${resp.statusCode}');
  }
  final sink = to.openWrite();
  final digest = _DigestSink();
  final hasher = sha256.startChunkedConversion(digest);
  var done = 0;
  try {
    await for (final chunk in resp) {
      sink.add(chunk);
      hasher.add(chunk);
      done += chunk.length;
      onProgress(done);
    }
  } finally {
    await sink.close();
  }
  hasher.close();
  return digest.value.toString();
}

class _DigestSink implements Sink<Digest> {
  late Digest value;

  @override
  void add(Digest data) => value = data;

  @override
  void close() {}
}

/// SHA-256 of a file the user chose.
Future<String> fileSha256(File f) async => (await sha256.bind(f.openRead()).first).toString();

/// The Windows side: disks via Get-Disk, writing through the elevated script.
class WindowsUsb {
  WindowsUsb({Directory? temp}) : temp = temp ?? Directory.systemTemp;

  final Directory temp;

  Future<List<UsbDisk>> disks() async {
    final r = await Process.run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', listDisksScript]);
    if (r.exitCode != 0) throw UsbError('usb.list_failed', '${r.stderr}'.trim());
    return parseDisks('${r.stdout}');
  }

  /// Starts the writer; [onProgress] gets each report until done or error.
  Future<WriteProgress> write(String writer, UsbDisk disk, String iso, void Function(WriteProgress p) onProgress) async {
    final dir = await temp.createTemp('dahlia-usb');
    final script = File('${dir.path}${Platform.pathSeparator}write.ps1');
    final progress = File('${dir.path}${Platform.pathSeparator}progress.json');
    await script.writeAsString(writer);
    WriteProgress? last;
    final poll = Timer.periodic(const Duration(milliseconds: 400), (_) {
      try {
        final p = parseProgress(progress.readAsStringSync());
        if (p != null) onProgress(last = p);
      } on FileSystemException {
        return;
      }
    });
    try {
      final r = await Process.run('powershell.exe', elevateArgs(script.path, disk, iso, progress.path));
      try {
        last = parseProgress(await progress.readAsString()) ?? last;
      } on FileSystemException {
        last = null;
      }
      // No report at all: the UAC prompt was declined.
      if (last == null) throw UsbError('usb.cancelled', '${r.stderr}'.trim());
      return last!;
    } finally {
      poll.cancel();
      await dir.delete(recursive: true).catchError((_) => dir);
    }
  }
}
