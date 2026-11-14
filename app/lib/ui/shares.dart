// Shares area: SMB/NFS shares of /mnt/user, the SMB users and the shares of
// other devices mounted under /mnt/remotes. Same API and rules as the
// dashboard's shares page.

import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';

import '../src/controller.dart';
import '../src/format.dart';
import '../src/server.dart';
import '../src/shares.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class SharesPage extends StatefulWidget {
  const SharesPage(this.c, {super.key});

  final Controller c;

  @override
  State<SharesPage> createState() => _SharesPageState();
}

class _SharesPageState extends State<SharesPage> with AreaCalls {
  List<Map> shares = const [];
  List<String> users = const [];
  List<Map> remotes = const [];
  bool poolMounted = true;
  String host = '';
  bool loaded = false;

  final _user = TextEditingController();
  final _pass = TextEditingController();
  final _repeat = TextEditingController();

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _user.dispose();
    _pass.dispose();
    _repeat.dispose();
    super.dispose();
  }

  static List<Map> _maps(Object? v) => v is List ? v.whereType<Map>().toList() : <Map>[];

  Future<void> _load() async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => Future.wait([s.get('/api/shares'), s.get('/api/system'), s.get('/api/remotes')]), m);
    if (r == null || !mounted) return;
    final [data, info, rem] = r;
    setState(() {
      if (data is Map) {
        shares = _maps(data['shares']);
        users = data['users'] is List ? [for (final u in data['users'] as List) '$u'] : <String>[];
        poolMounted = data['pool_mounted'] == true;
      }
      if (info is Map) host = '${info['hostname'] ?? ''}';
      if (rem is Map) remotes = _maps(rem['remotes']);
      loaded = true;
    });
  }

  String _enc(Object? name) => Uri.encodeComponent('$name');

  // --- shares ---------------------------------------------------------------

  Future<void> _editShare(Map? share) async {
    final m = ScaffoldMessenger.of(context);
    final res = await showDialog<(String, Map<String, Object>)>(
      context: context,
      builder: (context) => _ShareDialog(c: c, share: share, users: users),
    );
    if (res == null) return;
    final (name, body) = res;
    final r = await call(() => s.post('/api/shares/${_enc(name)}', body), m);
    if (r != null) say(m, c.t('shares.saved', {'name': name}));
    await _load();
  }

  Future<void> _removeShare(Map share) async {
    final m = ScaffoldMessenger.of(context);
    if (!await ask(c.t('shares.confirm_remove', {'name': share['name']}), 'shares.remove', danger: true)) return;
    final r = await call(() => s.post('/api/shares/${_enc(share['name'])}/remove'), m);
    if (r != null) say(m, c.t('shares.removed', {'name': share['name']}));
    await _load();
  }

  Future<void> _emptyRecycle(Map share) async {
    final m = ScaffoldMessenger.of(context);
    if (!await ask(c.t('shares.recycle_confirm', {'name': share['name']}), 'shares.recycle_empty', danger: true)) return;
    final r = await call(() => s.post('/api/shares/${_enc(share['name'])}/empty-recycle'), m);
    if (r is Map) {
      say(m, c.t('shares.recycle_emptied', {'name': share['name'], 'n': r['files'] ?? 0, 'size': bytes((r['bytes'] as num?) ?? 0, c.t)}));
    }
  }

  // --- SMB users ------------------------------------------------------------

  bool get _userOk => _user.text.trim().isNotEmpty && passwordRe.hasMatch(_pass.text) && _pass.text == _repeat.text;

  Future<void> _addUser() async {
    final m = ScaffoldMessenger.of(context);
    final name = _user.text.trim();
    final r = await call(() => s.post('/api/smb-users', {'name': name, 'password': _pass.text}), m);
    if (r == null) return;
    _user.clear();
    _pass.clear();
    _repeat.clear();
    say(m, c.t('shares.user_added', {'name': name}));
    await _load();
  }

  Future<void> _password(String user) async {
    final m = ScaffoldMessenger.of(context);
    final pw = await showDialog<String>(context: context, builder: (context) => _PasswordDialog(c: c, user: user));
    if (pw == null) return;
    final r = await call(() => s.post('/api/smb-users/${_enc(user)}/password', {'password': pw}), m);
    if (r != null) say(m, c.t('shares.password_changed', {'name': user}));
  }

  Future<void> _removeUser(String user) async {
    final m = ScaffoldMessenger.of(context);
    if (!await ask(c.t('shares.confirm_user_remove', {'name': user}), 'shares.user_remove', danger: true)) return;
    final r = await call(() => s.post('/api/smb-users/${_enc(user)}/remove'), m);
    if (r != null) say(m, c.t('shares.user_removed', {'name': user}));
    await _load();
  }

  // --- shares of other devices ------------------------------------------------

  Future<void> _editRemote(Map? remote) async {
    final m = ScaffoldMessenger.of(context);
    final res = await showDialog<(String, Map<String, Object>)>(
      context: context,
      builder: (context) => _RemoteDialog(c: c, remote: remote),
    );
    if (res == null) return;
    final (name, body) = res;
    final r = await call(() => s.post('/api/remotes/${_enc(name)}', body), m);
    if (r != null) say(m, c.t('remotes.saved', {'name': name, 'path': '/mnt/remotes/$name'}));
    await _load();
  }

  Future<void> _removeRemote(Map remote) async {
    final m = ScaffoldMessenger.of(context);
    final name = '${remote['name']}';
    if (!await ask(c.t('remotes.confirm_remove', {'name': name}), 'shares.remove', danger: true)) return;
    Future<Object?> remove(bool force) => s.post('/api/remotes/${_enc(name)}/remove', {if (force) 'force': true});
    String? usedBy;
    Object? r = await call<Object?>(() async {
      try {
        return await remove(false);
      } on ApiError catch (e) {
        if (!usedByApps(e)) rethrow;
        usedBy = usedByDetail(e.detail);
        return null;
      }
    }, m);
    // Apps still use a folder on it: say which and ask again.
    final detail = usedBy;
    if (detail != null && mounted) {
      if (!await ask(c.t('remotes.confirm_force', {'detail': detail}), 'shares.remove', danger: true)) return;
      r = await call<Object?>(() => remove(true), m);
    }
    if (r != null) say(m, c.t('remotes.removed', {'name': name}));
    await _load();
  }

  // --- layout ---------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return PageFrame(c: c, children: [
      const SizedBox(height: 40),
      Rise(child: AreaTitle(c.t('nav.shares'), hint: host.isEmpty ? '' : c.t('shares.hint', {'host': host}))),
      if (loaded && !poolMounted && shares.isEmpty) ...[
        const SizedBox(height: 18),
        Glass(child: Text(c.t('shares.no_pool'), style: TextStyle(color: p.warn, height: 1.45))),
      ],
      Heading(c.t('shares.title'), side: [
        FilledButton(onPressed: loaded ? () => _editShare(null) : null, child: Text(c.t('shares.new'))),
        OutlinedButton(onPressed: _load, child: Text(c.t('common.refresh'))),
      ]),
      if (!loaded) Busy(c.t('common.loading')),
      if (loaded)
        Glass(
          padding: const EdgeInsets.fromLTRB(18, 8, 18, 18),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            if (shares.isEmpty) _empty(p, 'shares.none'),
            for (final sh in shares) _share(p, sh),
          ]),
        ),
      Heading(c.t('shares.users_title')),
      Glass(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c.t('shares.users_hint'), style: TextStyle(color: p.muted, height: 1.45)),
          if (loaded && users.isEmpty) ...[const SizedBox(height: 12), _empty(p, 'shares.no_users')],
          for (final u in users) _userRow(p, u),
          const SizedBox(height: 18),
          _userForm(),
        ]),
      ),
      Heading(c.t('remotes.title'), side: [
        OutlinedButton(onPressed: loaded ? () => _editRemote(null) : null, child: Text(c.t('remotes.new'))),
      ]),
      Glass(
        padding: const EdgeInsets.fromLTRB(18, 18, 18, 18),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c.t('remotes.hint'), style: TextStyle(color: p.muted, height: 1.45)),
          if (loaded && remotes.isEmpty) ...[const SizedBox(height: 12), _empty(p, 'remotes.none')],
          for (final r in remotes) _remote(p, r),
        ]),
      ),
    ]);
  }

  Widget _empty(Palette p, String key) => Padding(
        padding: const EdgeInsets.only(top: 10),
        child: Text(c.t(key), style: TextStyle(color: p.muted)),
      );

  Widget _code(Palette p, String text) => SelectableText(text, style: TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 13, color: p.fg));

  Widget _small(String key, VoidCallback onPressed, {Color? color}) => TextButton(
        style: TextButton.styleFrom(foregroundColor: color, minimumSize: const Size(0, 36)),
        onPressed: onPressed,
        child: Text(c.t(key)),
      );

  /// One entry: text on the left, buttons on the right, wrapping when narrow.
  Widget _row(Palette p, Widget lead, Widget text, List<Widget> ops) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(top: 10),
        padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
        decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(14)),
        child: Wrap(
          spacing: 12,
          runSpacing: 8,
          alignment: WrapAlignment.spaceBetween,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Row(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              lead,
              const SizedBox(width: 12),
              ConstrainedBox(constraints: const BoxConstraints(maxWidth: 640), child: text),
            ]),
            Wrap(spacing: 4, runSpacing: 4, children: ops),
          ],
        ),
      );

  Widget _icon(Palette p, IconData icon) => Container(
        width: 40,
        height: 40,
        decoration: BoxDecoration(color: p.accentSoft, borderRadius: BorderRadius.circular(11)),
        child: Icon(icon, color: p.accent, size: 22),
      );

  Color _securityColor(Palette p, String sec) => switch (sec) {
        'public' => p.warn,
        'private' => p.ok,
        _ => p.accent,
      };

  Widget _share(Palette p, Map sh) {
    final name = '${sh['name']}';
    final sec = '${sh['security']}';
    final rights = sh['users'] is Map
        ? (sh['users'] as Map).entries.map((e) => '${e.key} (${tv(c, 'access', e.value)})').join(', ')
        : '';
    final recycle = (sh['recycle_days'] as num?) ?? 0;
    return _row(
      p,
      _icon(p, Icons.folder_shared_rounded),
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(spacing: 8, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text(name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
          Tag(tv(c, 'security', sec), _securityColor(p, sec), tooltip: tv(c, 'security', '${sec}_hint')),
          if (sh['time_machine'] == true) Tag(c.t('shares.time_machine_badge'), p.accent),
          if (sh['nfs'] == true) Tag(c.t('shares.nfs_badge'), p.accent),
          if (recycle > 0) Tag(c.t('shares.recycle_badge', {'n': recycle}), p.muted),
          if (sh['exists'] == false) Tag(c.t('shares.missing'), p.warn),
        ]),
        if ('${sh['comment'] ?? ''}'.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text('${sh['comment']}', style: TextStyle(color: p.muted, fontSize: 13)),
        ],
        if (sec != 'public') ...[
          const SizedBox(height: 6),
          Text('${c.t('shares.users')}: ${rights.isEmpty ? '–' : rights}', style: TextStyle(color: p.muted, fontSize: 13)),
        ],
        if (host.isNotEmpty) ...[
          const SizedBox(height: 6),
          _code(p, shareAddress(host, name)),
          if (sh['nfs'] == true) _code(p, nfsAddress(host, name)),
        ],
      ]),
      [
        if (recycle > 0) _small('shares.recycle_empty', () => _emptyRecycle(sh)),
        _small('shares.edit', () => _editShare(sh)),
        _small('shares.remove', () => _removeShare(sh), color: p.danger),
      ],
    );
  }

  Widget _userRow(Palette p, String u) => _row(
        p,
        AppAvatar(c, {'id': u, 'name': u}, size: 40),
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: Text(u, style: const TextStyle(fontWeight: FontWeight.w600)),
        ),
        [
          _small('shares.change_password', () => _password(u)),
          _small('shares.user_remove', () => _removeUser(u), color: p.danger),
        ],
      );

  Widget _userForm() {
    final mismatch = _repeat.text.isNotEmpty && _pass.text != _repeat.text;
    Widget field(TextEditingController t, String key, {bool secret = false, String? error}) => SizedBox(
          width: 300,
          child: TextField(
            controller: t,
            obscureText: secret,
            autocorrect: false,
            inputFormatters: [LengthLimitingTextInputFormatter(secret ? 128 : 32)],
            decoration: InputDecoration(labelText: c.t(key), errorText: error),
            onChanged: (_) => setState(() {}),
            onSubmitted: (_) => _userOk ? _addUser() : null,
          ),
        );
    return Wrap(spacing: 10, runSpacing: 10, crossAxisAlignment: WrapCrossAlignment.start, children: [
      field(_user, 'field.username'),
      field(_pass, 'shares.password', secret: true),
      field(_repeat, 'field.password_repeat', secret: true, error: mismatch ? c.t('error.password_mismatch') : null),
      Padding(
        padding: const EdgeInsets.only(top: 4),
        child: FilledButton(onPressed: _userOk ? _addUser : null, child: Text(c.t('shares.user_add'))),
      ),
    ]);
  }

  Color _stateColor(Palette p, String state) => switch (state) {
        'mounted' => p.ok,
        'error' => p.danger,
        'off' => p.warn,
        _ => p.muted,
      };

  Widget _remote(Palette p, Map r) {
    final state = '${r['state']}';
    final smb = r['type'] == 'smb';
    final login = smb ? ('${r['username'] ?? ''}'.isEmpty ? c.t('remotes.guest') : '${r['username']}') : 'NFS';
    return _row(
      p,
      _icon(p, Icons.lan_rounded),
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(spacing: 8, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text('${r['name']}', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
          Tag(tv(c, 'remote_state', state), _stateColor(p, state)),
          if (r['readonly'] == true) Tag(c.t('remotes.readonly_badge'), p.muted),
        ]),
        const SizedBox(height: 6),
        Wrap(spacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
          _code(p, remoteSource(r)),
          Text('· $login', style: TextStyle(color: p.muted, fontSize: 13)),
        ]),
        const SizedBox(height: 4),
        Text('${c.t('remotes.path')}: ${r['path'] ?? ''}', style: TextStyle(color: p.muted, fontSize: 13)),
      ]),
      [
        _small('shares.edit', () => _editRemote(r)),
        _small('shares.remove', () => _removeRemote(r), color: p.danger),
      ],
    );
  }
}

