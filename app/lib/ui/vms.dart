// VMs area: Incus set up on demand, VMs and system containers with start,
// stop, settings and deletion, the graphical console of a VM (own RFB
// client over the dashboard's WebSocket) and the shell of a container in the
// terminal. Same API and texts as the dashboard's VMs page.

import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';

import '../src/apps.dart';
import '../src/controller.dart';
import '../src/rfb.dart';
import '../src/server.dart';
import '../src/vms.dart';
import 'apps.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class VmsPage extends StatefulWidget {
  const VmsPage(this.c, {super.key});

  final Controller c;

  @override
  State<VmsPage> createState() => _VmsPageState();
}

class _VmsPageState extends State<VmsPage> with AreaCalls {
  VmState? st;
  String location = 'system';
  bool busy = false;
  Timer? _timer;

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    if (!mounted) return;
    final r = await call(() => s.get('/api/vms'), ScaffoldMessenger.of(context));
    if (r is! Map || !mounted) return;
    final next = VmState(r);
    setState(() => st = next);
    _timer?.cancel();
    if (next.busy) _timer = Timer(const Duration(seconds: 4), _load);
  }

  Future<void> _post(String path, [Object? body, String? done]) async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post(path, body), m);
    if (r != null && done != null) say(m, done);
    if (mounted) setState(() => busy = false);
    await _load();
  }

  Future<void> _op(VmInstance v, String op) async {
    final danger = op == 'stop' || op == 'force-stop' || op == 'delete';
    final key = op == 'force-stop' ? 'force_stop' : op;
    if (op != 'start' && !await ask(c.t('vms.confirm_$key', {'name': v.name}), 'vms.$key', danger: danger)) return;
    await _post('/api/vms/${Uri.encodeComponent(v.name)}/$op', null, c.t('vms.done', {'name': v.name}));
  }

  Future<void> _shell(VmInstance v) async {
    final m = ScaffoldMessenger.of(context);
    await Clipboard.setData(ClipboardData(text: v.shell));
    say(m, c.t('vms.shell_copied', {'cmd': v.shell}));
    c.go(Area.terminal);
  }

  Future<void> _create() async {
    final state = st;
    if (state == null) return;
    final body = await showDialog<Map<String, Object?>>(
      context: context,
      builder: (_) => _CreateDialog(c: c, st: state),
    );
    if (body == null || !mounted) return;
    await _post('/api/vms', body, c.t('vms.create_started', {'name': body['name']}));
  }

  Future<void> _settings(VmInstance v) async {
    final body = await showDialog<Map<String, Object?>>(
      context: context,
      builder: (_) => _SettingsDialog(c: c, v: v, maxCpus: st?.cpus ?? 1),
    );
    if (body == null || !mounted) return;
    await _post('/api/vms/${Uri.encodeComponent(v.name)}/settings', body, c.t('vms.saved', {'name': v.name}));
  }

  Future<void> _snapshots(VmInstance v) async {
    await showDialog<void>(context: context, builder: (_) => _SnapshotsDialog(c: c, area: this, name: v.name));
    await _load();
  }

  void _console(VmInstance v) => showDialog<void>(
        context: context,
        builder: (_) => VmConsoleDialog(c: c, name: v.name),
      );

  @override
  Widget build(BuildContext context) {
    final state = st;
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('nav.vms'), hint: c.t('vms.hint'))),
      const SizedBox(height: 28),
      if (state == null)
        Busy(c.t('common.loading'))
      else if (!state.installed)
        _setup(context.palette, state)
      else
        _list(context.palette, state),
    ]);
  }

  Widget _setup(Palette p, VmState state) {
    final places = [('system', '/var/lib/incus', true), ('cache', '/mnt/cache/incus', state.cache)];
    return Glass(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(c.t('vms.setup_title'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Text(c.t('vms.setup_hint'), style: TextStyle(color: p.muted, height: 1.45)),
        const SizedBox(height: 16),
        Text(c.t('vms.location'), style: const TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Wrap(spacing: 10, runSpacing: 10, children: [
          for (final (name, path, ok) in places)
            ChoiceChip(
              label: Text('${c.t('vms.location_$name')}  $path'),
              selected: location == name,
              selectedColor: p.navActive,
              onSelected: ok ? (_) => setState(() => location = name) : null,
            ),
        ]),
        const SizedBox(height: 14),
        if (state.installing)
          Busy(c.t('vms.installing'))
        else ...[
          if (state.installFailed) _error(p, c.t('vms.install_failed')),
          FilledButton(
            onPressed: busy ? null : () => _post('/api/vms/install', {'location': location}, c.t('vms.install_started')),
            child: Text(c.t('vms.install')),
          ),
        ],
      ]),
    );
  }

  Widget _list(Palette p, VmState state) => Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Wrap(spacing: 10, runSpacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
            Text(c.t('vms.instances'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            FilledButton(onPressed: busy ? null : _create, child: Text(c.t('vms.new'))),
            OutlinedButton(onPressed: busy ? null : _load, child: Text(c.t('common.refresh'))),
          ]),
          if (state.error != null) ...[
            const SizedBox(height: 10),
            _error(p, c.t('vms.error', {'error': state.error!})),
          ],
          const SizedBox(height: 14),
          for (final cr in state.creating) _creatingRow(p, cr),
          for (final v in state.instances) _row(p, v),
          if (state.instances.isEmpty && state.creating.isEmpty)
            Text(c.t('vms.none'), style: TextStyle(color: p.muted)),
        ]),
      );

  Widget _error(Palette p, String text) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Text(text, style: TextStyle(color: p.danger)),
      );

  Widget _frame(Palette p, List<Widget> children) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(border: Border.all(color: p.line), borderRadius: BorderRadius.circular(14)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
      );

  Widget _creatingRow(Palette p, VmCreating cr) => _frame(p, [
        Wrap(spacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text(cr.name, style: const TextStyle(fontWeight: FontWeight.w600)),
          Tag(c.t(cr.failed ? 'vms.create_failed' : 'vms.creating'), cr.failed ? p.danger : p.warn),
        ]),
      ]);

  Widget _row(Palette p, VmInstance v) {
    final color = switch (v.status) {
      'running' => p.ok,
      'frozen' => p.warn,
      'error' => p.danger,
      _ => p.muted,
    };
    final facts = [
      if (v.image.isNotEmpty) v.image,
      if (v.cpus != null) c.t('vms.cpus_n', {'n': v.cpus!}),
      if (v.memory != null) c.t('vms.memory_n', {'n': gbText(v.memory!)}),
      c.t(v.lan ? 'vms.network_lan' : 'vms.network_nat'),
      if (v.addresses.isNotEmpty) v.addresses.join(', '),
      if (v.autostart) c.t('vms.autostart_on'),
    ];
    Widget btn(String key, VoidCallback f, {bool danger = false}) => OutlinedButton(
          style: danger ? OutlinedButton.styleFrom(foregroundColor: p.danger) : null,
          onPressed: busy ? null : f,
          child: Text(c.t(key)),
        );
    return _frame(p, [
      Wrap(spacing: 8, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
        Container(width: 9, height: 9, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        Text(v.name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15.5)),
        Tag(c.t(v.vm ? 'vms.kind_vm' : 'vms.kind_container'), p.accent),
        Tag(tv(c, 'vms.status', v.status), color),
        if (v.iso) Tag(c.t('vms.has_iso'), p.muted),
      ]),
      const SizedBox(height: 6),
      Text(facts.join(' · '), style: TextStyle(color: p.muted, fontSize: 13)),
      const SizedBox(height: 10),
      Wrap(spacing: 8, runSpacing: 8, children: [
        if (v.running) ...[
          if (v.console) FilledButton(onPressed: () => _console(v), child: Text(c.t('vms.console'))),
          if (!v.vm) FilledButton(onPressed: () => _shell(v), child: Text(c.t('vms.shell'))),
          btn('vms.restart', () => _op(v, 'restart')),
          btn('vms.stop', () => _op(v, 'stop'), danger: true),
          btn('vms.force_stop', () => _op(v, 'force-stop'), danger: true),
        ] else ...[
          FilledButton(onPressed: busy ? null : () => _op(v, 'start'), child: Text(c.t('vms.start'))),
          if (v.iso) btn('vms.eject', () => _op(v, 'eject')),
          btn('vms.delete', () => _op(v, 'delete'), danger: true),
        ],
        btn('snap.button', () => _snapshots(v)),
        btn('vms.settings', () => _settings(v)),
      ]),
    ]);
  }
}

