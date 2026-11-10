#!/bin/bash
# Build the Dahlia installer ISO by remastering the current Debian 13 netinst.
#
#   1. download netinst + SHA256SUMS, verify signature and checksum
#   2. append iso/preseed.cfg and the mode preseeds to the installer initrd
#   3. replace the BIOS (isolinux) and UEFI (grub) boot menus
#   4. add the Dahlia packages and installer scripts under /dahlia
#   5. write a new hybrid ISO with the original boot setup (BIOS + UEFI)
#
# Usage: iso/build-iso.sh            (run tools/build-packages.sh first)
# Output: build/dahlia-linux-<version>-amd64.iso and .sha256
# Needs: curl, gpg, xorriso, cpio, gzip, sha256sum, md5sum
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILD="$ROOT/build"
CACHE="$BUILD/cache"
WORK="$BUILD/iso-work"
OUT="$BUILD/dahlia-linux-${VERSION}-amd64.iso"

MIRROR="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd"
# Debian CD signing key (see https://www.debian.org/CD/verify).
DEBIAN_CD_KEY="DF9B9C49EAA9298432589D76DA87E80D6294BE9B"

if ! ls "$BUILD"/pool/*.deb >/dev/null 2>&1; then
    echo "No packages in $BUILD/pool, run tools/build-packages.sh first" >&2
    exit 1
fi

mkdir -p "$CACHE"
rm -rf "$WORK"
mkdir -p "$WORK/initrd-add" "$WORK/gnupg"
chmod 700 "$WORK/gnupg"

# --- 1. download and verify ------------------------------------------------
curl -fsSL -o "$CACHE/SHA256SUMS" "$MIRROR/SHA256SUMS"
curl -fsSL -o "$CACHE/SHA256SUMS.sign" "$MIRROR/SHA256SUMS.sign"

gpg --homedir "$WORK/gnupg" --batch --quiet \
    --keyserver hkps://keyring.debian.org --recv-keys "$DEBIAN_CD_KEY"
gpg --homedir "$WORK/gnupg" --batch --status-fd 1 \
    --verify "$CACHE/SHA256SUMS.sign" "$CACHE/SHA256SUMS" \
    | grep -q "VALIDSIG $DEBIAN_CD_KEY"

NETINST="$(grep -oE 'debian-13\.[0-9]+\.[0-9]+-amd64-netinst\.iso' "$CACHE/SHA256SUMS" | head -n1)"
if [ -z "$NETINST" ]; then
    echo "No Debian 13 netinst image listed in $MIRROR/SHA256SUMS" >&2
    exit 1
fi
if [ ! -f "$CACHE/$NETINST" ]; then
    curl -fL --retry 3 -o "$CACHE/$NETINST.part" "$MIRROR/$NETINST"
    mv "$CACHE/$NETINST.part" "$CACHE/$NETINST"
fi
(cd "$CACHE" && grep " $NETINST\$" SHA256SUMS | sha256sum -c -)
echo "Base image: $NETINST"

# --- 2. initrd with preseed ------------------------------------------------
xorriso -osirrox on -indev "$CACHE/$NETINST" \
    -extract /install.amd/initrd.gz "$WORK/initrd.gz" \
    -extract /md5sum.txt "$WORK/md5sum.txt"
chmod u+w "$WORK/initrd.gz" "$WORK/md5sum.txt"

gunzip -c "$WORK/initrd.gz" > "$WORK/initrd.cpio"
cp "$ROOT/iso/preseed.cfg" "$WORK/initrd-add/preseed.cfg"
cp "$ROOT/iso/installer/dahlia-auto.cfg" "$ROOT/iso/installer/dahlia-guided.cfg" "$WORK/initrd-add/"
(cd "$WORK/initrd-add" && printf '%s\n' preseed.cfg dahlia-auto.cfg dahlia-guided.cfg \
    | cpio --quiet -H newc -o -A -F "$WORK/initrd.cpio")
gzip -9 -c "$WORK/initrd.cpio" > "$WORK/initrd-dahlia.gz"

# --- 3./4. payload tree ----------------------------------------------------
mkdir -p "$WORK/dahlia/pool"
cp "$BUILD"/pool/*.deb "$WORK/dahlia/pool/"
for f in pick-disk.sh guided.sh apply-answers.sh lib.sh late-command.sh; do
    install -m 0755 "$ROOT/iso/installer/$f" "$WORK/dahlia/$f"
done
install -m 0644 "$ROOT/iso/installer/dahlia.templates" "$WORK/dahlia/dahlia.templates"
printf 'DAHLIA_VERSION=%s\nDEBIAN_BASE=%s\n' "$VERSION" "$NETINST" > "$WORK/dahlia/release"

# md5sum.txt: drop entries for replaced files, add the new ones, so the
# installer's "check CD integrity" stays usable.
md5_line() { printf '%s  ./%s\n' "$(md5sum "$1" | cut -d' ' -f1)" "$2"; }
grep -vE '  \./(install\.amd/initrd\.gz|isolinux/isolinux\.cfg|boot/grub/grub\.cfg)$' \
    "$WORK/md5sum.txt" > "$WORK/md5sum.new"
{
    md5_line "$WORK/initrd-dahlia.gz" install.amd/initrd.gz
    md5_line "$ROOT/iso/isolinux.cfg" isolinux/isolinux.cfg
    md5_line "$ROOT/iso/grub.cfg" boot/grub/grub.cfg
    (cd "$WORK" && find dahlia -type f | sort | while read -r f; do md5_line "$f" "$f"; done)
} >> "$WORK/md5sum.new"

# --- 5. write the ISO -------------------------------------------------------
rm -f "$OUT" "$OUT.sha256"
xorriso -indev "$CACHE/$NETINST" -outdev "$OUT" \
    -map "$WORK/initrd-dahlia.gz" /install.amd/initrd.gz \
    -map "$ROOT/iso/isolinux.cfg" /isolinux/isolinux.cfg \
    -map "$ROOT/iso/grub.cfg" /boot/grub/grub.cfg \
    -map "$WORK/dahlia" /dahlia \
    -map "$WORK/md5sum.new" /md5sum.txt \
    -boot_image any replay

(cd "$BUILD" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")
echo "Wrote $OUT"
cat "$OUT.sha256"
