// Pages before the home page: connect and pair, fingerprint or code,
// connecting, login, and the version update.

import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';
import 'package:url_launcher/url_launcher.dart';

import '../src/controller.dart';
import '../src/discover.dart';
import '../src/manifest.dart';
import '../src/self_update.dart';
import 'theme.dart';
import 'widgets.dart';

class ConnectPage extends StatefulWidget {
  const ConnectPage(this.c, {super.key});

  final Controller c;

  @override
  State<ConnectPage> createState() => _ConnectPageState();
}

class _ConnectPageState extends State<ConnectPage> {
  final _address = TextEditingController();
  List<Found>? _found;

  @override
  void initState() {
    super.initState();
    _address.text = widget.c.settings.host ?? '';
    _search();
  }

  Future<void> _search() async {
    setState(() => _found = null);
    final f = await browse();
    if (mounted) setState(() => _found = f);
  }

  @override
  void dispose() {
    _address.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final p = context.palette;
    final found = _found;
    return Panel(c: c, title: c.t('connect.title'), children: [
      Text(c.t('connect.intro'), style: TextStyle(color: p.muted, height: 1.45)),
      const SizedBox(height: 22),
      Row(children: [
        Text(c.t('connect.found'), style: const TextStyle(fontWeight: FontWeight.w600)),
        const Spacer(),
        TextButton(onPressed: found == null ? null : _search, child: Text(c.t('connect.search'))),
      ]),
      const SizedBox(height: 6),
      if (found == null) Busy(c.t('connect.searching')),
      if (found != null && found.isEmpty) Text(c.t('connect.none_found'), style: TextStyle(color: p.muted)),
      if (found != null)
        for (final f in found)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Material(
              color: p.sub,
              borderRadius: BorderRadius.circular(14),
              child: ListTile(
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                leading: Icon(Icons.dns_rounded, color: p.accent),
                title: Text(f.name, style: const TextStyle(fontWeight: FontWeight.w600)),
                subtitle: Text(f.host, style: TextStyle(color: p.muted)),
                trailing: const Icon(Icons.chevron_right_rounded),
                onTap: c.busy ? null : () => c.choose(f.host),
              ),
            ),
          ),
      const SizedBox(height: 18),
      TextField(
        controller: _address,
        decoration: InputDecoration(labelText: c.t('connect.address')),
        onSubmitted: c.busy ? null : c.choose,
      ),
      const SizedBox(height: 14),
      Row(children: [
        FilledButton(
          onPressed: c.busy ? null : () => c.choose(_address.text),
          child: Text(c.t('connect.continue')),
        ),
        const SizedBox(width: 12),
        if (c.settings.paired)
          OutlinedButton(onPressed: c.busy ? null : c.connect, child: Text(c.t('connect.retry'))),
        if (c.busy) ...[const SizedBox(width: 14), Expanded(child: Busy(c.t('connect.contacting')))],
      ]),
      ErrorLine(c),
    ]);
  }
}

class FingerprintPage extends StatefulWidget {
  const FingerprintPage(this.c, {super.key});

  final Controller c;

  @override
  State<FingerprintPage> createState() => _FingerprintPageState();
}

class _FingerprintPageState extends State<FingerprintPage> {
  final _code = TextEditingController();

  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final p = context.palette;
    return Panel(c: c, title: c.t('pair.check_title'), width: 560, children: [
      Text(c.t('pair.check_intro'), style: TextStyle(color: p.muted)),
      const SizedBox(height: 12),
      Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(14)),
        child: SelectableText(
          '${c.pendingHost}\n${c.pendingFingerprint}',
          style: const TextStyle(fontFamily: 'monospace', fontSize: 13, height: 1.5),
        ),
      ),
      const SizedBox(height: 12),
      Text(c.t('pair.check_where'), style: TextStyle(color: p.muted, height: 1.45)),
      const SizedBox(height: 16),
      Wrap(spacing: 12, runSpacing: 10, children: [
        FilledButton(onPressed: c.busy ? null : c.confirmFingerprint, child: Text(c.t('pair.confirm'))),
        OutlinedButton(onPressed: c.busy ? null : c.back, child: Text(c.t('pair.back'))),
      ]),
      const SizedBox(height: 26),
      Text(c.t('pair.code_title'), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
      const SizedBox(height: 6),
      Text(c.t('pair.code_hint'), style: TextStyle(color: p.muted, height: 1.45)),
      const SizedBox(height: 12),
      Row(children: [
        Expanded(
          child: TextField(
            controller: _code,
            textCapitalization: TextCapitalization.characters,
            decoration: InputDecoration(labelText: c.t('pair.code'), hintText: 'ABCDE-FGHJK'),
            onSubmitted: c.busy ? null : c.pairWithCode,
          ),
        ),
        const SizedBox(width: 12),
        FilledButton(
          onPressed: c.busy ? null : () => c.pairWithCode(_code.text),
          child: Text(c.t('pair.code_pair')),
        ),
      ]),
      if (c.busy) ...[const SizedBox(height: 14), Busy(c.t('pair.checking_code'))],
      ErrorLine(c),
    ]);
  }
}

class ConnectingPage extends StatelessWidget {
  const ConnectingPage(this.c, {super.key});

  final Controller c;