class _CreateDialog extends StatefulWidget {
  const _CreateDialog({required this.c, required this.st});

  final Controller c;
  final VmState st;

  @override
  State<_CreateDialog> createState() => _CreateDialogState();
}

class _CreateDialogState extends State<_CreateDialog> {
  final name = TextEditingController();
  final cpus = TextEditingController(text: '2');
  final memory = TextEditingController(text: '2');
  final disk = TextEditingController(text: '20');
  bool vm = false;
  bool iso = false;
  late String image = widget.st.images.isEmpty ? 'debian/13' : widget.st.images.first;
  Place? isoFile;
  bool lan = false;
  bool autostart = false;
  bool start = true;
  bool secureboot = true;
  bool windows = false;

  Controller get c => widget.c;

  @override
  void dispose() {
    for (final t in [name, cpus, memory, disk]) {
      t.dispose();
    }
    super.dispose();
  }

  Map<String, Object?>? get _body => vmRequest(
        name: name.text.trim(),
        vm: vm,
        fromIso: iso,
        image: image,
        isoRoot: isoFile?.root,
        isoPath: isoFile?.path,
        cpus: int.tryParse(cpus.text) ?? 0,
        memoryGb: double.tryParse(memory.text.replaceAll(',', '.')) ?? 0,
        diskGb: int.tryParse(disk.text) ?? 0,
        lan: lan,
        autostart: autostart,
        start: start,
        secureboot: secureboot,
        windows: windows,
      );

