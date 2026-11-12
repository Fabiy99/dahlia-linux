#!/bin/bash
# Integration test for /usr/lib/dahlia/system-data.sh against a Btrfs and an
# ext4 loop device standing in for the root filesystem.
#
# Must run as root with btrfs-progs and e2fsprogs. SCRIPT may point at the
# script in the source tree.
set -euo pipefail

SCRIPT="${SCRIPT:-/usr/lib/dahlia/system-data.sh}"
WORK="$(mktemp -d /var/tmp/sysdata-test.XXXXXX)"
LOOPS=()

fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

cleanup() {
    for m in "$WORK"/check "$WORK"/root/srv "$WORK"/root "$WORK"/ext; do
        mountpoint -q "$m" && umount "$m"
    done
    for l in "${LOOPS[@]}"; do losetup -d "$l" || true; done
    rm -rf "$WORK"
}
trap cleanup EXIT

loop() {
    truncate -s "$2" "$WORK/$1.img"
    local dev
    dev="$(losetup --find --show "$WORK/$1.img")"
    LOOPS+=("$dev")
    echo "$dev"
}

export DAHLIA_NO_SYSTEMCTL=1

step "Btrfs root: subvolume @srv and a mount unit"
BTRFS="$(loop btrfs 300M)"
mkfs.btrfs -q "$BTRFS"
mkdir -p "$WORK/top" "$WORK/root" "$WORK/units" "$WORK/check"
mount "$BTRFS" "$WORK/top"
btrfs -q subvolume create "$WORK/top/@rootfs"
umount "$WORK/top"
mount -o subvol=@rootfs "$BTRFS" "$WORK/root"
DAHLIA_SYSTEM_ROOT="$WORK/root" DAHLIA_SYSTEM_DATA="$WORK/root/srv" \
    DAHLIA_UNIT_DIR="$WORK/units" "$SCRIPT"
unit="$(ls "$WORK/units")"
[ "$(echo "$unit" | wc -l)" = 1 ] || fail "expected one unit, got: $unit"
case "$unit" in *srv.mount) ;; *) fail "unit name $unit" ;; esac
uuid="$(blkid -s UUID -o value "$BTRFS")"
grep -qx "What=/dev/disk/by-uuid/$uuid" "$WORK/units/$unit" || fail "What= wrong"
grep -qx "Where=$WORK/root/srv" "$WORK/units/$unit" || fail "Where= wrong"
grep -qx "Options=subvol=@srv,noatime,nofail" "$WORK/units/$unit" || fail "Options= wrong"
mount -o subvolid=5 "$BTRFS" "$WORK/check"
btrfs subvolume show "$WORK/check/@srv" >/dev/null || fail "@srv missing"
umount "$WORK/check"
[ -d "$WORK/root/srv" ] || fail "mount point missing"

step "Btrfs: the subvolume is outside snapshots of the root"
mount -o subvol=@srv "$BTRFS" "$WORK/root/srv"
echo keep > "$WORK/root/srv/data"
btrfs -q subvolume snapshot "$WORK/root" "$WORK/root/snap"
[ ! -e "$WORK/root/snap/srv/data" ] || fail "app data ended up in a root snapshot"
btrfs -q subvolume delete "$WORK/root/snap"

step "Btrfs: a second run changes nothing"
before="$(sha256sum "$WORK/units/$unit")"
DAHLIA_SYSTEM_ROOT="$WORK/root" DAHLIA_SYSTEM_DATA="$WORK/root/srv" \
    DAHLIA_UNIT_DIR="$WORK/units" "$SCRIPT"
[ "$(sha256sum "$WORK/units/$unit")" = "$before" ] || fail "unit rewritten"
[ "$(cat "$WORK/root/srv/data")" = keep ] || fail "data changed"
umount "$WORK/root/srv"
umount "$WORK/root"

step "ext4 root: plain directory, no unit"
EXT="$(loop ext4 64M)"
mkfs.ext4 -q "$EXT"
mkdir -p "$WORK/ext" "$WORK/units-ext"
mount "$EXT" "$WORK/ext"
DAHLIA_SYSTEM_ROOT="$WORK/ext" DAHLIA_SYSTEM_DATA="$WORK/ext/srv" \
    DAHLIA_UNIT_DIR="$WORK/units-ext" "$SCRIPT"
[ -d "$WORK/ext/srv" ] || fail "directory missing"
[ -z "$(ls -A "$WORK/units-ext")" ] || fail "unit written on ext4"

echo
echo "system-data: all checks passed"
