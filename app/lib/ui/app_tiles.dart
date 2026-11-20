// Start screen tiles with content from apps (tiles.py on the server):
// photos from Immich, new in Jellyfin, running downloads. Same states and
// texts as renderAppTile() in the dashboard's app.js.

import 'dart:async';

import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';
import 'package:url_launcher/url_launcher.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/server.dart';
import 'shell.dart';
import 'theme.dart';

final _images = <String, Future<Uint8List?>>{};

/// A picture served by the dashboard (/api/tiles/image/...), cached per path.
class ServerImage extends StatelessWidget {
  const ServerImage(this.c, this.path, {super.key, this.fit = BoxFit.cover});

  final Controller c;
  final String path;
  final BoxFit fit;

  @override
  Widget build(BuildContext context) {
    final server = c.server;
    final blank = ColoredBox(color: context.palette.sub);
    if (server == null) return blank;
    final future = _images.putIfAbsent('${server.host}$path', () => server.bytes(path).catchError((_) => null));
    return FutureBuilder<Uint8List?>(
      future: future,
      builder: (context, snap) => snap.data == null
          ? blank
          : Image.memory(snap.data!, fit: fit, gaplessPlayback: true, errorBuilder: (_, _, _) => blank),
    );
  }
}

class AppContentTile extends StatefulWidget {
  const AppContentTile(this.c, this.tile, {super.key, required this.apps});

  final Controller c;

  /// photos, media or downloads.
  final String tile;

  /// Installed apps, for the link into the app.
  final List<Map> apps;

  @override
  State<AppContentTile> createState() => _AppContentTileState();
}