/// New or existing share; pops with its name and the request body.
class _ShareDialog extends StatefulWidget {
  const _ShareDialog({required this.c, required this.share, required this.users});

  final Controller c;
  final Map? share;
  final List<String> users;

  @override
  State<_ShareDialog> createState() => _ShareDialogState();
}

class _ShareDialogState extends State<_ShareDialog> {
  final _name = TextEditingController();
  final _comment = TextEditingController();
  final _tmSize = TextEditingController();
  final _days = TextEditingController();
  String security = 'private';
  bool nfs = false;
  bool timeMachine = false;
  bool recycle = false;
  final access = <String, String>{};

  @override
  void initState() {
    super.initState();
    final sh = widget.share;
    _name.text = '${sh?['name'] ?? ''}';
    _comment.text = '${sh?['comment'] ?? ''}';
    security = securities.contains(sh?['security']) ? '${sh!['security']}' : 'private';
    nfs = sh?['nfs'] == true;
    timeMachine = sh?['time_machine'] == true;
    final size = (sh?['time_machine_size'] as num?) ?? 0;
    _tmSize.text = size > 0 ? '$size' : '';
    final days = (sh?['recycle_days'] as num?) ?? 0;
    recycle = days > 0;
    _days.text = '${days > 0 ? days : 30}';
    for (final u in widget.users) {
      access[u] = accessOf(sh, u);
    }
  }

