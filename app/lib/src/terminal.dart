// Terminal without widgets: what goes over the socket besides the raw bytes,
// as the dashboard's terminal page does it.

import 'dart:convert';

/// New size of the terminal; the dashboard passes text frames on as resize.
String resizeMessage(int cols, int rows) => jsonEncode({'cols': cols, 'rows': rows});

/// Locale key of an error the dashboard sends as text ("terminal.unavailable"), else null.
String? terminalError(String text) {
  try {
    final m = jsonDecode(text);
    return m is Map && m['error'] is String ? m['error'] as String : null;
  } on FormatException {
    return null;
  }
}

/// Turns the output bytes into text; a character split across two
/// messages comes out whole once its second half arrives.
class OutputDecoder {
  final _out = StringBuffer();
  late final ByteConversionSink _sink = const Utf8Decoder(allowMalformed: true).startChunkedConversion(StringConversionSink.fromStringSink(_out));

  String add(List<int> bytes) {
    _sink.add(bytes);
    final s = _out.toString();
    _out.clear();
    return s;
  }
}
