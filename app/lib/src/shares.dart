// Shares area without widgets: addresses, request bodies and the name rules
// of the agent, the same as the dashboard's shares page.

import 'server.dart';

const securities = ['public', 'secure', 'private'];
const accesses = ['none', 'ro', 'rw'];

/// Patterns of the agent (smb.py, remotes.py), checked before sending.
final remoteNameRe = RegExp(r'^[a-z0-9][a-z0-9-]{0,31}$');
final passwordRe = RegExp(r'^[^\x00-\x1f\x7f]{8,128}$');

String shareAddress(String host, String name) => '\\\\$host.local\\$name';

String nfsAddress(String host, String name) => '$host.local:/mnt/user/$name';

/// \\host\share for SMB, host:/export for NFS.
String remoteSource(Map r) => r['type'] == 'smb' ? '\\\\${r['host']}\\${r['share']}' : '${r['host']}:${r['share']}';

/// Access preselected for [user]: the share's, new shares give read and write.
String accessOf(Map? share, String user) {
  final users = share?['users'];
  final a = users is Map ? users[user] : null;
  return a is String ? a : (share == null ? 'rw' : 'none');
}

/// Body of `POST /api/shares/<name>`. Public shares have no user rights,
/// private ones no NFS (it has no login).
Map<String, Object> shareBody({
  required String comment,
  required String security,
  required Map<String, String> users,
  required bool timeMachine,
  required int timeMachineSize,
  required bool nfs,
  required bool recycle,
  required int recycleDays,
}) =>
    {
      'comment': comment.trim(),
      'security': security,
      'users': security == 'public' ? <String, String>{} : {for (final e in users.entries) if (e.value != 'none') e.key: e.value},
      'time_machine': timeMachine,
      'time_machine_size': timeMachine ? timeMachineSize : 0,
      'nfs': security != 'private' && nfs,
      'recycle_days': recycle ? recycleDays.clamp(1, 365) : 0,
    };

/// Body of `POST /api/remotes/<name>`. An empty password while editing keeps
/// the saved one; NFS has neither user nor password.
Map<String, Object> remoteBody({
  required String type,
  required String host,
  required String share,
  required String username,
  required String password,
  required bool readonly,
  bool hasPassword = false,
}) {
  final smb = type == 'smb';
  return {
    'type': type,
    'host': host.trim(),
    'share': share.trim(),
    'username': smb ? username.trim() : '',
    if (smb && (password.isNotEmpty || !hasPassword)) 'password': password,
    'readonly': readonly,
  };
}

/// Removing a remote failed only because apps use folders on it; asking
/// again with force removes it anyway.
bool usedByApps(ApiError e) => e.status == 422 && e.detail.contains('used by apps');

/// "used by apps: a, b" from the agent's message, without the CLI hint.
String usedByDetail(String detail) => detail.split(';').first;