  @override
  void dispose() {
    _name.dispose();
    _comment.dispose();
    _tmSize.dispose();
    _days.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final c = widget.c;
    final editing = widget.share != null;
    final digits = [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(7)];
    Widget hint(String text) => Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(text, style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
        );
    Widget check(bool value, String key, ValueChanged<bool> onChanged) => CheckboxListTile(
          value: value,
          dense: true,
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          title: Text(c.t(key)),
          onChanged: (v) => setState(() => onChanged(v ?? false)),
        );
    return AlertDialog(
      title: Text(editing ? c.t('shares.edit_title', {'name': widget.share!['name']}) : c.t('shares.new')),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            TextField(
              controller: _name,
              readOnly: editing,
              autofocus: !editing,
              inputFormatters: [LengthLimitingTextInputFormatter(64)],
              decoration: InputDecoration(labelText: c.t('shares.name')),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _comment,
              inputFormatters: [LengthLimitingTextInputFormatter(80)],
              decoration: InputDecoration(labelText: c.t('shares.comment')),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: security,
              decoration: InputDecoration(labelText: c.t('shares.security')),
              borderRadius: BorderRadius.circular(12),
              items: [for (final s in securities) DropdownMenuItem(value: s, child: Text(c.t('security.$s')))],
              onChanged: (v) => setState(() => security = v ?? security),
            ),
            const SizedBox(height: 8),
            hint(c.t('security.${security}_hint')),
            // Public shares give everyone write access; per-user rights do not apply.
            if (security != 'public') ...[
              if (widget.users.isEmpty) hint(c.t('shares.no_users')),
              for (final u in widget.users)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Row(children: [
                    Expanded(child: Text(u, style: const TextStyle(fontWeight: FontWeight.w600))),
                    SizedBox(
                      width: 220,
                      child: DropdownButtonFormField<String>(
                        initialValue: access[u],
                        isDense: true,
                        borderRadius: BorderRadius.circular(12),
                        items: [for (final a in accesses) DropdownMenuItem(value: a, child: Text(c.t('access.$a')))],
                        onChanged: (v) => setState(() => access[u] = v ?? 'none'),
                      ),
                    ),
                  ]),
                ),
            ],
            // NFS has no login: only public (read-write) and secure (read-only) shares.
            if (security != 'private') ...[
              check(nfs, 'shares.nfs', (v) => nfs = v),
              if (nfs) hint(c.t(security == 'public' ? 'shares.nfs_hint_rw' : 'shares.nfs_hint_ro')),
            ],
            check(timeMachine, 'shares.time_machine', (v) => timeMachine = v),
            if (timeMachine)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: TextField(
                  controller: _tmSize,
                  inputFormatters: digits,
                  decoration: InputDecoration(labelText: c.t('shares.time_machine_size')),
                ),
              ),
            check(recycle, 'shares.recycle', (v) => recycle = v),
            if (recycle) ...[
              TextField(
                controller: _days,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(3)],
                decoration: InputDecoration(labelText: c.t('shares.recycle_days')),
              ),
              const SizedBox(height: 8),
              hint(c.t('shares.recycle_hint')),
            ],
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          onPressed: _name.text.trim().isEmpty
              ? null
              : () => Navigator.pop(context, (
                    _name.text.trim(),
                    shareBody(
                      comment: _comment.text,
                      security: security,
                      users: access,
                      timeMachine: timeMachine,
                      timeMachineSize: int.tryParse(_tmSize.text) ?? 0,
                      nfs: nfs,
                      recycle: recycle,
                      recycleDays: int.tryParse(_days.text) ?? 30,
                    ),
                  )),
          child: Text(c.t('common.save')),
        ),
      ],
    );
  }
}

