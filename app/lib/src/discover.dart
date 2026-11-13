// Finds Dahlia servers in the local network: mDNS service _dahlia._tcp,
// published by avahi from /etc/avahi/services/dahlia.service.

import 'dart:async';
import 'dart:io';

import 'package:multicast_dns/multicast_dns.dart';

const service = '_dahlia._tcp.local';

class Found {
  const Found(this.name, this.host);

  /// Service name, usually the server's hostname.
  final String name;

  /// What the app connects to: "dahlia.local" or "dahlia.local:8443".
  final String host;
}

/// "Dahlia.local." -> "dahlia.local"; port 443 is left out.
String hostOf(String target, int port) {
  var h = target.toLowerCase();
  while (h.endsWith('.')) {
    h = h.substring(0, h.length - 1);
  }
  return port == 443 ? h : '$h:$port';
}

String nameOf(String ptr) {
  final i = ptr.indexOf('.$service');
  return i < 0 ? ptr : ptr.substring(0, i);
}

/// Browses for [wait]; errors (no network, port 5353 taken) give an empty list.
Future<List<Found>> browse({Duration wait = const Duration(seconds: 3)}) async {
  final client = MDnsClient(rawDatagramSocketFactory: (host, int port,
          {bool reuseAddress = true, bool reusePort = false, int ttl = 1}) =>
      RawDatagramSocket.bind(host, port, reuseAddress: true, reusePort: !Platform.isWindows, ttl: ttl));
  final found = <String, Found>{};
  try {
    await client.start();
    final ptrs = client.lookup<PtrResourceRecord>(
      ResourceRecordQuery.serverPointer(service),
      timeout: wait,
    );
    await for (final ptr in ptrs) {
      await for (final srv in client.lookup<SrvResourceRecord>(
        ResourceRecordQuery.service(ptr.domainName),
        timeout: const Duration(seconds: 1),
      )) {
        final host = hostOf(srv.target, srv.port);
        found[host] = Found(nameOf(ptr.domainName), host);
      }
    }
  } on Object {
    // Discovery is a convenience; the address field always works.
  } finally {
    client.stop();
  }
  final list = found.values.toList()..sort((a, b) => a.host.compareTo(b.host));
  return list;
}
