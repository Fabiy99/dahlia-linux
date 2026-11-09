#!/bin/bash
# Build the apt repository, sign it with a throwaway key and check that apt
# accepts it with the Dahlia source file, and rejects a wrong signature.
#
# Usage: tests/integration/apt-repo.sh
# Needs: dpkg-deb, apt-ftparchive, apt-get, gpg, gpgv (no root)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
REPO="$ROOT/build/repo"
SOURCES="$ROOT/packages/dahlia-archive-keyring/etc/apt/sources.list.d/dahlia.sources"

T="$(mktemp -d)"
trap 'GNUPGHOME="$T/gpg" gpgconf --kill gpg-agent >/dev/null 2>&1 || true; rm -rf "$T"' EXIT

gen_key() { # <gnupghome> <name>
    mkdir -p "$1" && chmod 700 "$1"
    GNUPGHOME="$1" gpg --batch --quiet --passphrase '' \
        --quick-gen-key "$2 <test@invalid>" ed25519 sign 1d
}
gen_key "$T/gpg" "Dahlia test"
gen_key "$T/other" "Someone else"
GNUPGHOME="$T/gpg" gpg --batch --armor --export > "$T/test.asc"

"$ROOT/tools/build-packages.sh"

echo "== sign"
DAHLIA_SIGNING_KEY="$(GNUPGHOME="$T/gpg" gpg --batch --armor --export-secret-keys)" \
    DAHLIA_KEYRING="$T/test.asc" "$ROOT/tools/sign-repo.sh"

echo "== signing with a key that does not match the keyring must fail"
if DAHLIA_SIGNING_KEY="$(GNUPGHOME="$T/other" gpg --batch --armor --export-secret-keys)" \
        DAHLIA_KEYRING="$T/test.asc" "$ROOT/tools/sign-repo.sh" >/dev/null 2>&1; then
    echo "FAIL: sign-repo.sh accepted a key that does not match the keyring" >&2
    exit 1
fi

# Re-sign with the right key (the failed run removed the signatures).
DAHLIA_SIGNING_KEY="$(GNUPGHOME="$T/gpg" gpg --batch --armor --export-secret-keys)" \
    DAHLIA_KEYRING="$T/test.asc" "$ROOT/tools/sign-repo.sh" >/dev/null

apt_in() { # <dir> <apt-get|apt-cache> args...
    local dir="$1" tool="$2"
    shift 2
    "$tool" -q \
        -o Dir::State="$dir/state" -o Dir::State::status=/var/lib/dpkg/status \
        -o Dir::Cache="$dir/cache" -o Dir::Etc::SourceList=/dev/null \
        -o Dir::Etc::SourceParts="$dir/sources.list.d" \
        -o Dir::Etc::Preferences=/dev/null -o Dir::Etc::PreferencesParts="$dir/empty" \
        -o Debug::NoLocking=1 -o APT::Sandbox::User="$(id -un)" "$@"
}
apt_dir() { # <dir> <trusted key>: apt tree using the shipped source file
    mkdir -p "$1/state/lists/partial" "$1/cache/archives/partial" \
        "$1/sources.list.d" "$1/empty"
    sed -e "s|^URIs: .*|URIs: file:$REPO/|" -e "s|^Signed-By: .*|Signed-By: $2|" \
        "$SOURCES" > "$1/sources.list.d/dahlia.sources"
}

echo "== apt accepts the repository"
apt_dir "$T/ok" "$T/test.asc"
apt_in "$T/ok" apt-get update
candidate="$(apt_in "$T/ok" apt-cache policy dahlia-base | awk '/Candidate:/ { print $2 }')"
if [ "$candidate" != "$VERSION" ]; then
    echo "FAIL: candidate for dahlia-base is '$candidate', expected $VERSION" >&2
    exit 1
fi

echo "== apt rejects the repository with a different trusted key"
GNUPGHOME="$T/other" gpg --batch --armor --export > "$T/other.asc"
apt_dir "$T/bad" "$T/other.asc"
out="$(apt_in "$T/bad" apt-get update 2>&1 || true)"
if ! grep -q "NO_PUBKEY\|not signed\|signatures couldn't be verified\|is not signed" <<<"$out"; then
    echo "FAIL: apt did not reject the signature:" >&2
    echo "$out" >&2
    exit 1
fi

echo "OK: signed repository accepted, foreign signature rejected (dahlia-base $VERSION)"