  Future<void> _pickIso() async {
    final chosen = await showDialog<Place>(
      context: context,
      builder: (_) => FolderPicker(c: c, start: isoFile ?? const Place(arrayRoot, ''), fileExt: '.iso'),
    );
    if (chosen != null) setState(() => isoFile = chosen);
  }

  Widget _field(TextEditingController t, String key, {bool number = true}) => SizedBox(
        width: 180,
        child: TextField(
          controller: t,
          keyboardType: number ? TextInputType.number : null,
          decoration: InputDecoration(labelText: c.t(key), isDense: true),
          onChanged: (_) => setState(() {}),
        ),
      );

  Widget _check(String key, bool value, ValueChanged<bool> on) => Row(mainAxisSize: MainAxisSize.min, children: [
        Checkbox(value: value, onChanged: (v) => setState(() => on(v ?? false))),
        Text(c.t(key)),
      ]);

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final body = _body;
    return AlertDialog(
      title: Text(c.t('vms.new_title')),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            TextField(
              controller: name,
              autofocus: true,
              inputFormatters: [LengthLimitingTextInputFormatter(32)],
              decoration: InputDecoration(labelText: c.t('vms.name'), isDense: true),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: 14),
            SegmentedButton<bool>(
              segments: [
                ButtonSegment(value: false, label: Text(c.t('vms.kind_container'))),
                ButtonSegment(value: true, label: Text(c.t('vms.kind_vm'))),
              ],
              selected: {vm},
              onSelectionChanged: (v) => setState(() => vm = v.first),
            ),
            if (vm) ...[
              const SizedBox(height: 12),
              SegmentedButton<bool>(
                segments: [
                  ButtonSegment(value: false, label: Text(c.t('vms.source_image'))),
                  ButtonSegment(value: true, label: Text(c.t('vms.source_iso'))),
                ],
                selected: {iso},
                onSelectionChanged: (v) => setState(() => iso = v.first),
              ),
            ],
            const SizedBox(height: 12),
            if (vm && iso)
              Wrap(spacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
                Text(isoFile?.full ?? c.t('vms.iso_missing'),
                    style: TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, color: isoFile == null ? p.muted : null)),
                OutlinedButton(onPressed: _pickIso, child: Text(c.t('vms.iso_pick'))),
              ])
            else
              DropdownButtonFormField<String>(
                initialValue: image,
                decoration: InputDecoration(labelText: c.t('vms.image'), isDense: true),
                items: [for (final i in widget.st.images) DropdownMenuItem(value: i, child: Text(i))],
                onChanged: (v) => setState(() => image = v ?? image),
              ),
            const SizedBox(height: 12),
            Wrap(spacing: 14, runSpacing: 12, children: [
              _field(cpus, 'vms.cpus'),
              _field(memory, 'vms.memory'),
              if (vm) _field(disk, 'vms.disk'),
            ]),
            const SizedBox(height: 14),
            SegmentedButton<bool>(
              segments: [
                ButtonSegment(value: false, label: Text(c.t('vms.network_nat'))),
                ButtonSegment(value: true, label: Text(c.t('vms.network_lan')), enabled: widget.st.lan),
              ],
              selected: {lan},
              onSelectionChanged: (v) => setState(() => lan = v.first),
            ),
            const SizedBox(height: 6),
            Text(c.t(lan ? 'vms.network_lan_hint' : 'vms.network_nat_hint'),
                style: TextStyle(color: p.muted, fontSize: 12.5, height: 1.4)),
            const SizedBox(height: 8),
            Wrap(children: [
              _check('vms.autostart', autostart, (v) => autostart = v),
              _check('vms.start_now', start, (v) => start = v),
              if (vm) _check('vms.secureboot', secureboot, (v) => secureboot = v),
              if (vm) _check('vms.windows', windows, (v) => windows = v),
            ]),
            if (vm)
              Text(c.t('vms.windows_hint'), style: TextStyle(color: p.muted, fontSize: 12.5, height: 1.4)),
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          onPressed: body == null ? null : () => Navigator.pop(context, body),
          child: Text(c.t('vms.create')),
        ),
      ],
    );
  }
}