/// Share of another device; pops with its name and the request body.
class _RemoteDialog extends StatefulWidget {
  const _RemoteDialog({required this.c, required this.remote});

  final Controller c;
  final Map? remote;

  @override
  State<_RemoteDialog> createState() => _RemoteDialogState();
}

class _RemoteDialogState extends State<_RemoteDialog> {
  final _name = TextEditingController();
  final _host = TextEditingController();
  final _share = TextEditingController();
  final _user = TextEditingController();
  final _pass = TextEditingController();
  String type = 'smb';
  bool readonly = false;

  @override
  void initState() {
    super.initState();
    final r = widget.remote;
    _name.text = '${r?['name'] ?? ''}';
    _host.text = '${r?['host'] ?? ''}';
    _share.text = '${r?['share'] ?? ''}';
    _user.text = '${r?['username'] ?? ''}';
    type = r?['type'] == 'nfs' ? 'nfs' : 'smb';
    readonly = r?['readonly'] == true;
  }

  @override
  void dispose() {
    for (final t in [_name, _host, _share, _user, _pass]) {
      t.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final c = widget.c;
    final r = widget.remote;
    final smb = type == 'smb';
    final ok = remoteNameRe.hasMatch(_name.text.trim()) && _host.text.trim().isNotEmpty && _share.text.trim().isNotEmpty;
    Widget field(TextEditingController t, String label, {String? hint, bool secret = false, bool readOnly = false, bool focus = false, int max = 255}) =>
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: TextField(
            controller: t,
            obscureText: secret,
            readOnly: readOnly,
            autofocus: focus,
            autocorrect: false,
            inputFormatters: [LengthLimitingTextInputFormatter(max)],
            decoration: InputDecoration(labelText: label, hintText: hint),
            onChanged: (_) => setState(() {}),
          ),
        );
    return AlertDialog(
      title: Text(r == null ? c.t('remotes.new') : c.t('remotes.edit_title', {'name': r['name']})),
      content: SizedBox(
        width: 480,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            field(_name, c.t('remotes.name'), readOnly: r != null, focus: r == null, max: 32),
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: DropdownButtonFormField<String>(
                initialValue: type,
                decoration: InputDecoration(labelText: c.t('remotes.type')),
                borderRadius: BorderRadius.circular(12),
                items: [for (final t in ['smb', 'nfs']) DropdownMenuItem(value: t, child: Text(c.t('remotes.type_$t')))],
                onChanged: (v) => setState(() => type = v ?? type),
              ),
            ),
            field(_host, c.t('remotes.host'), hint: c.t('remotes.host_placeholder')),
            field(_share, c.t(smb ? 'remotes.share_smb' : 'remotes.share_nfs'),
                hint: c.t(smb ? 'remotes.share_smb_placeholder' : 'remotes.share_nfs_placeholder')),
            if (smb) ...[
              field(_user, c.t('remotes.username'), max: 64),
              field(_pass, c.t('remotes.password'), secret: true),
              Text(c.t(r?['has_password'] == true ? 'remotes.password_keep' : 'remotes.guest_hint'),
                  style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
            ],
            CheckboxListTile(
              value: readonly,
              dense: true,
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              title: Text(c.t('remotes.readonly')),
              onChanged: (v) => setState(() => readonly = v ?? false),
            ),
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(
          onPressed: !ok
              ? null
              : () => Navigator.pop(context, (
                    _name.text.trim(),
                    remoteBody(
                      type: type,
                      host: _host.text,
                      share: _share.text,
                      username: _user.text,
                      password: _pass.text,
                      readonly: readonly,
                      hasPassword: r?['has_password'] == true,
                    ),
                  )),
          child: Text(c.t('remotes.connect')),
        ),
      ],
    );
  }
}

