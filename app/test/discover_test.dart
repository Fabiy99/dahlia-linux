import 'package:dahlia/src/discover.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('host names from SRV records', () {
    expect(hostOf('Dahlia.local.', 443), 'dahlia.local');
    expect(hostOf('nas.local.', 8443), 'nas.local:8443');
    expect(nameOf('test._dahlia._tcp.local'), 'test');
  });
}
