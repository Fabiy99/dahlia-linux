// Family accounts (role "family"): their own start screen with the apps an
// admin gave them and the shares their SMB user may open (/api/family).

/// Windows path of a share, as Explorer takes it: \\host\share.
String smbPath(String host, String share) => '\\\\$host\\$share';

/// macOS/Linux address of the same share.
String smbUrl(String host, String share) => 'smb://$host/$share';

/// The apps of /api/family.
List<Map> familyApps(Map family) =>
    family['apps'] is List ? (family['apps'] as List).whereType<Map>().toList() : const [];

/// The shares of /api/family: name, comment and access ("rw" or "ro").
List<Map> familyShares(Map family) => family['shares'] is List
    ? [
        for (final s in (family['shares'] as List).whereType<Map>())
          if (s['access'] == 'rw' || s['access'] == 'ro') s,
      ]
    : const [];