/// New password for an SMB user, asked twice; pops with it.
class _PasswordDialog extends StatefulWidget {
  const _PasswordDialog({required this.c, required this.user});

  final Controller c;
  final String user;

  @override
  State<_PasswordDialog> createState() => _PasswordDialogState();
}

class _PasswordDialogState extends State<_PasswordDialog> {
  final _pass = TextEditingController();
  final _repeat = TextEditingController();

  @override
  void dispose() {
    _pass.dispose();
    _repeat.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.c;
    final mismatch = _repeat.text.isNotEmpty && _pass.text != _repeat.text;
    final ok = passwordRe.hasMatch(_pass.text) && _pass.text == _repeat.text;
    Widget field(TextEditingController t, String key, {String? error, bool focus = false}) => Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: TextField(
            controller: t,
            autofocus: focus,
            obscureText: true,
            inputFormatters: [LengthLimitingTextInputFormatter(128)],
            decoration: InputDecoration(labelText: c.t(key), errorText: error),
            onChanged: (_) => setState(() {}),
          ),
        );
    return AlertDialog(
      title: Text(c.t('shares.password_title', {'name': widget.user})),
      content: SizedBox(
        width: 420,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          field(_pass, 'shares.password', focus: true),
          field(_repeat, 'field.password_repeat', error: mismatch ? c.t('error.password_mismatch') : null),
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(c.t('common.cancel'))),
        FilledButton(onPressed: ok ? () => Navigator.pop(context, _pass.text) : null, child: Text(c.t('common.save'))),
      ],
    );
  }
}