class _SettingsDialog extends StatefulWidget {
  const _SettingsDialog({required this.c, required this.v, required this.maxCpus});

  final Controller c;
  final VmInstance v;
  final int maxCpus;

  @override
  State<_SettingsDialog> createState() => _SettingsDialogState();
}

class _SettingsDialogState extends State<_SettingsDialog> {
  late final cpus = TextEditingController(text: '${widget.v.cpus ?? 1}');
  late final memory = TextEditingController(text: gbText(widget.v.memory ?? 1024));
  late bool autostart = widget.v.autostart;

  Controller get c => widget.c;

  @override
  void dispose() {
    cpus.dispose();
    memory.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final n = int.tryParse(cpus.text) ?? 0;
    final gb = double.tryParse(memory.text.replaceAll(',', '.')) ?? 0;
    final ok = n >= 1 && n <= widget.maxCpus && gb >= .125;
    return AlertDialog(
      title: Text(c.t('vms.settings_title', {'name': widget.v.name})),
      content: SizedBox(
        width: 420,
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          TextField(
            controller: cpus,
            decoration: InputDecoration(labelText: c.t('vms.cpus'), isDense: true),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: memory,
            decoration: InputDecoration(labelText: c.t('vms.memory'), isDense: true),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 8),
          Row(children: [
            Checkbox(value: autostart, onChanged: (v) => setState(() => autostart = v ?? false)),
            Text(c.t('vms.autostart')),
          ]),
          Text(c.t('vms.settings_hint'), style: TextStyle(color: context.palette.muted, fontSize: 12.5)),
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          onPressed: ok
              ? () => Navigator.pop(context, {'cpus': n, 'memory': (gb * 1024).round(), 'autostart': autostart})
              : null,
          child: Text(c.t('common.save')),
        ),
      ],
    );
  }
}

/// Graphical console of a VM in a large dialog.
class VmConsoleDialog extends StatefulWidget {
  const VmConsoleDialog({super.key, required this.c, required this.name});

  final Controller c;
  final String name;

  @override
  State<VmConsoleDialog> createState() => _VmConsoleDialogState();
}

