// UI texts. The app's own texts come from assets/locales/<lang>.json; texts
// of the server (error keys, area names) from the paired server's dashboard
// locales, so app and dashboard use the same words.

import 'dart:convert';

class Strings {
  Strings(this.lang, this._own, [Map<String, String>? server]) : _server = server ?? const {};

  final String lang;
  final Map<String, String> _own;
  final Map<String, String> _server;

  static Map<String, String> parse(String text) {
    final j = jsonDecode(text);
    if (j is! Map) return const {};
    return {
      for (final e in j.entries)
        if (e.key is String && e.value is String) e.key as String: e.value as String,
    };
  }

  Strings withServer(Map<String, String> server) => Strings(lang, _own, server);

  /// Text for [key] with {name} placeholders filled in; the key itself if unknown.
  String t(String key, [Map<String, Object?> vars = const {}]) {
    var s = _own[key] ?? _server[key] ?? key;
    vars.forEach((k, v) => s = s.replaceAll('{$k}', '$v'));
    return s;
  }

  bool has(String key) => _own.containsKey(key) || _server.containsKey(key);
}
