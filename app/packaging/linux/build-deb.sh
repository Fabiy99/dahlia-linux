#!/bin/bash
# Package the Flutter Linux bundle as dahlia_<version>_amd64.deb.
#
# Usage: app/packaging/linux/build-deb.sh VERSION BUNDLE_DIR OUT_DIR
#   BUNDLE_DIR: app/build/linux/x64/release/bundle
# Needs: dpkg-deb
set -euo pipefail

VERSION="$1"
BUNDLE="$2"
OUT="$3"
HERE="$(cd "$(dirname "$0")" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
chmod 0755 "$STAGE"

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "bad version: $VERSION" >&2
    exit 1
fi
if [ ! -x "$BUNDLE/dahlia" ]; then
    echo "$BUNDLE/dahlia missing, run flutter build linux first" >&2
    exit 1
fi

# /opt/dahlia: the self-update checks for exactly this path (installer.dart).
install -d "$STAGE/opt/dahlia" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/512x512/apps" "$STAGE/DEBIAN"
cp -a "$BUNDLE"/. "$STAGE/opt/dahlia/"
ln -s /opt/dahlia/dahlia "$STAGE/usr/bin/dahlia"
install -m 0644 "$HERE/dahlia.desktop" "$STAGE/usr/share/applications/dahlia.desktop"
install -m 0644 "$HERE/dahlia.png" "$STAGE/usr/share/icons/hicolor/512x512/apps/dahlia.png"

size="$(du -sk "$STAGE" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: dahlia
Version: $VERSION
Architecture: amd64
Maintainer: Dahlia Linux <dahlia@invalid>
Installed-Size: $size
Depends: libgtk-3-0t64 | libgtk-3-0, libsecret-tools, pkexec | policykit-1
Section: net
Priority: optional
Homepage: https://github.com/Fabiy99/dahlia-linux
Description: Desktop app for Dahlia Linux servers
 Native client for a Dahlia home server: pairing, start page, server
 updates. Always runs the same version as the server and updates itself.
CONTROL

mkdir -p "$OUT"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUT/dahlia_${VERSION}_amd64.deb"
