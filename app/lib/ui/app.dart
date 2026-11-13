// Root widget: theme, backdrop and the page for the controller's stage.

import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import 'bloom.dart';
import 'apps.dart';
import 'home.dart';
import 'pages.dart';
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
          theme: dahliaTheme(Brightness.light),
          darkTheme: dahliaTheme(Brightness.dark),
          locale: Locale(c.settings.lang),
          home: Scaffold(
            backgroundColor: Colors.transparent,
            body: Backdrop(
              home: c.stage == Stage.home && c.area == Area.home,
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
        },
      };
}
