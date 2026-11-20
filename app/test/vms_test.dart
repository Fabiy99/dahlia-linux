import 'package:dahlia/src/vms.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('state of /api/vms', () {
    final st = VmState({
      'installed': true,
      'install_job': {'active': 'inactive', 'result': 'success', 'started': 'Tue 2026-11-18'},
      'host': {'cpus': 8, 'memory': 16384},
      'images': ['debian/13', 'alpine/3.22'],
      'lan': true,
      'error': null,
      'instances': [
        {'name': 'web', 'type': 'container', 'status': 'running', 'cpus': 2, 'memory': 1024,
         'autostart': true, 'addresses': ['10.12.0.5'], 'network': 'nat', 'console': false},
        {'name': 'win', 'type': 'vm', 'status': 'stopped', 'network': 'lan', 'console': true, 'iso': true},
      ],
      'creating': [
        {'name': 'db', 'job': {'active': 'active', 'result': 'success'}},
        {'name': 'old', 'job': {'active': 'failed', 'result': 'exit-code'}},
      ],
    });
    expect([st.installed, st.installing, st.installFailed, st.cpus, st.memory, st.lan, st.cache],
        [true, false, false, 8, 16384, true, false]);
    final [web, win] = st.instances;
    expect([web.vm, web.running, web.memory, web.addresses, web.shell],
        [false, true, 1024, ['10.12.0.5'], 'incus exec web -- bash -l']);
    expect([win.vm, win.running, win.lan, win.console, win.iso, win.cpus], [true, false, true, true, true, null]);
    expect([for (final c in st.creating) (c.name, c.failed)], [('db', false), ('old', true)]);
    expect(st.busy, isTrue);
    expect(VmState({'installed': false, 'install_job': {'active': 'failed', 'result': 'exit-code', 'started': 'x'}})
        .installFailed, isTrue);
  });

  test('request for a new instance', () {
    Map<String, Object?>? req({String name = 'win', bool vm = true, bool iso = true, String? root = '/srv',
            double gb = 8, bool windows = true}) =>
        vmRequest(
          name: name,
          vm: vm,
          fromIso: iso,
          image: 'debian/13',
          isoRoot: root,
          isoPath: root == null ? null : 'isos/win11.iso',
          cpus: 4,
          memoryGb: gb,
          diskGb: 64,
          lan: true,
          autostart: false,
          start: true,
          secureboot: true,
          windows: windows,
        );
    expect(req(), {
      'name': 'win', 'kind': 'vm', 'source': 'iso', 'image': 'debian/13', 'iso_root': '/srv',
      'iso_path': 'isos/win11.iso', 'cpus': 4, 'memory': 8192, 'disk': 64, 'network': 'lan',
      'autostart': false, 'start': true, 'secureboot': true, 'windows': true,
    });
    expect(req(root: null), isNull);
    expect(req(name: 'Win'), isNull);
    expect(req(gb: 0), isNull);
    final ct = req(vm: false, gb: 1.5)!;
    expect([ct['kind'], ct['source'], ct['memory'], ct['disk'], ct['windows'], ct.containsKey('iso_root')],
        ['container', 'image', 1536, 10, false, false]);
  });
}
