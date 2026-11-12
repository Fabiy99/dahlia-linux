#!/bin/bash
# Roll the root filesystem back to a snapper snapshot.
#
#   rollback.sh SNAPSHOT [--reboot]     make snapshot SNAPSHOT the new root
#   rollback.sh --delete-old NAME       delete a root kept by an earlier rollback
#
# Debian installs the root into a top-level Btrfs subvolume (@rootfs) and
# mounts it by name (fstab, GRUB rootflags), so `snapper rollback`, which
# changes the default subvolume, has no effect. Instead, as snapper-rollback
# does for the @ layout: a writable snapshot of the chosen snapshot is
# created next to the root, the running root is renamed to
# <subvol>.pre-rollback-<time> and the new one takes its name. The snapper
# snapshots (nested subvolume .snapshots) move over to the new root. The
# next boot starts the old state.
#
# Kept from the running system, not taken back: /home, /root, /var/log,
# /etc/dahlia, /var/lib/dahlia, /var/lib/dashboard and
# /var/lib/docker. App data on /srv (@srv) and the array are separate
# anyway. Copies use reflinks, so they cost no space.
#
# Environment (tests): DAHLIA_SYSTEM_ROOT (mount point of the root fs),
# DAHLIA_NO_SYSTEMCTL=1.
set -euo pipefail

KEEP=(home root var/log etc/dahlia var/lib/dahlia var/lib/dashboard var/lib/docker)
OLD_RE='^[A-Za-z0-9@._-]+\.pre-rollback-[0-9]{8}-[0-9]{6}$'

root="${DAHLIA_SYSTEM_ROOT:-/}"

die() {
    echo "rollback: $*" >&2
    exit 1
}

usage() {
    die "usage: rollback.sh SNAPSHOT [--reboot] | rollback.sh --delete-old NAME"
}

[ "$(findmnt -no FSTYPE --mountpoint "$root")" = "btrfs" ] || die "root is not Btrfs"
# SOURCE looks like /dev/sda2[/@rootfs].
src="$(findmnt -no SOURCE --mountpoint "$root")"
dev="${src%%\[*}"
[ "$src" != "$dev" ] || die "root is not mounted from a subvolume"
sub="${src#*\[}"
sub="${sub%\]}"
sub="${sub#/}"
[[ "$sub" =~ ^[A-Za-z0-9@._-]+$ ]] || die "root subvolume $sub is not a top-level subvolume"

top="$(mktemp -d /run/rollback.XXXXXX)"
new=""
cleanup() {
    if [ -n "$new" ] && [ -d "$new" ]; then
        btrfs -q subvolume delete "$new" || true
    fi
    mountpoint -q "$top" && umount "$top"
    rmdir "$top"
}
trap cleanup EXIT
mount -t btrfs -o subvolid=5 "$dev" "$top"

if [ "${1:-}" = "--delete-old" ]; then
    name="${2:-}"
    [[ "$name" =~ $OLD_RE ]] || die "not a kept root: $name"
    [ -d "$top/$name" ] || die "$name does not exist"
    # Nested subvolumes first (Docker's btrfs driver makes some), deepest first.
    btrfs subvolume list -o "$top/$name" | sed -n 's/^.* path //p' \
        | awk '{ print length($0) "\t" $0 }' | sort -rn | cut -f2- \
        | while read -r path; do
            btrfs -q subvolume delete "$top/${path#<FS_TREE>/}"
        done
    btrfs -q subvolume delete "$top/$name"
    echo "rollback: deleted $name"
    exit 0
fi

n="${1:-}"
[[ "$n" =~ ^[1-9][0-9]{0,5}$ ]] || usage
case "${2:-}" in
    "" | --reboot) ;;
    *) usage ;;
esac
snap="$top/$sub/.snapshots/$n/snapshot"
[ -d "$snap" ] || die "snapshot $n not found"

if [ -z "${DAHLIA_NO_SYSTEMCTL:-}" ]; then
    # Nothing should write into the directories that are carried over.
    systemctl stop apps.service docker.socket docker.service || true
fi

stamp="$(date +%Y%m%d-%H%M%S)"
old="$sub.pre-rollback-$stamp"
[ ! -e "$top/$old" ] || die "$old exists already"
new="$top/$sub.rollback-new"
[ ! -e "$new" ] || btrfs -q subvolume delete "$new"
btrfs -q subvolume snapshot "$snap" "$new"

for d in "${KEEP[@]}"; do
    [ -e "$top/$sub/$d" ] || continue
    rm -rf "${new:?}/$d"
    mkdir -p "$(dirname "$new/$d")"
    cp -a --reflink=auto "$top/$sub/$d" "$new/$d"
done
mkdir -p "$new/var/lib/dahlia"
printf '{"snapshot": %s, "old_root": "%s", "time": "%s"}\n' "$n" "$old" "$(date -Iseconds)" \
    > "$new/var/lib/dahlia/rollback.json"

# Swap. The snapshot leaves an empty directory where .snapshots was.
rmdir "$new/.snapshots" 2>/dev/null || true
mv "$top/$sub" "$top/$old"
mv "$new" "$top/$sub"
new=""
mv "$top/$old/.snapshots" "$top/$sub/.snapshots"
sync -f "$top/$sub"
echo "rollback: $sub is now snapshot $n, the previous root is kept as $old"

if [ "${2:-}" = "--reboot" ] && [ -z "${DAHLIA_NO_SYSTEMCTL:-}" ]; then
    systemctl reboot
fi
