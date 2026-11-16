// Messages of the operating system for server notifications: Windows toast,
// macOS Notification Center, freedesktop notifications on Linux.

import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import 'notices.dart';

class DesktopNotifier {
  final _plugin = FlutterLocalNotificationsPlugin();
  bool? _ready;

  Future<bool> _init() async {
    final ready = _ready;
    if (ready != null) return ready;
    try {
      _ready = await _plugin.initialize(
            settings: const InitializationSettings(
              macOS: DarwinInitializationSettings(),
              linux: LinuxInitializationSettings(defaultActionName: 'Open'),
              windows: WindowsInitializationSettings(
                appName: 'Dahlia',
                appUserModelId: 'DahliaLinux.Dahlia',
                // Fixed id of the app's notification activator.
                guid: 'b7f4c2a0-5d1e-4c8b-9a36-2f1d7e0c4a91',
              ),
            ),
          ) ??
          false;
    } on Object {
      // No notification service (e.g. a Linux desktop without one): bell list only.
      _ready = false;
    }
    return _ready!;
  }

  Future<void> show(Notice n) async {
    if (!await _init()) return;
    try {
      await _plugin.show(id: n.time & 0x7fffffff, title: n.title, body: n.message);
    } on Object {
      _ready = false;
    }
  }
}
