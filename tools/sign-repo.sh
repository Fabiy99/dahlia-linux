#!/bin/bash
# Sign the flat apt repository in build/repo/ (InRelease and Release.gpg)
# and the desktop app's update manifests in build/repo/app/ (<version>.json.sig,
# see tools/fetch-app-manifests.sh and app/lib/src/manifest.dart).
#
# Usage: DAHLIA_SIGNING_KEY="$(cat private.asc)" tools/sign-repo.sh
# Env:   DAHLIA_SIGNING_KEY  ASCII-armored private key without passphrase (required)
#        DAHLIA_KEYRING      public key the signature must verify against
#                            (default: the key shipped in the archive-keyring package)
# Needs: gpg, gpgv
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$ROOT/build/repo"
KEYRING="${DAHLIA_KEYRING:-$ROOT/packages/archive-keyring/usr/share/keyrings/dahlia-archive-keyring.asc}"

if [ -z "${DAHLIA_SIGNING_KEY:-}" ]; then
    echo "DAHLIA_SIGNING_KEY is empty" >&2
    exit 1
fi
if [ ! -f "$REPO/Release" ]; then
    echo "$REPO/Release missing, run tools/build-packages.sh first" >&2
    exit 1
fi
if [ ! -f "$KEYRING" ]; then
    echo "public key $KEYRING missing" >&2
    exit 1
fi

GNUPGHOME="$(mktemp -d)"
export GNUPGHOME
trap 'gpgconf --kill gpg-agent >/dev/null 2>&1 || true; rm -rf "$GNUPGHOME"' EXIT

printf '%s\n' "$DAHLIA_SIGNING_KEY" | gpg --batch --quiet --import
fpr="$(gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" { print $10; exit }')"
if [ -z "$fpr" ]; then
    echo "no secret key found in DAHLIA_SIGNING_KEY" >&2
    exit 1
fi

rm -f "$REPO/InRelease" "$REPO/Release.gpg"
gpg --batch --yes --local-user "$fpr" --digest-algo SHA512 \
    --clearsign --output "$REPO/InRelease" "$REPO/Release"
gpg --batch --yes --local-user "$fpr" --digest-algo SHA512 \
    --armor --detach-sign --output "$REPO/Release.gpg" "$REPO/Release"

# The signature must verify against the key that systems actually trust;
# a mismatched secret would publish a repository nobody can update from.
gpg --batch --yes --dearmor --output "$GNUPGHOME/trusted.gpg" < "$KEYRING"
gpgv --keyring "$GNUPGHOME/trusted.gpg" "$REPO/InRelease"
gpgv --keyring "$GNUPGHOME/trusted.gpg" "$REPO/Release.gpg" "$REPO/Release"

# The desktop app verifies these with the same public key.
shopt -s nullglob
for manifest in "$REPO"/app/*.json; do
    gpg --batch --yes --local-user "$fpr" --digest-algo SHA512 \
        --armor --detach-sign --output "$manifest.sig" "$manifest"
    gpgv --keyring "$GNUPGHOME/trusted.gpg" "$manifest.sig" "$manifest"
done
shopt -u nullglob

echo "Signed $REPO with key $fpr"
