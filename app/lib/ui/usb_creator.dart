// USB Creator on the connect page (Windows): installer ISO from the latest
// release or a file of one's own, then a USB stick, then writing with the
// UAC prompt. See lib/src/usb_creator.dart.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';
import 'package:path_provider/path_provider.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/usb_creator.dart';
import 'theme.dart';
import 'widgets.dart';

class UsbCreatorPanel extends StatefulWidget {
  const UsbCreatorPanel(this.c, {super.key, required this.onClose});

  final Controller c;
  final VoidCallback onClose;

  @override
  State<UsbCreatorPanel> createState() => _UsbCreatorPanelState();
}

class _UsbCreatorPanelState extends State<UsbCreatorPanel> {
  final _http = HttpClient()..userAgent = 'Dahlia-USB-Creator';
  final _usb = WindowsUsb();
  IsoRelease? release;
  String? releaseError;
  String? iso;
  bool isoChecked = false;
  int? downloaded;
  List<UsbDisk>? disks;
  UsbDisk? disk;
  WriteProgress? progress;
  String? error;
  bool working = false;

  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _release();
    _disks();
  }

  @override
  void dispose() {
    _http.close(force: true);
    super.dispose();
  }

  Future<void> _release() async {
    try {
      final req = await _http.getUrl(Uri.parse(releasesUrl));
      req.headers.set('Accept', 'application/vnd.github+json');
      final resp = await req.close();
      final body = await resp.transform(utf8.decoder).join();
      final r = resp.statusCode == 200 ? parseRelease(jsonDecode(body)) : null;
      if (mounted) setState(() => r == null ? releaseError = 'usb.no_release' : release = r);
    } on IOException {
      if (mounted) setState(() => releaseError = 'usb.offline');
    } on FormatException {
      if (mounted) setState(() => releaseError = 'usb.no_release');
    }
  }

  Future<void> _disks() async {
    setState(() => disks = null);
    try {
      final list = await _usb.disks();
      if (!mounted) return;
      setState(() {
        disks = list;
        if (disk != null && !list.any((d) => d.number == disk!.number && d.size == disk!.size)) disk = null;
      });
    } on UsbError catch (e) {
      if (mounted) {
        setState(() {
          disks = const [];
          error = e.key;
        });
      }
    } on ProcessException {
      if (mounted) {
        setState(() {
          disks = const [];
          error = 'usb.list_failed';
        });
      }
    }
  }

  Future<void> _download() async {
    final r = release;
    if (r == null) return;
    setState(() {
      working = true;
      error = null;
      downloaded = 0;
    });
    try {
      final dir = await getDownloadsDirectory() ?? Directory.systemTemp;
      final file = File('${dir.path}${Platform.pathSeparator}${r.name}');
      final sumReq = await _http.getUrl(r.sumUrl);
      final sumResp = await sumReq.close();
      final expected = parseSha256(await sumResp.transform(utf8.decoder).join(), r.name);
      if (expected == null) throw UsbError('usb.no_release');
      // An earlier download that matches is used again.
      final have = await file.exists() && await file.length() == r.size ? await fileSha256(file) : null;
      final sum = have == expected
          ? have!
          : await download(_http, r.url, file, (n) {
              if (mounted) setState(() => downloaded = n);
            });
      if (sum != expected) {
        await file.delete();
        throw UsbError('usb.checksum_failed');
      }
      if (mounted) {
        setState(() {
          iso = file.path;
          isoChecked = true;
        });
      }
    } on UsbError catch (e) {
      if (mounted) setState(() => error = e.key);
    } on IOException {
      if (mounted) setState(() => error = 'usb.download_failed');
    }
    if (mounted) {
      setState(() {
        working = false;
        downloaded = null;
      });
    }
  }

  Future<void> _ownFile() async {
    final f = await openFile(acceptedTypeGroups: const [
      XTypeGroup(label: 'ISO', extensions: ['iso']),
    ]);
    if (f != null && mounted) {
      setState(() {
        iso = f.path;
        isoChecked = false;
      });
    }
  }

  Future<void> _write() async {
    final d = disk;
    final path = iso;
    if (d == null || path == null) return;
    final p = context.palette;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        content: SizedBox(
          width: 420,
          child: Text(c.t('usb.confirm', {'name': _diskName(d)}), style: const TextStyle(height: 1.45)),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text(c.t('usb.cancel'))),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: p.danger, foregroundColor: p.accentFg),
            onPressed: () => Navigator.pop(context, true),
            child: Text(c.t('usb.write')),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() {
      working = true;
      error = null;
      progress = const WriteProgress('preparing');
    });
    try {
      final writer = await rootBundle.loadString(writerAsset);
      final last = await _usb.write(writer, d, path, (pr) {
        if (mounted) setState(() => progress = pr);
      });
      if (mounted) setState(() => progress = last);
    } on UsbError catch (e) {
      if (mounted) {
        setState(() {
          progress = null;
          error = e.key;
        });
      }
    } on ProcessException {
      if (mounted) {
        setState(() {
          progress = null;
          error = 'usb.cancelled';
        });
      }
    }
    if (mounted) setState(() => working = false);
  }

  String _diskName(UsbDisk d) => '${d.name.isEmpty ? c.t('usb.stick') : d.name} (${bytes(d.size, c.t)})';

  Widget _step(Palette p, int n, String title, bool done, List<Widget> body) => Padding(
        padding: const EdgeInsets.only(bottom: 20),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Container(
            width: 28,
            height: 28,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: done ? p.ok : p.navActive, shape: BoxShape.circle),
            child: done
                ? Icon(Icons.check_rounded, size: 18, color: p.accentFg)
                : Text('$n', style: const TextStyle(fontWeight: FontWeight.w700)),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15.5)),
              const SizedBox(height: 8),
              ...body,
            ]),
          ),
        ]),
      );

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final r = release;
    final pr = progress;
    final muted = TextStyle(color: p.muted, height: 1.4);
    final finished = pr?.state == 'done';
    return Panel(c: c, title: c.t('usb.title'), width: 560, children: [
      Text(c.t('usb.intro'), style: muted),
      const SizedBox(height: 22),
      _step(p, 1, c.t('usb.step_iso'), iso != null, [
        if (iso != null) ...[
          Text(iso!, style: const TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12.5)),
          const SizedBox(height: 4),
          Text(c.t(isoChecked ? 'usb.checked' : 'usb.own_file'), style: TextStyle(color: isoChecked ? p.ok : p.muted)),
          const SizedBox(height: 8),
        ] else if (r != null)
          Text(c.t('usb.release', {'version': r.version, 'size': bytes(r.size, c.t)}), style: muted)
        else
          Text(releaseError == null ? c.t('usb.loading') : c.t(releaseError!), style: muted),
        if (downloaded != null && r != null) ...[
          const SizedBox(height: 8),
          LinearProgressIndicator(value: r.size > 0 ? downloaded! / r.size : null, color: p.accent, backgroundColor: p.sub),
          const SizedBox(height: 4),
          Text(c.t('usb.downloading', {'done': bytes(downloaded!, c.t), 'size': bytes(r.size, c.t)}), style: muted),
        ],
        const SizedBox(height: 8),
        Wrap(spacing: 10, runSpacing: 8, children: [
          if (r != null && !isoChecked)
            FilledButton(onPressed: working ? null : _download, child: Text(c.t('usb.download'))),
          OutlinedButton(onPressed: working ? null : _ownFile, child: Text(c.t('usb.choose_file'))),
        ]),
      ]),
      _step(p, 2, c.t('usb.step_stick'), disk != null, [
        if (disks == null)
          Busy(c.t('usb.searching'))
        else if (disks!.isEmpty)
          Text(c.t('usb.no_sticks'), style: muted)
        else
          RadioGroup<int>(
            groupValue: disk?.number,
            onChanged: (n) {
              if (!working) setState(() => disk = disks!.where((d) => d.number == n).firstOrNull);
            },
            child: Column(children: [
              for (final d in disks!)
                RadioListTile<int>(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  value: d.number,
                  enabled: !working,
                  title: Text(_diskName(d)),
                ),
            ]),
          ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton(onPressed: working ? null : _disks, child: Text(c.t('usb.refresh'))),
        ),
      ]),
      _step(p, 3, c.t('usb.step_write'), finished, [
        Text(c.t('usb.warning'), style: muted),
        if (pr != null) ...[
          const SizedBox(height: 10),
          LinearProgressIndicator(
            value: finished ? 1 : pr.fraction,
            color: pr.state == 'error' ? p.danger : p.accent,
            backgroundColor: p.sub,
          ),
          const SizedBox(height: 6),
          Text(
            pr.state == 'error'
                ? c.t('usb.failed', {'error': pr.message})
                : c.t('usb.state.${pr.state}', {'percent': ((pr.fraction ?? 0) * 100).round()}),
            style: TextStyle(color: pr.state == 'error' ? p.danger : (finished ? p.ok : p.muted), height: 1.4),
          ),
        ],
        const SizedBox(height: 10),
        FilledButton(
          onPressed: working || iso == null || disk == null ? null : _write,
          child: Text(c.t('usb.write')),
        ),
      ]),
      if (error != null) Text(c.t(error!), style: TextStyle(color: p.danger)),
      const SizedBox(height: 8),
      Align(
        alignment: Alignment.centerLeft,
        child: TextButton(onPressed: working ? null : widget.onClose, child: Text(c.t('usb.back'))),
      ),
    ]);
  }
}
