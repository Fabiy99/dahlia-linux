// Terminal area: a shell on the server as the Linux account with the
// dashboard account's name; su asks for its password, as over SSH. Same
// socket and rules as the dashboard's terminal page. The session stays open
// while other areas are shown and ends on sign-out.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'package:material_ui/material_ui.dart';
import 'package:xterm/xterm.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/server.dart';
import '../src/terminal.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

const _theme = TerminalTheme(
  cursor: Color(0xFFD873B8),
  selection: Color(0x66D873B8),
  foreground: Color(0xFFEBE8F0),
  background: Color(0xFF14121A),
  black: Color(0xFF14121A),
  red: Color(0xFFE5677A),
  green: Color(0xFF7FC99A),
  yellow: Color(0xFFE7C17A),
  blue: Color(0xFF7AA6E7),
  magenta: Color(0xFFD873B8),
  cyan: Color(0xFF6CC7C9),
  white: Color(0xFFD6D2DE),
  brightBlack: Color(0xFF6F6880),
  brightRed: Color(0xFFF08A9A),
  brightGreen: Color(0xFF9BDDB2),
  brightYellow: Color(0xFFF2D59B),
  brightBlue: Color(0xFF9DBDF0),
  brightMagenta: Color(0xFFE79BCD),
  brightCyan: Color(0xFF92DADB),
  brightWhite: Color(0xFFFFFFFF),
  searchHitBackground: Color(0xFFE7C17A),
  searchHitBackgroundCurrent: Color(0xFFF2D59B),
  searchHitForeground: Color(0xFF14121A),
);

/// One shell session per signed-in controller, kept across area changes.
class _Session extends ChangeNotifier {
  _Session(this.c) {
    terminal = Terminal(maxLines: 5000, onOutput: _input, onResize: _resize);
    c.addListener(_stageChanged);
  }

  final Controller c;
  late final Terminal terminal;
  WebSocket? _socket;
  StreamSubscription? _sub;
  bool connecting = false;

  bool get connected => _socket != null;

  void _input(String data) => _socket?.add(utf8.encode(data));

  void _resize(int cols, int rows, int pw, int ph) => _socket?.add(resizeMessage(cols, rows));

  /// Signing out or a server change ends the shell.
  void _stageChanged() {
    if (c.stage != Stage.home && (connected || connecting)) disconnect();
  }

  Future<void> connect() async {
    final s = c.server;
    if (s == null || connecting) return;
    await disconnect(quiet: true);
    connecting = true;
    // A fresh screen for every connection, as in the dashboard.
    terminal.write('\x1bc${c.t('terminal.ready')}\r\n');
    notifyListeners();
    try {
      final cols = terminal.viewWidth > 1 ? terminal.viewWidth : 80;
      final rows = terminal.viewHeight > 1 ? terminal.viewHeight : 24;
      final ws = await s.terminal(cols: cols, rows: rows);
      final decoder = OutputDecoder();
      _socket = ws;
      _sub = ws.listen(
        (msg) {
          if (msg is String) {
            final key = terminalError(msg);
            if (key != null) terminal.write('\r\n${c.t(key)}\r\n');
          } else if (msg is List<int>) {
            terminal.write(decoder.add(msg));
          }
        },
        onDone: () {
          if (_socket != ws) return;
          _closed();
        },
        onError: (_) {
          if (_socket == ws) _closed();
        },
        cancelOnError: true,
      );
    } on ApiError catch (e) {
      terminal.write('\r\n${c.t(e.key)}\r\n');
    } on WebSocketException {
      // The dashboard refuses with 4401 before the upgrade when the session or role is wrong.
      terminal.write('\r\n${c.t('error.login_required')}\r\n');
    }
    connecting = false;
    notifyListeners();
  }

  void _closed() {
    _socket = null;
    _sub = null;
    terminal.write('\r\n\x1b[2m${c.t('terminal.closed')}\x1b[0m\r\n');
    notifyListeners();
  }

  Future<void> disconnect({bool quiet = false}) async {
    final ws = _socket;
    _socket = null;
    await _sub?.cancel();
    _sub = null;
    await ws?.close();
    if (ws != null && !quiet) _closed();
    notifyListeners();
  }
}

/// Copy and paste. On Linux and Windows with Shift, so that Ctrl+A, Ctrl+V
/// and friends keep their meaning in the shell; macOS uses Cmd.
Map<ShortcutActivator, Intent> get _shortcuts => defaultTargetPlatform == TargetPlatform.macOS
    ? defaultTerminalShortcuts
    : {
        const SingleActivator(LogicalKeyboardKey.keyC, control: true, shift: true): CopySelectionTextIntent.copy,
        const SingleActivator(LogicalKeyboardKey.keyV, control: true, shift: true): const PasteTextIntent(SelectionChangedCause.keyboard),
      };

final _sessions = Expando<_Session>();

class TerminalPage extends StatefulWidget {
  const TerminalPage(this.c, {super.key});

  final Controller c;

  @override
  State<TerminalPage> createState() => _TerminalPageState();
}

class _TerminalPageState extends State<TerminalPage> {
  late final _Session session = _sessions[widget.c] ??= _Session(widget.c);
  final _controller = TerminalController();
  final _focus = FocusNode();

  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    session.addListener(_changed);
    // Opening the page connects, as in the dashboard; the view first needs its size.
    if (!session.connected && !session.connecting) {
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        await session.connect();
        if (mounted) _focus.requestFocus();
      });
    }
  }

  @override
  void dispose() {
    session.removeListener(_changed);
    _controller.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _changed() {
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    // The whole box fits in the window below the title, so full-screen programs stay visible.
    final height = (MediaQuery.of(context).size.height - 470).clamp(300.0, 2000.0);
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('nav.terminal'), hint: c.t('terminal.hint'))),
      const SizedBox(height: 24),
      Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Wrap(spacing: 10, runSpacing: 10, crossAxisAlignment: WrapCrossAlignment.center, children: [
            Text(c.t('terminal.account', {'name': displayName(c.user ?? '')}), style: const TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(width: 6),
            if (session.connected)
              OutlinedButton(onPressed: () => session.disconnect(), child: Text(c.t('terminal.disconnect')))
            else
              FilledButton(onPressed: session.connecting ? null : () => session.connect(), child: Text(c.t('terminal.connect'))),
            if (session.connecting) const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
          ]),
          const SizedBox(height: 14),
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Container(
              height: height,
              color: _theme.background,
              padding: const EdgeInsets.all(10),
              child: TerminalView(
                session.terminal,
                controller: _controller,
                focusNode: _focus,
                autofocus: true,
                theme: _theme,
                textStyle: const TerminalStyle(fontSize: 14, fontFamily: monoFont, fontFamilyFallback: monoFallback),
                hardwareKeyboardOnly: true,
                shortcuts: _shortcuts,
              ),
            ),
          ),
          const SizedBox(height: 8),
          Text(c.t(defaultTargetPlatform == TargetPlatform.macOS ? 'terminal.copy_hint_mac' : 'terminal.copy_hint'), style: TextStyle(color: p.muted, fontSize: 12.5)),
        ]),
      ),
    ]);
  }
}
