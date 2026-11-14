// Addresses section of the settings: mDNS names for the apps and the local
// CA with its fingerprint and certificate download. Same API as the
// dashboard's Addresses page.

import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:material_ui/material_ui.dart';

import '../src/addresses.dart';
import '../src/controller.dart';
import 'area.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets.dart';

class AddressesSection extends StatefulWidget {
  const AddressesSection(this.c, {super.key});

  final Controller c;

  @override
  State<AddressesSection> createState() => _AddressesSectionState();
}

class _AddressesSectionState extends State<AddressesSection> with AreaCalls {
  Map? d;
  String fingerprint = '';
  bool mdns = false;
  bool busy = false;

  @override
  Controller get c => widget.c;

  @override
  void initState() {
    super.initState();
    _load(fill: true);
  }

  Future<void> _load({bool fill = false}) async {
    final m = ScaffoldMessenger.of(context);
    final r = await call(() => Future.wait([s.get('/api/domains'), s.get('/api/pairing')]), m);
    if (r == null || !mounted) return;
    final [domains, pairing] = r;
    if (domains is! Map) return;
    setState(() {
      d = domains;
      fingerprint = pairing is Map ? '${pairing['ca_fingerprint'] ?? ''}' : '';
      if (fill) mdns = domains['mdns_aliases'] == true;
    });
  }

  Future<void> _save() async {
    final m = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    final r = await call(() => s.post('/api/domains', {'mdns_aliases': mdns}, const Duration(minutes: 2)), m);
    if (r != null) say(m, c.t(domainsSavedKey(r)));
    if (mounted) setState(() => busy = false);
    await _load(fill: true);
  }

  Future<void> _downloadCa() async {
    final m = ScaffoldMessenger.of(context);
    final where = await getSaveLocation(suggestedName: 'dahlia-ca.crt');
    if (where == null || !mounted) return;
    final ok = await call(() async {
      await s.download('/dahlia-ca.crt', File(where.path));
      return true;
    }, m);
    if (ok == true) say(m, c.t('fm.downloaded', {'path': where.path}));
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final data = d;
    if (data == null) return Busy(c.t('common.loading'));
    final grid = pageGrid(MediaQuery.of(context).size.width);
    final full = grid.unit * grid.cols + 18 * (grid.cols - 1);
    final half = grid.cols == 1 ? full : (full - 18) / 2;
    const mono = TextStyle(fontFamily: monoFont, fontFamilyFallback: monoFallback, fontSize: 12.5, height: 1.4);
    Widget hint(String key) => Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Text(c.t(key), style: TextStyle(color: p.muted, fontSize: 13, height: 1.4)),
        );
    Widget title(String key) => Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: Text(c.t(key), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
        );
    return Wrap(spacing: 18, runSpacing: 18, children: [
      SizedBox(
        width: half,
        child: Glass(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            title('domains.title'),
            hint('domains.hint'),
            CheckboxListTile(
              value: mdns,
              dense: true,
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              title: Text(c.t('domains.mdns')),
              onChanged: (v) => setState(() => mdns = v ?? false),
            ),
            hint('domains.mdns_hint'),
            FilledButton(onPressed: busy ? null : _save, child: Text(c.t('common.save'))),
          ]),
        ),
      ),
      SizedBox(
        width: half,
        child: Glass(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            title('domains.ca_title'),
            hint('domains.ca_hint'),
            for (final (key, value) in tlsFacts(data))
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  SizedBox(width: 230, child: Text(c.t(key), style: TextStyle(color: p.muted))),
                  Expanded(child: SelectableText(value, style: key == 'domains.ca_fingerprint' ? mono : null)),
                ]),
              ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: _downloadCa,
              icon: const Icon(Icons.download_rounded, size: 18),
              label: Text(c.t('domains.ca_download')),
            ),
            const SizedBox(height: 22),
            Text(c.t('domains.fingerprint_title'), style: const TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            hint('domains.fingerprint_hint'),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: p.sub, borderRadius: BorderRadius.circular(12)),
              child: SelectableText(fingerprint.isEmpty ? '-' : fingerprint, style: mono),
            ),
          ]),
        ),
      ),
    ]);
  }
}