  @override
  Widget build(BuildContext context) => Panel(
        c: c,
        title: c.t('connect.title'),
        children: [
          const SizedBox(height: 8),
          Busy(c.t('connect.connecting', {'host': c.settings.host ?? ''})),
        ],
      );
}

class LoginPage extends StatefulWidget {
  const LoginPage(this.c, {super.key});

  final Controller c;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  late final _name = TextEditingController(text: widget.c.settings.username ?? '');
  final _password = TextEditingController();
  final _code = TextEditingController();
  late bool _save = widget.c.settings.autoLogin;

  @override
  void dispose() {
    _name.dispose();
    _password.dispose();
    _code.dispose();
    super.dispose();
  }

  void _submit() {
    final c = widget.c;
    if (c.busy) return;
    c.login(_name.text, _password.text, code: _code.text.trim(), save: _save);
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final p = context.palette;
    return Panel(c: c, title: c.t('login.title'), width: 440, children: [
      Text(c.settings.host ?? '', style: TextStyle(color: p.muted)),
      const SizedBox(height: 18),
      AutofillGroup(
        child: Column(children: [
          TextField(
            controller: _name,
            autofocus: _name.text.isEmpty,
            autofillHints: const [AutofillHints.username],
            decoration: InputDecoration(labelText: c.t('login.username')),
            textInputAction: TextInputAction.next,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _password,
            autofocus: _name.text.isNotEmpty,
            obscureText: true,
            autofillHints: const [AutofillHints.password],
            decoration: InputDecoration(labelText: c.t('login.password')),
            onSubmitted: (_) => _submit(),
          ),
        ]),
      ),
      if (c.needsCode) ...[
        const SizedBox(height: 12),
        TextField(
          controller: _code,
          autofocus: true,
          keyboardType: TextInputType.number,
          inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9A-Za-z-]'))],
          decoration: InputDecoration(labelText: c.t('login.code')),
          onSubmitted: (_) => _submit(),
        ),
      ],
      const SizedBox(height: 8),
      CheckboxListTile(
        value: _save,
        contentPadding: EdgeInsets.zero,
        controlAffinity: ListTileControlAffinity.leading,
        title: Text(c.t('login.save')),
        onChanged: (v) => setState(() => _save = v ?? false),
      ),
      const SizedBox(height: 8),
      Row(children: [
        FilledButton(onPressed: c.busy ? null : _submit, child: Text(c.t('login.submit'))),
        const SizedBox(width: 12),
        TextButton(onPressed: c.busy ? null : c.otherServer, child: Text(c.t('connect.other'))),
        if (c.busy) ...[const SizedBox(width: 12), Expanded(child: Busy(c.t('login.signing_in')))],
      ]),
      ErrorLine(c),
    ]);
  }
}

class UpdatePage extends StatefulWidget {
  const UpdatePage(this.c, {super.key});

  final Controller c;

  @override
  State<UpdatePage> createState() => _UpdatePageState();
}

class _UpdatePageState extends State<UpdatePage> {
  late final SelfUpdate? _u = (widget.c.serverVersion ?? '').isEmpty
      ? null
      : SelfUpdate(
          target: widget.c.serverVersion!,
          loadKey: () => DefaultAssetBundle.of(context).loadString('assets/dahlia-archive-keyring.asc'),
        );

  @override
  void initState() {
    super.initState();
    _u?.addListener(() => setState(() {}));
    WidgetsBinding.instance.addPostFrameCallback((_) => _u?.run());
  }

  @override
  void dispose() {
    _u?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final p = context.palette;
    final u = _u;
    final server = c.serverVersion ?? '';
    final List<Widget> body;
    if (u == null) {
      body = [Text(c.t('update.server_too_old'), style: TextStyle(color: p.muted, height: 1.45))];
    } else {
      body = [
        Text(c.t('update.why', {'server': server, 'app': appVersion}), style: TextStyle(color: p.muted, height: 1.45)),
        const SizedBox(height: 20),
        switch (u.phase) {
          UpdatePhase.checking => Busy(c.t('update.checking', {'version': server})),
          UpdatePhase.downloading => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text(c.t('update.downloading', {'percent': (u.progress * 100).round()})),
              const SizedBox(height: 10),
              Meter(u.progress),
            ]),
          UpdatePhase.installing => Busy(c.t('update.installing')),
          UpdatePhase.failed => Text(c.t('error.update_${u.error}', {'version': server}),
              style: TextStyle(color: p.danger)),
          UpdatePhase.manual => Text(c.t('update.manual', {'version': server})),
        },
      ];
    }
    final failed = u == null || u.phase == UpdatePhase.failed || u.phase == UpdatePhase.manual;
    return Panel(c: c, title: c.t('update.title'), width: 520, children: [
      ...body,
      if (failed) ...[
        const SizedBox(height: 20),
        Wrap(spacing: 12, runSpacing: 10, children: [
          FilledButton(onPressed: c.connect, child: Text(c.t('connect.retry'))),
          if (u != null)
            OutlinedButton(
              onPressed: () => launchUrl(u.releasePage),
              child: Text(c.t('update.release_page')),
            ),
          TextButton(onPressed: c.otherServer, child: Text(c.t('connect.other'))),
        ]),
      ],
    ]);
  }
}
