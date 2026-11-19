// Frame of the signed-in pages: top bar with the areas and the account menu,
// centered content, and the app icons shared by start page and Apps.

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_svg/flutter_svg.dart';
import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/format.dart';
import 'bloom.dart';
import 'notices.dart';
import 'services.dart';
import 'theme.dart';
import 'tour.dart';
import 'widgets.dart';

/// Areas in the bar; the rest sit in the "More" menu.
const _areas = [
  (Area.home, 'nav.overview'),
  (Area.apps, 'nav.apps'),
  (Area.storage, 'nav.storage'),
  (Area.files, 'nav.files'),
];

const _more = [
  (Area.shares, 'nav.shares'),
  (Area.parity, 'nav.parity'),
  (Area.backup, 'nav.backup'),
  (Area.docker, 'nav.docker'),
  (Area.terminal, 'nav.terminal'),
  (Area.jobs, 'jobs.title'),
  (Area.log, 'nav.log'),
  (Area.settings, 'nav.account'),
];

class TopBar extends StatelessWidget {
  const TopBar(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final narrow = MediaQuery.of(context).size.width < 640;
    Widget tab(String label, bool on, VoidCallback? onTap, {bool menu = false, int count = 0}) => Container(
          height: 44,
          padding: EdgeInsets.symmetric(horizontal: narrow ? 12 : 18),
          decoration: BoxDecoration(color: on ? p.navActive : Colors.transparent, borderRadius: BorderRadius.circular(12)),
          alignment: Alignment.center,
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Text(label, style: TextStyle(color: on ? p.navActiveFg : p.fg, fontWeight: FontWeight.w500, fontSize: 14)),
            if (count > 0)
              Container(
                margin: const EdgeInsets.only(left: 7),
                constraints: const BoxConstraints(minWidth: 22),
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(color: p.accent, borderRadius: BorderRadius.circular(999)),
                child: Text('$count', textAlign: TextAlign.center, style: TextStyle(color: p.accentFg, fontSize: 11.25)),
              ),
            if (menu) Icon(Icons.expand_more_rounded, size: 18, color: on ? p.navActiveFg : p.muted),
          ]),
        );
    final inMore = _more.where((e) => e.$1 == c.area).firstOrNull;
    return Glass(
      radius: 18,
      padding: const EdgeInsets.fromLTRB(18, 10, 12, 10),
      child: Row(children: [
        const DahliaMark(size: 28),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            const Text('Dahlia', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
            if (!narrow)
              Text(c.settings.host ?? '', overflow: TextOverflow.ellipsis, style: TextStyle(color: p.muted, fontSize: 12)),
          ]),
        ),
        for (final (view, key) in _areas)
          Padding(
            padding: const EdgeInsets.only(left: 4),
            child: Material(
              color: Colors.transparent,
              child: InkWell(key: tourKey(c, view.name), borderRadius: BorderRadius.circular(12), onTap: () => c.go(view), child: tab(c.t(key), c.area == view, null, count: view == Area.apps ? c.appUpdates : 0)),
            ),
          ),
        // The chosen area of the menu takes its place in the bar.
        Padding(
          padding: const EdgeInsets.only(left: 4),
          child: PopupMenuButton<Area>(
            key: tourKey(c, 'more'),
            tooltip: '',
            position: PopupMenuPosition.under,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
            onSelected: c.go,
            itemBuilder: (context) => [
              for (final (view, key) in _more)
                PopupMenuItem(
                  value: view,
                  child: Text(c.t(key), style: TextStyle(color: c.area == view ? p.navActiveFg : null, fontWeight: c.area == view ? FontWeight.w600 : null)),
                ),
            ],
            child: tab(c.t(inMore?.$2 ?? 'nav.more'), inMore != null, null, menu: true),
          ),
        ),
        const SizedBox(width: 12),
        NoticeBell(c, key: tourKey(c, 'bell')),
        const SizedBox(width: 4),
        // Phone and pairing of other desktop apps, like the dashboard's "Phone" button.
        Tooltip(
          key: tourKey(c, 'phone'),
          message: c.t('phone.open'),
          child: Material(
            color: Colors.transparent,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: BorderSide(color: p.accent.withValues(alpha: .35)),
            ),
            child: InkWell(
              borderRadius: BorderRadius.circular(12),
              onTap: () => showDialog<void>(
                context: context,
                builder: (context) => Dialog(
                  backgroundColor: Colors.transparent,
                  child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 820), child: SingleChildScrollView(child: PhoneCard(c))),
                ),
              ),
              child: Container(
                height: 44,
                padding: EdgeInsets.symmetric(horizontal: narrow ? 12 : 14),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(Icons.smartphone_rounded, size: 17, color: p.navActiveFg),
                  if (!narrow) ...[
                    const SizedBox(width: 7),
                    Text(c.t('nav.phone'), style: TextStyle(color: p.navActiveFg, fontWeight: FontWeight.w500, fontSize: 14)),
                  ],
                ]),
              ),
            ),
          ),
        ),
        const SizedBox(width: 4),
        PopupMenuButton<String>(
          tooltip: '',
          position: PopupMenuPosition.under,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          icon: Icon(Icons.account_circle_rounded, color: p.navActiveFg),
          onSelected: (v) {
            switch (v) {
              case 'en' || 'de':
                c.setLanguage(v);
              case 'tour':
                c.restartTour();
              case 'server':
                c.otherServer();
              case 'logout':
                c.logout();
            }
          },
          itemBuilder: (context) => [
            PopupMenuItem(enabled: false, child: Text(displayName(c.user ?? ''), style: const TextStyle(fontWeight: FontWeight.w600))),
            const PopupMenuDivider(),
            CheckedPopupMenuItem(value: 'en', checked: c.settings.lang == 'en', child: const Text('English')),
            CheckedPopupMenuItem(value: 'de', checked: c.settings.lang == 'de', child: const Text('Deutsch')),
            const PopupMenuDivider(),
            PopupMenuItem(value: 'tour', child: Text(c.t('tour.restart'))),
            PopupMenuItem(value: 'server', child: Text(c.t('menu.other_server'))),
            PopupMenuItem(value: 'logout', child: Text(c.t('menu.logout'), style: TextStyle(color: p.danger))),
          ],
        ),
      ]),
    );
  }
}

