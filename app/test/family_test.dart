import 'package:dahlia/src/family.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('share paths for Explorer and Finder', () {
    expect(smbPath('dahlia.local', 'fotos'), r'\\dahlia.local\fotos');
    expect(smbUrl('dahlia.local', 'fotos'), 'smb://dahlia.local/fotos');
  });

  test('apps and shares of a family account', () {
    final fam = {
      'apps': [
        {'id': 'immich'},
        'junk',
      ],
      'shares': [
        {'name': 'fotos', 'access': 'rw'},
        {'name': 'filme', 'access': 'ro'},
        {'name': 'odd', 'access': 'admin'},
      ],
    };
    expect(familyApps(fam).map((a) => a['id']), ['immich']);
    expect(familyShares(fam).map((s) => s['name']), ['fotos', 'filme']);
    expect(familyApps(const {}), isEmpty);
    expect(familyShares(const {}), isEmpty);
  });
}
