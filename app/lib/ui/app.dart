// Root widget: theme, backdrop and the page for the controller's stage.

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import 'bloom.dart';
import 'apps.dart';
import 'backup.dart';
import 'docker.dart';
import 'files.dart';
import 'home.dart';
import 'jobs.dart';
import 'pages.dart';
import 'parity.dart';
import 'settings.dart';
import 'shares.dart';
import 'storage.dart';
import 'terminal.dart';
import 'theme.dart';

const title = 'Dahlia, I love you ♥';

class DahliaApp extends StatelessWidget {
  const DahliaApp(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) => MaterialApp(
          title: title,
          debugShowCheckedModeBanner: false,
          theme: dahliaTheme(Brightness.light, c.settings.look.accent),
          darkTheme: dahliaTheme(Brightness.dark, c.settings.look.accent),
          themeMode: switch (c.settings.look.theme) {
            'light' => ThemeMode.light,
            'dark' => ThemeMode.dark,
            _ => ThemeMode.system,
          },
          locale: Locale(c.settings.lang),
          home: Scaffold(
            backgroundColor: Colors.transparent,
            body: Backdrop(
              home: c.stage == Stage.home && c.area == Area.home,
              look: c.settings.look,
              mood: c.stage == Stage.home ? c.mood : null,
              child: AnimatedSwitcher(
                duration: const Duration(milliseconds: 350),
                child: KeyedSubtree(key: ValueKey((c.stage, c.area)), child: SizedBox.expand(child: _page())),
              ),
            ),
          ),
        ),
      );

  Widget _page() => switch (c.stage) {
        Stage.starting || Stage.connecting => ConnectingPage(c),
        Stage.connect => ConnectPage(c),
        Stage.fingerprint => FingerprintPage(c),
        Stage.login => LoginPage(c),
        Stage.update => UpdatePage(c),
        Stage.home => switch (c.area) {
          Area.home => HomePage(c),
          Area.apps => AppsPage(c),
          Area.storage => StoragePage(c),
          Area.files => FilesPage(c),
          Area.shares => SharesPage(c),
          Area.parity => ParityPage(c),
          Area.backup => BackupPage(c),
          Area.docker => DockerPage(c),
          Area.jobs => JobsPage(c),
          Area.log => LogPage(c),
          Area.settings => SettingsPage(c),
          Area.terminal => TerminalPage(c),
        },
      };
}
