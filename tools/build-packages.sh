#!/bin/bash
# Build all Dahlia .deb packages from packages/<name>/ into build/pool/
# and generate a flat apt repository index in build/repo/.
#
# Usage: tools/build-packages.sh
# Needs: dpkg-deb, apt-ftparchive (apt-utils)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILD="$ROOT/build"
POOL="$BUILD/pool"
REPO="$BUILD/repo"

rm -rf "$BUILD/pkgroot" "$POOL" "$REPO"
mkdir -p "$BUILD/pkgroot" "$POOL" "$REPO"

for src in "$ROOT"/packages/*/; do
    name="$(basename "$src")"
    stage="$BUILD/pkgroot/$name"
    cp -a "$src" "$stage"
    # Bytecode from local test runs must not end up in the package.
    find "$stage" -name __pycache__ -type d -prune -exec rm -rf {} +

    # Substitute the version in every text file that carries the marker.
    grep -rlI '@VERSION@' "$stage" | while read -r f; do
        sed -i "s/@VERSION@/$VERSION/g" "$f"
    done

    # Maintainer scripts must be executable; dpkg-deb refuses otherwise.
    for script in preinst postinst prerm postrm; do
        if [ -f "$stage/DEBIAN/$script" ]; then
            chmod 0755 "$stage/DEBIAN/$script"
        fi
    done

    # Every file under etc/ is a conffile, so local edits survive upgrades.
    if [ -d "$stage/etc" ]; then
        (cd "$stage" && find etc -type f | sort | sed 's|^|/|') > "$stage/DEBIAN/conffiles"
    fi

    dpkg-deb --root-owner-group -Zxz --build "$stage" "$POOL/${name}_${VERSION}_all.deb"
done

# Flat repository: "deb [trusted=yes] file:/path ./" style, used on the ISO.
cp "$POOL"/*.deb "$REPO/"
(cd "$REPO" && apt-ftparchive packages . > Packages && gzip -9kf Packages)
(cd "$REPO" && apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=Dahlia \
    -o APT::FTPArchive::Release::Label=Dahlia \
    -o APT::FTPArchive::Release::Suite=trixie \
    release . > Release)

echo "Built $(find "$POOL" -name '*.deb' | wc -l) package(s), version $VERSION:"
ls -1 "$POOL"
