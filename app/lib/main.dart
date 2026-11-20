// Dahlia desktop app: native client for a Dahlia server, always at the
// server's version (lib/src/server.dart, lib/src/self_update.dart).

import 'dart:io';

import 'package:flutter/services.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:material_ui/material_ui.dart';
import 'package:path_provider/path_provider.dart';

import 'src/controller.dart';
import 'src/desktop_notify.dart';
import 'ui/app.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await initializeDateFormatting();
  final dir = await getApplicationSupportDirectory();
  final c = Controller(
    settingsFile: File('${dir.path}${Platform.pathSeparator}settings.json'),
    loadAsset: rootBundle.loadString,
    popup: DesktopNotifier().show,
  );
  runApp(DahliaApp(c));
  await c.start();
}