/// Side padding of the content, as in the dashboard (clamp(16px, 4vw, 48px)).
double pagePad(double width) => math.max(16.0, math.min(48.0, width * .04));

/// Width of the content: the dashboard's .content is at most 1240 px
/// including its side padding.
double pageContent(double width) => math.min(1240.0, width) - 2 * pagePad(width);

/// Column width of the page grid: three, two or one columns like the
/// dashboard's tiles, with 18 px between them.
({double unit, int cols}) pageGrid(double width) {
  final content = pageContent(width);
  final cols = content > 980 ? 3 : (content > 640 ? 2 : 1);
  return (unit: (content - 18 * (cols - 1)) / cols, cols: cols);
}

/// Scrolling page with the top bar, content at most 1240 px wide.
class PageFrame extends StatelessWidget {
  const PageFrame({super.key, required this.c, required this.children});

  final Controller c;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.of(context).size.width;
    final pad = pagePad(width);
    return SingleChildScrollView(
      padding: EdgeInsets.fromLTRB(pad, 24, pad, 56),
      child: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: pageContent(width)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [TopBar(c), UpdateBanner(c), ...children]),
        ),
      ),
    );
  }
}

/// Notice below the top bar while the server has updates pending; it leads
/// to the Updates section of the settings and can be closed.
class UpdateBanner extends StatelessWidget {
  const UpdateBanner(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) {
    if (c.pendingUpdates == 0 || c.updateBannerClosed || c.area == Area.settings) return const SizedBox.shrink();
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: Container(
        padding: const EdgeInsets.fromLTRB(18, 8, 8, 8),
        decoration: BoxDecoration(
          color: p.accent.withValues(alpha: .10),
          border: Border.all(color: p.accent.withValues(alpha: .35)),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Row(children: [
          Icon(Icons.system_update_alt_rounded, color: p.accent, size: 20),
          const SizedBox(width: 12),
          Expanded(child: Text(c.t('updates.banner', {'n': c.pendingUpdates}), style: const TextStyle(fontWeight: FontWeight.w500))),
          TextButton(onPressed: () => c.go(Area.settings), child: Text(c.t('updates.banner_open'))),
          IconButton(
            tooltip: c.t('common.close'),
            onPressed: c.closeUpdateBanner,
            icon: Icon(Icons.close_rounded, size: 18, color: p.muted),
          ),
        ]),
      ),
    );
  }
}

/// Address of an app's web interface: its mDNS alias, else the port link.
Uri? appUrl(Controller c, Map app) {
  final port = app['public_port'];
  if (port == null) return null;
  final urls = app['urls'];
  final mdns = urls is Map ? urls['mdns'] : null;
  if (mdns is String) return Uri.tryParse(mdns);
  final host = c.settings.host!.replaceFirst(RegExp(r':\d+$'), '');
  return Uri.parse('http://$host:$port');
}

final _icons = <String, Future<Uint8List?>>{};

/// The app's logo from the server (/app-icons/, SVG or bitmap), else its first letter on a
/// color derived from the id, like the dashboard's avatar().
class AppAvatar extends StatelessWidget {
  const AppAvatar(this.c, this.app, {super.key, this.size = 44});

  final Controller c;
  final Map app;
  final double size;

  @override
  Widget build(BuildContext context) {
    final id = '${app['id'] ?? ''}';
    final name = '${app['name'] ?? id}';
    final hue = id.codeUnits.fold<int>(0, (h, ch) => (h * 31 + ch) % 360).toDouble();
    final letter = Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: HSLColor.fromAHSL(1, hue, .45, .45).toColor(),
        borderRadius: BorderRadius.circular(size * .27),
      ),
      child: Text(name.isEmpty ? '?' : name.substring(0, 1).toUpperCase(),
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: size * .4)),
    );
    final icon = app['icon'];
    final server = c.server;
    if (icon is! String || server == null) return letter;
    final svg = icon.split('?').first.toLowerCase().endsWith('.svg');
    final future = _icons.putIfAbsent('${server.host}/$icon', () => server.bytes('/app-icons/$icon'));
    return FutureBuilder<Uint8List?>(
      future: future,
      builder: (context, snap) {
        final data = snap.data;
        if (data == null) return letter;
        return ClipRRect(
          borderRadius: BorderRadius.circular(size * .22),
          child: SizedBox.square(
            dimension: size,
            child: svg
              ? SvgPicture.memory(data, width: size, height: size, fit: BoxFit.contain,
                  errorBuilder: (_, _, _) => letter)
              : Image.memory(data, width: size, height: size, fit: BoxFit.contain,
                  errorBuilder: (_, _, _) => letter),
          ),
        );
      },
    );
  }
}
