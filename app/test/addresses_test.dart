import 'package:dahlia/src/addresses.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('certificate facts, dash for what is missing', () {
    expect(tlsFacts({
      'ca': {'sha256': 'AB:CD', 'not_after': '2036-11-10'},
      'cert': {'not_after': '2027-02-08'},
      'names': ['dahlia.local', 'jellyfin.local'],
    }), [
      ('domains.ca_fingerprint', 'AB:CD'),
      ('domains.ca_valid', '2036-11-10'),
      ('domains.cert_valid', '2027-02-08'),
      ('domains.cert_names', 'dahlia.local, jellyfin.local'),
    ]);
    expect(tlsFacts({}).map((e) => e.$2), ['-', '-', '-', '-']);
  });

  test('saved message names a renewed CA', () {
    expect(domainsSavedKey({'ca_renewed': true}), 'domains.saved_new_ca');
    expect(domainsSavedKey({'ca_renewed': false}), 'domains.saved');
    expect(domainsSavedKey(null), 'domains.saved');
  });
}