class _AppContentTileState extends State<AppContentTile> {
  Map? data;
  Object? failed;
  bool editing = false;
  Timer? _timer;
  final _fields = <String, TextEditingController>{};

  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load();
    if (widget.tile == 'downloads') _timer = Timer.periodic(const Duration(seconds: 10), (_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    for (final f in _fields.values) {
      f.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await c.server!.get('/api/tiles/${widget.tile}/data');
      if (mounted && r is Map) {
        setState(() {
          data = r;
          failed = null;
        });
      }
    } on ApiError catch (e) {
      if (e.status == 401) return;
      if (mounted) setState(() => failed = e.key);
    } on VersionMismatch {
      return;
    }
  }

  Future<void> _save(String app, List<String> names) async {
    final m = ScaffoldMessenger.of(context);
    final danger = context.palette.danger;
    try {
      await c.server!.post('/api/tiles/$app', {
        'settings': {for (final n in names) n: _fields[n]!.text},
      });
      m.showSnackBar(SnackBar(content: Text(c.t('tiles.saved'))));
      if (mounted) setState(() => editing = false);
      await _load();
    } on ApiError catch (e) {
      m.showSnackBar(SnackBar(content: Text(c.t(e.key)), backgroundColor: danger));
    } on VersionMismatch {
      return;
    }
  }

  Uri? _link(String? app) {
    final a = widget.apps.where((x) => x['id'] == app).firstOrNull;
    return a == null ? null : appUrl(c, a);
  }

  Widget _muted(String text) => Text(text, style: TextStyle(color: context.palette.muted, fontSize: 13.5, height: 1.4));

  Widget _form(String app) {
    final names = app == 'qbittorrent' ? ['user', 'password'] : ['key'];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: 10),
        _muted(c.t('tiles.hint_$app')),
        for (final n in names)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: TextField(
              controller: _fields.putIfAbsent(n, TextEditingController.new),
              obscureText: n != 'user',
              autocorrect: false,
              inputFormatters: [LengthLimitingTextInputFormatter(256)],
              decoration: InputDecoration(labelText: c.t('tiles.field_$n'), isDense: true),
            ),
          ),
        const SizedBox(height: 12),
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton(onPressed: () => _save(app, names), child: Text(c.t('common.save'))),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final d = data;
    if (d == null) return _muted(failed == null ? c.t('common.loading') : c.t('$failed'));
    final tile = widget.tile;
    final app = '${d['app'] ?? (d['apps'] is List && (d['apps'] as List).isNotEmpty ? d['apps'][0] : '')}';
    final admin = c.isAdmin;
    switch (d['state']) {
      case 'missing':
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _muted(c.t('tiles.missing_$tile')),
            TextButton(onPressed: () => c.go(Area.apps), child: Text(c.t('tiles.to_apps'))),
          ],
        );
      case 'setup':
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _muted(c.t('tiles.setup', {'app': c.t('tiles.app.$app')})),
            if (admin) _form(app),
          ],
        );
      case 'error':
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _muted(c.t('tiles.error', {'app': c.t('tiles.app.$app'), 'error': d['error']})),
            if (admin && app != 'transmission')
              editing
                  ? _form(app)
                  : Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton(
                        onPressed: () => setState(() => editing = true),
                        child: Text(c.t('tiles.change')),
                      ),
                    ),
          ],
        );
    }
    final link = _link(app);
    if (tile == 'photos') {
      final id = d['id'];
      if (id is! String) return _muted(c.t('tiles.no_photos'));
      final years = d['years'];
      final caption = years is int
          ? c.t(years == 1 ? 'tiles.photo_year' : 'tiles.photo_years', {'n': years})
          : c.t('tiles.photo_random');
      return MouseRegion(
        cursor: link == null ? MouseCursor.defer : SystemMouseCursors.click,
        child: GestureDetector(
          onTap: link == null ? null : () => launchUrl(link),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(14),
            child: AspectRatio(
              aspectRatio: 4 / 3,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  ServerImage(c, '/api/tiles/image/immich/$id'),
                  Positioned(
                    left: 10,
                    bottom: 9,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(999)),
                      child: Text(caption, style: const TextStyle(color: Colors.white, fontSize: 13)),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }
    if (tile == 'media') {
      final items = d['items'] is List ? (d['items'] as List).whereType<Map>().toList() : <Map>[];
      if (items.isEmpty) return _muted(c.t('tiles.no_media'));
      return LayoutBuilder(
        builder: (context, box) {
          final cols = ((box.maxWidth + 11) / (96 + 11)).floor().clamp(1, 6);
          final cell = (box.maxWidth - 11 * (cols - 1)) / cols;
          return Wrap(
            spacing: 11,
            runSpacing: 11,
            children: [
              for (final i in items)
                SizedBox(
                  width: cell,
                  child: InkWell(
                    borderRadius: BorderRadius.circular(10),
                    onTap: link == null ? null : () => launchUrl(link),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        ClipRRect(
                          borderRadius: BorderRadius.circular(10),
                          child: AspectRatio(
                            aspectRatio: 2 / 3,
                            child: i['image'] == true
                                ? ServerImage(c, '/api/tiles/image/jellyfin/${i['id']}')
                                : ColoredBox(
                                    color: p.sub,
                                    child: Center(
                                      child: Text(
                                        '${i['name']}'.characters.take(1).toString(),
                                        style: TextStyle(fontSize: 24, color: p.muted),
                                      ),
                                    ),
                                  ),
                          ),
                        ),
                        const SizedBox(height: 5),
                        Text(
                          i['year'] == null ? '${i['name']}' : '${i['name']} (${i['year']})',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 12.5, height: 1.3),
                        ),
                      ],
                    ),
                  ),
                ),
            ],
          );
        },
      );
    }
    final items = d['items'] is List ? (d['items'] as List).whereType<Map>().toList() : <Map>[];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Text('↓ ${rate((d['down'] as num?) ?? 0, c.t)}', style: const TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(width: 18),
            Text('↑ ${rate((d['up'] as num?) ?? 0, c.t)}', style: const TextStyle(fontWeight: FontWeight.w600)),
          ],
        ),
        const SizedBox(height: 10),
        if (items.isEmpty) _muted(c.t('tiles.no_downloads')),
        for (final i in items)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  '${i['name']}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 13.5),
                ),
                const SizedBox(height: 2),
                Text(
                  [
                    '${(((i['progress'] as num?) ?? 0) * 100).round()} %',
                    if ((i['down'] as num? ?? 0) > 0) '↓ ${rate(i['down'] as num, c.t)}',
                    if (i['eta'] is int) c.t('tiles.eta', {'time': duration(i['eta'] as int, c.t)}),
                  ].join(' · '),
                  style: TextStyle(color: p.muted, fontSize: 12),
                ),
                const SizedBox(height: 4),
                ClipRRect(
                  borderRadius: BorderRadius.circular(3),
                  child: LinearProgressIndicator(
                    value: ((i['progress'] as num?) ?? 0).toDouble(),
                    minHeight: 6,
                    backgroundColor: p.sub,
                    color: p.accent,
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
