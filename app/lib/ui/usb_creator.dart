// USB Creator on the connect page (Windows): installer ISO from the latest
// release or a file of one's own, then a USB stick, then writing with the
// UAC prompt. See lib/src/usb_creator.dart. Optionally the stick installs a
// server without a screen: the app then picks the disk with the installer
// and pairs on its own (lib/src/headless.dart).

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';
import 'package:path_provider/path_provider.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/headless.dart';
import '../src/pairing.dart';
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

  // Install without a screen.
  bool headless = false;
  final _host = TextEditingController(text: 'dahlia');
  final _account = TextEditingController();
  final _pw = TextEditingController();
  final _pw2 = TextEditingController();
  final _confirm = TextEditingController();
  String keymap = keymaps.first;
  String timezone = timezones.first;
  HeadlessServer? server;
  String setupCode = '';
  HeadlessDisk? target;
  bool pairing = false;
  String? pairError;

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
    unawaited(server?.stop());
    for (final t in [_host, _account, _pw, _pw2, _confirm]) {
      t.dispose();
    }
    super.dispose();
  }

  /// Locale key of the first problem in the headless fields, or null.
  String? _headlessProblem() {
    if (!validHostname(_host.text.trim())) return 'headless.bad_host';
    if (!validAccount(_account.text.trim())) return 'headless.bad_account';
    if (!validPassword(_pw.text)) return 'headless.bad_password';
    if (_pw.text != _pw2.text) return 'headless.password_mismatch';
    return null;
  }

  /// Starts the endpoint for the installer and fills the slots in a copy of the ISO.
  Future<String> _prepareHeadless(String path) async {
    await server?.stop();
    final srv = HeadlessServer(newToken(), onChange: () {
      if (mounted) setState(() {});
      if (server?.step == 'finished') unawaited(_pair());
    });
    final port = await srv.start();
    final pcs = await localEndpoints(port);
    if (pcs.isEmpty) throw UsbError('headless.no_network');
    final copy = File('$path.headless.iso');
    final config = headlessConfig(
        hostname: _host.text.trim(), timezone: timezone, keymap: keymap, pcs: pcs, token: srv.token);
    try {
      await patchIso(File(path), copy, headlessSlots(config));
    } on SlotError {
      await srv.stop();
      throw UsbError('headless.old_iso');
    }
    setupCode = newSetupCode();
    server = srv;
    return copy.path;
  }

  void _install() {
    final srv = server;
    final d = target;
    if (srv == null || d == null) return;
    if (d.hasData && _confirm.text.trim() != d.confirmWord) {
      setState(() => error = 'headless.confirm_wrong');
      return;
    }
    setState(() => error = null);
    srv.answer(answerText(
        disk: d, account: _account.text.trim(), password: _pw.text, code: setupCode, confirm: _confirm.text.trim()));
  }

  /// After the reboot: the server comes up under its name or its old address.
  Future<void> _pair() async {
    final srv = server;
    if (srv == null || pairing || srv.fingerprint.isEmpty) return;
    setState(() {
      pairing = true;
      pairError = null;
    });
    final hosts = ['${_host.text.trim()}.local', ?srv.installerIp];
    for (var i = 0; i < 90 && mounted; i++) {
      for (final h in hosts) {
        try {
          await c.pairHeadless(h, srv.fingerprint, setupCode, _account.text.trim(), _pw.text);
          return;
        } on PairingError catch (e) {
          if (e.key == 'certificate') {
            if (mounted) setState(() => pairError = 'headless.pair_mismatch');
            return;
          }
        } on IOException {
          continue;
        }
      }
      await Future<void>.delayed(const Duration(seconds: 10));
    }
    if (mounted) {
      setState(() {
        pairing = false;
        pairError = 'headless.pair_timeout';
      });
    }
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
      final image = headless ? await _prepareHeadless(path) : path;
      final last = await _usb.write(writer, d, image, (pr) {
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

  Widget _field(TextEditingController t, String label, {bool secret = false}) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: TextField(
          controller: t,
          obscureText: secret,
          enabled: !working && (server == null || t == _confirm),
          onChanged: (_) => setState(() {}),
          decoration: InputDecoration(labelText: c.t(label), isDense: true),
        ),
      );

  Widget _choice(String label, String value, List<String> values, void Function(String v) set) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: DropdownButtonFormField<String>(
          initialValue: value,
          isDense: true,
          decoration: InputDecoration(labelText: c.t(label), isDense: true),
          items: [for (final v in values) DropdownMenuItem(value: v, child: Text(v))],
          onChanged: working || server != null ? null : (v) => setState(() => set(v ?? value)),
        ),
      );

  List<Widget> _headlessFields(TextStyle muted) => [
        SwitchListTile(
          value: headless,
          contentPadding: EdgeInsets.zero,
          title: Text(c.t('headless.switch')),
          subtitle: Text(c.t('headless.switch_hint'), style: muted),
          onChanged: working || server != null ? null : (v) => setState(() => headless = v),
        ),
        if (headless) ...[
          _field(_host, 'headless.hostname'),
          _field(_account, 'headless.account'),
          _field(_pw, 'headless.password', secret: true),
          _field(_pw2, 'headless.password_again', secret: true),
          _choice('headless.keymap', keymap, keymaps, (v) => keymap = v),
          _choice('headless.timezone', timezone, timezones, (v) => timezone = v),
        ],
      ];

  List<Widget> _headlessStep(Palette p, TextStyle muted) {
    final srv = server!;
    final disks = srv.disks;
    if (pairing) return [Busy(c.t('headless.pairing', {'host': '${_host.text.trim()}.local'}))];
    if (pairError != null) {
      return [
        Text(c.t(pairError!), style: TextStyle(color: p.danger, height: 1.4)),
        const SizedBox(height: 6),
        SelectableText(c.t('headless.manual', {'host': '${_host.text.trim()}.local', 'code': setupCode}), style: muted),
        const SizedBox(height: 8),
        OutlinedButton(onPressed: _pair, child: Text(c.t('headless.pair_again'))),
      ];
    }
    if (srv.answered || (srv.step != 'hello' && srv.step.isNotEmpty)) {
      final n = headlessSteps.indexOf(srv.step);
      return [
        LinearProgressIndicator(
          value: n < 0 ? null : (n + 1) / headlessSteps.length,
          color: p.accent,
          backgroundColor: p.sub,
        ),
        const SizedBox(height: 6),
        Text(c.t(srv.answered && srv.step == 'hello' ? 'headless.step.answered' : 'headless.step.${srv.step}'), style: muted),
      ];
    }
    if (disks == null) {
      return [
        Text(c.t('headless.boot'), style: muted),
        const SizedBox(height: 10),
        Busy(c.t('headless.waiting')),
      ];
    }
    return [
      Text(c.t('headless.pick'), style: muted),
      if (srv.rejected != null) Text(c.t('headless.rejected'), style: TextStyle(color: p.danger)),
      RadioGroup<String>(
        groupValue: target?.path,
        onChanged: (v) => setState(() {
          target = disks.where((d) => d.path == v).firstOrNull;
          _confirm.clear();
        }),
        child: Column(children: [
          for (final d in disks)
            RadioListTile<String>(
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: d.path,
              title: Text('${d.path.replaceFirst('/dev/', '')}  ${bytes(d.size, c.t)}  ${d.model}'),
              subtitle: d.hasData
                  ? Text(c.t('headless.has_data', {'serial': d.confirmWord}), style: TextStyle(color: p.danger))
                  : (d.serial.isEmpty ? null : Text(c.t('headless.serial', {'serial': d.serial}), style: muted)),
            ),
        ]),
      ),
      if (target?.hasData == true) _field(_confirm, 'headless.confirm'),
      const SizedBox(height: 6),
      FilledButton(
        style: FilledButton.styleFrom(backgroundColor: p.danger, foregroundColor: p.accentFg),
        onPressed: target == null ? null : _install,
        child: Text(c.t('headless.install')),
      ),
    ];
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
        ..._headlessFields(muted),
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
          onPressed: working || iso == null || disk == null || (headless && _headlessProblem() != null) ? null : _write,
          child: Text(c.t('usb.write')),
        ),
        if (headless && _headlessProblem() != null && !working && server == null) ...[
          const SizedBox(height: 6),
          Text(c.t(_headlessProblem()!), style: muted),
        ],
      ]),
      if (server != null && finished) _step(p, 4, c.t('headless.step_server'), false, _headlessStep(p, muted)),
      if (error != null) Text(c.t(error!), style: TextStyle(color: p.danger)),
      const SizedBox(height: 8),
      Align(
        alignment: Alignment.centerLeft,
        child: TextButton(onPressed: working ? null : widget.onClose, child: Text(c.t('usb.back'))),
      ),
    ]);
  }
}
