// Bell in the top bar: the server's last notifications, a dot for unread
// ones, and the switch for messages of the operating system.

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/notices.dart';
import 'theme.dart';

class NoticeBell extends StatelessWidget {
  const NoticeBell(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final unread = c.unreadNotices;
    return Tooltip(
      message: c.t('notices.title'),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () {
            c.readNotices();
            showDialog<void>(context: context, builder: (context) => NoticeDialog(c));
          },
          child: SizedBox(
            width: 44,
            height: 44,
            child: Stack(alignment: Alignment.center, children: [
              Icon(unread > 0 ? Icons.notifications_active_rounded : Icons.notifications_none_rounded,
                  size: 21, color: p.navActiveFg),
              if (unread > 0)
                Positioned(
                  top: 6,
                  right: 4,
                  child: Container(
                    constraints: const BoxConstraints(minWidth: 17),
                    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                    decoration: BoxDecoration(color: p.accent, borderRadius: BorderRadius.circular(999)),
                    child: Text(unread > 9 ? '9+' : '$unread',
                        textAlign: TextAlign.center, style: TextStyle(color: p.accentFg, fontSize: 10.5)),
                  ),
                ),
            ]),
          ),
        ),
      ),
    );
  }
}

class NoticeDialog extends StatelessWidget {
  const NoticeDialog(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final p = context.palette;
          final l = MaterialLocalizations.of(context);
          Color color(Notice n) => switch (n.level) { 'critical' => p.danger, 'warning' => p.warn, _ => p.accent };
          return AlertDialog(
            title: Text(c.t('notices.title')),
            content: SizedBox(
              width: 520,
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: c.settings.desktopNotices,
                  onChanged: c.setDesktopNotices,
                  title: Text(c.t('notices.desktop')),
                  subtitle: Text(c.t('notices.desktop_hint'), style: TextStyle(color: p.muted, fontSize: 12.5)),
                ),
                const SizedBox(height: 8),
                if (c.notices.isEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 18),
                    child: Text(c.t('notices.none'), style: TextStyle(color: p.muted)),
                  )
                else
                  Flexible(
                    child: ListView(shrinkWrap: true, children: [
                      for (final n in c.notices.take(30))
                        Container(
                          margin: const EdgeInsets.only(bottom: 8),
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: p.sub,
                            borderRadius: BorderRadius.circular(12),
                            border: Border(left: BorderSide(color: color(n), width: 3)),
                          ),
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Row(children: [
                              Expanded(child: Text(n.title, style: const TextStyle(fontWeight: FontWeight.w600))),
                              Text(
                                  '${l.formatShortDate(n.when)} ${l.formatTimeOfDay(TimeOfDay.fromDateTime(n.when), alwaysUse24HourFormat: true)}',
                                  style: TextStyle(color: p.muted, fontSize: 12)),
                            ]),
                            if (n.message.isNotEmpty) ...[
                              const SizedBox(height: 4),
                              SelectableText(n.message,
                                  maxLines: 6, style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
                            ],
                          ]),
                        ),
                    ]),
                  ),
              ]),
            ),
            actions: [TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.close')))],
          );
        },
      );
}
