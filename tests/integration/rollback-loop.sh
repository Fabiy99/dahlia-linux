#!/bin/bash
# Integration test for /usr/lib/dahlia/rollback.sh on a Btrfs loop device
# laid out like a Debian install: top-level subvolume @rootfs with snapper's
# nested .snapshots/N/snapshot.
#
# Must run as root with btrfs-progs. SCRIPT may point at the script in the
# source tree.
set -euo pipefail

SCRIPT="${SCRIPT:-/usr/lib/dahlia/rollback.sh}"
WORK="$(mktemp -d /var/tmp/rollback-test.XXXXXX)"
LOOP=""

fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

cleanup() {
    for m in "$WORK"/root "$WORK"/top; do
        mountpoint -q "$m" && umount "$m"
    done
    [ -z "$LOOP" ] || losetup -d "$LOOP" || true
    rm -rf "$WORK"
}
trap cleanup EXIT

export DAHLIA_NO_SYSTEMCTL=1
export DAHLIA_SYSTEM_ROOT="$WORK/root"

step "Debian-like layout"
truncate -s 300M "$WORK/btrfs.img"
LOOP="$(losetup --find --show "$WORK/btrfs.img")"
mkfs.btrfs -q "$LOOP"
mkdir -p "$WORK/top" "$WORK/root"
mount "$LOOP" "$WORK/top"
btrfs -q subvolume create "$WORK/top/@rootfs"
r="$WORK/top/@rootfs"
mkdir -p "$r/etc/dahlia" "$r/var/lib/dahlia" "$r/var/log" "$r/home/admin" "$r/usr/bin"
echo "old" > "$r/usr/bin/tool"
echo "old" > "$r/etc/dahlia/firewall.json"
echo "old" > "$r/home/admin/notes"
btrfs -q subvolume create "$r/.snapshots"
mkdir "$r/.snapshots/1"
btrfs -q subvolume snapshot -r "$r" "$r/.snapshots/1/snapshot"
echo "<snapshot/>" > "$r/.snapshots/1/info.xml"
# Changes after the snapshot: a broken update and new Dahlia settings.
echo "new" > "$r/usr/bin/tool"
echo "added" > "$r/usr/bin/extra"
echo "new" > "$r/etc/dahlia/firewall.json"
echo "new" > "$r/home/admin/notes"
echo "log" > "$r/var/log/syslog"
umount "$WORK/top"
mount -o subvol=@rootfs "$LOOP" "$WORK/root"

step "Bad arguments"
for args in "" "0" "x" "1 --now" "--delete-old @rootfs" "--delete-old ../x"; do
    # shellcheck disable=SC2086
    if "$SCRIPT" $args 2>/dev/null; then fail "accepted: '$args'"; fi
done
if "$SCRIPT" 7 2>/dev/null; then fail "accepted a missing snapshot"; fi

step "Roll back to snapshot 1"
"$SCRIPT" 1
umount "$WORK/root"
mount -o subvol=@rootfs "$LOOP" "$WORK/root"
r="$WORK/root"
[ "$(cat "$r/usr/bin/tool")" = "old" ] || fail "system file not rolled back"
[ ! -e "$r/usr/bin/extra" ] || fail "file added after the snapshot is still there"
[ "$(cat "$r/etc/dahlia/firewall.json")" = "new" ] || fail "/etc/dahlia not kept"
[ "$(cat "$r/home/admin/notes")" = "new" ] || fail "/home not kept"
[ "$(cat "$r/var/log/syslog")" = "log" ] || fail "/var/log not kept"
[ -f "$r/.snapshots/1/info.xml" ] || fail "snapshots did not move to the new root"
[ -d "$r/.snapshots/1/snapshot/usr" ] || fail "snapshot 1 is gone"
grep -q '"snapshot": 1' "$r/var/lib/dahlia/rollback.json" || fail "no rollback.json"
btrfs subvolume show "$r" >/dev/null
old="$(sed -n 's/.*"old_root": "\([^"]*\)".*/\1/p' "$r/var/lib/dahlia/rollback.json")"
[[ "$old" == @rootfs.pre-rollback-* ]] || fail "old root name: $old"

step "Old root is kept and can be deleted"
mount -o subvolid=5 "$LOOP" "$WORK/top"
[ "$(cat "$WORK/top/$old/usr/bin/tool")" = "new" ] || fail "old root content"
[ ! -e "$WORK/top/@rootfs.rollback-new" ] || fail "temporary subvolume left over"
btrfs -q subvolume create "$WORK/top/$old/nested"
umount "$WORK/top"
"$SCRIPT" --delete-old "$old"
mount -o subvolid=5 "$LOOP" "$WORK/top"
[ ! -e "$WORK/top/$old" ] || fail "old root not deleted"
[ -d "$WORK/top/@rootfs" ] || fail "current root deleted"
umount "$WORK/top"

echo "rollback: all checks passed"