class _VmConsoleDialogState extends State<VmConsoleDialog> {
  WebSocket? _ws;
  RfbClient? _rfb;
  ui.Image? _image;
  bool _decoding = false;
  bool _again = false;
  String? _state;
  int _buttons = 0;
  final _down = <int, int>{};
  final _focus = FocusNode();

  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _state = c.t('vms.console_connecting');
    _connect();
  }

  @override
  void dispose() {
    _ws?.close();
    _image?.dispose();
    _focus.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    try {
      final ws = await c.server!.vmConsole(widget.name);
      if (!mounted) {
        await ws.close();
        return;
      }
      final rfb = RfbClient((data) => ws.add(data))
        ..onReady = (() => setState(() => _state = null))
        ..onFrame = _frame
        ..onError = ((m) {
          if (mounted) setState(() => _state = '${c.t('vms.console_failed')} ($m)');
          ws.close();
        });
      _ws = ws;
      _rfb = rfb;
      ws.listen(
        (msg) {
          if (msg is List<int>) rfb.add(msg);
        },
        onDone: () {
          if (mounted && _state == null) setState(() => _state = c.t('vms.console_closed'));
        },
        onError: (_) {},
        cancelOnError: true,
      );
      _focus.requestFocus();
    } on ApiError {
      if (mounted) setState(() => _state = c.t('vms.console_failed'));
    } on WebSocketException {
      if (mounted) setState(() => _state = c.t('vms.console_failed'));
    }
  }

  /// One picture at a time; updates in between are folded into the next.
  void _frame() {
    final rfb = _rfb;
    if (rfb == null || rfb.width == 0) return;
    if (_decoding) {
      _again = true;
      return;
    }
    _decoding = true;
    final copy = Uint8List.fromList(rfb.pixels);
    ui.decodeImageFromPixels(copy, rfb.width, rfb.height, ui.PixelFormat.rgba8888, (img) {
      _decoding = false;
      if (!mounted) {
        img.dispose();
        return;
      }
      final old = _image;
      setState(() => _image = img);
      old?.dispose();
      if (_again) {
        _again = false;
        _frame();
      }
    });
  }

  KeyEventResult _key(FocusNode _, KeyEvent e) {
    final rfb = _rfb;
    if (rfb == null) return KeyEventResult.ignored;
    final hid = e.physicalKey.usbHidUsage;
    if (e is KeyUpEvent) {
      final sym = _down.remove(hid);
      if (sym != null) rfb.key(sym, false, scanCode: hidScanCodes[hid]);
      return KeyEventResult.handled;
    }
    final sym = _down[hid] ?? keysymFor(hid, e.character, e.logicalKey.keyId);
    if (sym == null) return KeyEventResult.handled;
    _down[hid] = sym;
    rfb.key(sym, true, scanCode: hidScanCodes[hid]);
    return KeyEventResult.handled;
  }

  int _mask(int flutterButtons) =>
      (flutterButtons & kPrimaryButton != 0 ? 1 : 0) |
      (flutterButtons & kMiddleMouseButton != 0 ? 2 : 0) |
      (flutterButtons & kSecondaryButton != 0 ? 4 : 0);

  void _pointer(PointerEvent e) {
    _buttons = _mask(e.buttons);
    _rfb?.pointer(e.localPosition.dx.round(), e.localPosition.dy.round(), _buttons);
  }

  void _scroll(PointerSignalEvent e) {
    if (e is! PointerScrollEvent) return;
    final bit = e.scrollDelta.dy < 0 ? 8 : 16;
    final x = e.localPosition.dx.round();
    final y = e.localPosition.dy.round();
    _rfb?.pointer(x, y, _buttons | bit);
    _rfb?.pointer(x, y, _buttons);
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final size = MediaQuery.of(context).size;
    final img = _image;
    return Dialog(
      insetPadding: const EdgeInsets.all(16),
      child: SizedBox(
        width: size.width - 32,
        height: size.height - 32,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              Expanded(
                child: Text(c.t('vms.console_title', {'name': widget.name}),
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
              ),
              if (_state != null) Text(_state!, style: TextStyle(color: p.muted)),
              const SizedBox(width: 12),
              OutlinedButton(onPressed: () => _rfb?.ctrlAltDel(), child: Text(c.t('vms.ctrl_alt_del'))),
              const SizedBox(width: 8),
              FilledButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.close'))),
            ]),
            const SizedBox(height: 10),
            Expanded(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: ColoredBox(
                  color: Colors.black,
                  child: img == null
                      ? const SizedBox.expand()
                      : Focus(
                          focusNode: _focus,
                          autofocus: true,
                          onKeyEvent: _key,
                          child: MouseRegion(
                            cursor: SystemMouseCursors.precise,
                            child: Center(
                              child: FittedBox(
                                fit: BoxFit.scaleDown,
                                child: Listener(
                                  onPointerDown: (e) {
                                    _focus.requestFocus();
                                    _pointer(e);
                                  },
                                  onPointerMove: _pointer,
                                  onPointerHover: _pointer,
                                  onPointerUp: _pointer,
                                  onPointerSignal: _scroll,
                                  child: SizedBox(
                                    width: img.width.toDouble(),
                                    height: img.height.toDouble(),
                                    child: RawImage(image: img, filterQuality: FilterQuality.medium),
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                ),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

/// Incus snapshots of one instance: take, restore, delete and the schedule.
class _SnapshotsDialog extends StatefulWidget {
  const _SnapshotsDialog({required this.c, required this.area, required this.name});

  final Controller c;
  final AreaCalls area;
  final String name;

  @override
  State<_SnapshotsDialog> createState() => _SnapshotsDialogState();
}

class _SnapshotsDialogState extends State<_SnapshotsDialog> {
  List<VmSnapshot>? snaps;
  String schedule = 'off';
  bool busy = false;
  final _name = TextEditingController();

  Controller get c => widget.c;
  String get base => '/api/vms/${Uri.encodeComponent(widget.name)}/snapshots';

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final r = await widget.area.call(() => widget.area.s.get(base), ScaffoldMessenger.of(context));
    if (!mounted || r is! Map) return;
    setState(() {
      snaps = parseSnapshots(r);
      schedule = snapshotSchedules.contains(r['schedule']) ? '${r['schedule']}' : 'off';
    });
  }

  Future<void> _do(Future<Object?> Function() f, [String? done]) async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await widget.area.call(f, m);
    if (r != null && done != null) widget.area.say(m, done);
    if (mounted) setState(() => busy = false);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final list = snaps;
    final s = widget.area.s;
    final name = widget.name;
    final nameOk = snapshotNameRe.hasMatch(_name.text.trim());
    return AlertDialog(
      title: Text(c.t('snap.title', {'name': name})),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(c.t('snap.hint'), style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
            const SizedBox(height: 14),
            DropdownButtonFormField<String>(
              initialValue: schedule,
              decoration: InputDecoration(labelText: c.t('snap.schedule'), isDense: true),
              borderRadius: BorderRadius.circular(12),
              items: [for (final e in snapshotSchedules) DropdownMenuItem(value: e, child: Text(c.t('snap.every_$e')))],
              onChanged: busy
                  ? null
                  : (v) => _do(() => s.post('/api/vms/${Uri.encodeComponent(name)}/snapshot-schedule', {'every': v ?? 'off'}),
                      c.t('snap.schedule_saved')),
            ),
            const SizedBox(height: 14),
            Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
              Expanded(
                child: TextField(
                  controller: _name,
                  decoration: InputDecoration(
                    labelText: c.t('snap.name'),
                    hintText: c.t('snap.name_placeholder'),
                    isDense: true,
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ),
              const SizedBox(width: 10),
              FilledButton(
                onPressed: busy || !nameOk
                    ? null
                    : () async {
                        final m = ScaffoldMessenger.of(context);
                        setState(() => busy = true);
                        final r = await widget.area.call(() => s.post(base, {'snapshot': _name.text.trim()}), m);
                        if (r is Map) {
                          widget.area.say(m, c.t('snap.taken', {'snap': r['snapshot']}));
                          _name.clear();
                        }
                        if (mounted) setState(() => busy = false);
                        await _load();
                      },
                child: Text(c.t('snap.take')),
              ),
            ]),
            const SizedBox(height: 14),
            if (list == null) Busy(c.t('common.loading')),
            if (list != null && list.isEmpty) Text(c.t('snap.none'), style: TextStyle(color: p.muted)),
            for (final sn in list ?? const <VmSnapshot>[])
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(children: [
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(sn.name, style: const TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontWeight: FontWeight.w600)),
                      if (sn.created != null)
                        Text(sn.created!.toLocal().toString().substring(0, 16), style: TextStyle(color: p.muted, fontSize: 12.5)),
                    ]),
                  ),
                  TextButton(
                    onPressed: busy
                        ? null
                        : () async {
                            if (!await widget.area.ask(c.t('snap.restore_confirm', {'name': name, 'snap': sn.name}), 'snap.restore',
                                danger: true)) {
                              return;
                            }
                            await _do(() => s.post('$base/${Uri.encodeComponent(sn.name)}/restore'),
                                c.t('snap.restored', {'name': name, 'snap': sn.name}));
                          },
                    child: Text(c.t('snap.restore')),
                  ),
                  TextButton(
                    style: TextButton.styleFrom(foregroundColor: p.danger),
                    onPressed: busy
                        ? null
                        : () async {
                            if (!await widget.area.ask(c.t('snap.delete_confirm', {'snap': sn.name}), 'snap.delete', danger: true)) {
                              return;
                            }
                            await _do(() => s.post('$base/${Uri.encodeComponent(sn.name)}/delete'));
                          },
                    child: Text(c.t('snap.delete')),
                  ),
                ]),
              ),
          ]),
        ),
      ),
      actions: [TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.close')))],
    );
  }
}

