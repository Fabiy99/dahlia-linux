import 'package:dahlia/src/server.dart';
import 'package:dahlia/src/shares.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_dahlia.dart';

void main() {
  test('addresses of a share and of a remote', () {
    expect(shareAddress('dahlia', 'media'), r'\\dahlia.local\media');
    expect(nfsAddress('dahlia', 'media'), 'dahlia.local:/mnt/user/media');
    expect(remoteSource({'type': 'smb', 'host': 'nas.local', 'share': 'Movies'}), r'\\nas.local\Movies');
    expect(remoteSource({'type': 'nfs', 'host': '192.168.1.20', 'share': '/volume1/movies'}), '192.168.1.20:/volume1/movies');
  });

  test('preselected access: new shares read and write, existing ones as saved', () {
    final share = {
      'users': {'anna': 'ro'}
    };
    expect(accessOf(null, 'anna'), 'rw');
    expect(accessOf(share, 'anna'), 'ro');
    expect(accessOf(share, 'ben'), 'none');
  });

  Map<String, Object> body(String security, {bool nfs = true, bool tm = true, bool recycle = true, int days = 30}) => shareBody(
        comment: '  Filme  ',
        security: security,
        users: {'anna': 'rw', 'ben': 'none', 'carl': 'ro'},
        timeMachine: tm,
        timeMachineSize: 500,
        nfs: nfs,
        recycle: recycle,
        recycleDays: days,
      );

  test('share body follows the dashboard rules', () {
    final private = body('private');
    expect(private['comment'], 'Filme');
    expect(private['users'], {'anna': 'rw', 'carl': 'ro'});
    expect(private['nfs'], isFalse, reason: 'NFS has no login');
    expect(private['time_machine_size'], 500);
    expect(private['recycle_days'], 30);

    final public = body('public');
    expect(public['users'], isEmpty, reason: 'public shares have no user rights');
    expect(public['nfs'], isTrue);

    expect(body('secure', tm: false)['time_machine_size'], 0);
    expect(body('secure', recycle: false)['recycle_days'], 0);
    expect(body('secure', days: 900)['recycle_days'], 365);
    expect(body('secure', days: 0)['recycle_days'], 1);
  });

  test('remote body: saved password kept, NFS without login', () {
    Map<String, Object> remote(String type, String password, {bool saved = false}) => remoteBody(
        type: type, host: ' nas.local ', share: ' Movies ', username: ' anna ', password: password, readonly: true, hasPassword: saved);
    final smb = remote('smb', 'secret');
    expect(smb, {'type': 'smb', 'host': 'nas.local', 'share': 'Movies', 'username': 'anna', 'password': 'secret', 'readonly': true});
    expect(remote('smb', '', saved: true).containsKey('password'), isFalse);
    expect(remote('smb', '')['password'], '', reason: 'guest login without a saved password');
    final nfs = remote('nfs', 'secret');
    expect(nfs['username'], '');
    expect(nfs.containsKey('password'), isFalse);
  });

  test('name and password rules of the agent', () {
    expect(remoteNameRe.hasMatch('nas-1'), isTrue);
    expect(remoteNameRe.hasMatch('NAS'), isFalse);
    expect(remoteNameRe.hasMatch('-nas'), isFalse);
    expect(passwordRe.hasMatch('1234567'), isFalse);
    expect(passwordRe.hasMatch('12345678'), isTrue);
    expect(passwordRe.hasMatch('1234567\n'), isFalse);
  });

  test('removing a remote used by apps asks again, force removes it', () async {
    final fake = FakeDahlia();
    await fake.start();
    addTearDown(() => fake.http.close(force: true));
    final s = Server(host: fake.host, pem: fixture('ca.crt'), appVersion: '1.4.0');
    try {
      await s.post('/api/remotes/nas/remove', {});
      fail('expected an error');
    } on ApiError catch (e) {
      expect(usedByApps(e), isTrue);
      expect(usedByDetail(e.detail), 'used by apps: jellyfin, sonarr');
    }
    expect(await s.post('/api/remotes/nas/remove', {'force': true}), {'name': 'nas'});
    expect(usedByApps(ApiError(422, 'error.agent', 'mount failed')), isFalse);
  });
}
