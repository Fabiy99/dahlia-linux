import 'dart:convert';

import 'package:dahlia/src/server.dart';
import 'package:dahlia/src/terminal.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('resize and errors as text frames', () {
    expect(jsonDecode(resizeMessage(120, 32)), {'cols': 120, 'rows': 32});
    expect(terminalError('{"error":"terminal.unavailable"}'), 'terminal.unavailable');
    expect(terminalError('{"x":1}'), isNull);
    expect(terminalError('not json'), isNull);
  });

  test('a character split across messages comes out whole', () {
    final d = OutputDecoder();
    final bytes = utf8.encode('Grüße ✿');
    expect(d.add(bytes.sublist(0, 3)), 'Gr');
    expect(d.add(bytes.sublist(3, 9)), 'üße ');
    expect(d.add(bytes.sublist(9)), '✿');
  });

  test('terminal address with the starting size', () {
    expect(terminalUrl('dahlia.local', cols: 80, rows: 24), 'wss://dahlia.local/api/terminal?cols=80&rows=24');
  });
}
