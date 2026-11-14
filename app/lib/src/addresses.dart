// Addresses section without widgets: the facts about the local CA and the
// server certificate, as the dashboard's Addresses page lists them.

/// Rows of the certificate card: locale key and value ('-' when unknown).
List<(String, String)> tlsFacts(Map d) {
  String of(Object? part, String key) {
    final v = part is Map ? '${part[key] ?? ''}' : '';
    return v.isEmpty ? '-' : v;
  }

  final names = d['names'] is List ? [for (final n in d['names'] as List) '$n'] : const <String>[];
  return [
    ('domains.ca_fingerprint', of(d['ca'], 'sha256')),
    ('domains.ca_valid', of(d['ca'], 'not_after')),
    ('domains.cert_valid', of(d['cert'], 'not_after')),
    ('domains.cert_names', names.isEmpty ? '-' : names.join(', ')),
  ];
}

/// Message after saving: a renewed CA must be imported again everywhere.
String domainsSavedKey(Object? response) =>
    response is Map && response['ca_renewed'] == true ? 'domains.saved_new_ca' : 'domains.saved';
