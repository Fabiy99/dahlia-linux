#!/bin/bash
# Integration test for dahlia-storage against real loop devices.
#
# Creates disks with existing data (ext4, XFS, Btrfs) plus a parity disk,
# adds them with dahlia-disk and checks: data untouched, systemd units
# active, mergerfs pools grow and shrink at runtime, SnapRAID config.
# Then a Btrfs RAID1 cache: new files land on it, dahlia-mover moves them
# to the data disks without changing the path; dahlia-snapraid syncs and
# stops above the delete threshold.
#
# Must run as root on a disposable machine (CI runner or test VM) with the
# dahlia-storage package installed.
set -euo pipefail

WORK="$(mktemp -d /var/tmp/dahlia-storage-test.XXXXXX)"
LOOPS=()
export DAHLIA_ALLOW_LOOP=1

fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

cleanup() {
    set +e
    exec 9<&- 2>/dev/null
    for unit in mnt-user.mount mnt-storage.mount mnt-cache.mount; do
        systemctl stop "$unit" 2>/dev/null
    done
    for m in /mnt/disk* /mnt/parity* /mnt/cache; do
        [ -d "$m" ] && umount "$m" 2>/dev/null
    done
    for l in "${LOOPS[@]}"; do losetup -d "$l" 2>/dev/null; done
    rm -rf "$WORK"
}
trap cleanup EXIT

# make_disk NAME FSTYPE SIZE_MB: image with a filesystem and a marker file.
make_disk() {
    local name="$1" fs="$2" size="$3" img="$WORK/$1.img" loop mnt="$WORK/seed-$1"
    truncate -s "${size}M" "$img"
    loop="$(losetup --find --show "$img")"
    LOOPS+=("$loop")
    case "$fs" in
        ext4) mkfs.ext4 -q -L "$name" "$loop" ;;
        xfs) mkfs.xfs -q -L "$name" "$loop" ;;
        btrfs) mkfs.btrfs -q -L "$name" "$loop" ;;
    esac
    mkdir -p "$mnt"
    mount "$loop" "$mnt"
    mkdir -p "$mnt/shares/media"
    head -c 1048576 /dev/urandom > "$mnt/shares/media/$name.bin"
    (cd "$mnt" && sha256sum "shares/media/$name.bin") > "$WORK/$name.sha256"
    umount "$mnt"
    # Let udev re-probe the device so /dev/disk/by-uuid/ exists, as it does
    # for real disks. systemd mount units bind to that device node.
    udevadm trigger --action=change "$loop"
    udevadm settle
    blkid -s UUID -o value "$loop" > "$WORK/$name.uuid"
    echo "$loop"
}

uuid_of() { cat "$WORK/$1.uuid"; }

check_data() {
    local name="$1" dir="$2"
    (cd "$dir" && sha256sum -c --quiet "$WORK/$name.sha256") \
        || fail "data of $name changed or missing in $dir"
}

branches() { getfattr --only-values -n user.mergerfs.branches "$1/.mergerfs"; }

step "create loop disks with existing data"
make_disk d1 ext4 300 >/dev/null
make_disk d2 xfs 400 >/dev/null
make_disk d3 btrfs 300 >/dev/null
make_disk p1 ext4 512 >/dev/null

# Small test disks: allow mergerfs to place files on them.
dahlia-disk set-minfreespace 10M

step "list shows the loop disks as candidates"
dahlia-disk list --json > "$WORK/list.json"
for name in d1 d2 d3 p1; do
    status="$(jq -r --arg u "$(uuid_of "$name")" '.[] | select(.uuid == $u) | .status' "$WORK/list.json")"
    [ "$status" = "candidate" ] || fail "$name has status '$status', expected candidate"
done

step "add first data disk: mounted, data intact, pools come up"
dahlia-disk add "$(uuid_of d1)" --role data
systemctl is-active --quiet mnt-disk1.mount || fail "mnt-disk1.mount not active"
check_data d1 /mnt/disk1
mountpoint -q /mnt/storage || fail "/mnt/storage not mounted"
mountpoint -q /mnt/user || fail "/mnt/user not mounted"
check_data d1 /mnt/storage

step "add more data disks: pools grow without remount"
pool_mount_id="$(findmnt -no ID /mnt/storage 2>/dev/null || stat -c %d /mnt/storage)"
dahlia-disk add "$(uuid_of d2)" --role data
dahlia-disk add "$(uuid_of d3)" --role data
[ "$(findmnt -no ID /mnt/storage 2>/dev/null || stat -c %d /mnt/storage)" = "$pool_mount_id" ] \
    || fail "/mnt/storage was remounted"
for name in d1 d2 d3; do check_data "$name" /mnt/storage; check_data "$name" /mnt/user; done
[ "$(branches /mnt/storage)" = "/mnt/disk1=RW:/mnt/disk2=RW:/mnt/disk3=RW" ] \
    || fail "unexpected branches: $(branches /mnt/storage)"

step "add parity disk: mounted, not part of any pool"
dahlia-disk add "$(uuid_of p1)" --role parity
mountpoint -q /mnt/parity1 || fail "/mnt/parity1 not mounted"
branches /mnt/storage | grep -q parity && fail "parity disk ended up in the pool"

step "new files land on a data disk"
head -c 65536 /dev/urandom > /mnt/user/shares/media/new.bin
ls /mnt/disk*/shares/media/new.bin >/dev/null 2>&1 || fail "new file not on any data disk"

step "a disk cannot be added twice"
if dahlia-disk add "$(uuid_of d1)" --role data 2>/dev/null; then fail "second add succeeded"; fi

step "unmounted mount points are immutable"
systemctl stop mnt-disk3.mount
if touch /mnt/disk3/should-fail 2>/dev/null; then fail "wrote into empty /mnt/disk3"; fi
systemctl start mnt-disk3.mount

step "snapraid config"
dahlia-disk snapraid-config > "$WORK/snapraid.conf"
grep -qx "parity /mnt/parity1/snapraid.parity" "$WORK/snapraid.conf" || fail "parity line missing"
for i in 1 2 3; do
    grep -qx "data d$i /mnt/disk$i/" "$WORK/snapraid.conf" || fail "data d$i missing"
done

step "add a Btrfs RAID1 cache: first branch of /mnt/user, not in /mnt/storage"
truncate -s 300M "$WORK/c1.img" "$WORK/c2.img"
c1="$(losetup --find --show "$WORK/c1.img")"; LOOPS+=("$c1")
c2="$(losetup --find --show "$WORK/c2.img")"; LOOPS+=("$c2")
mkfs.btrfs -q -L cache -d raid1 -m raid1 "$c1" "$c2"
udevadm trigger --action=change "$c1" "$c2"
udevadm settle
cache_uuid="$(blkid -s UUID -o value "$c1")"
user_mount_id="$(findmnt -no ID /mnt/user 2>/dev/null || stat -c %d /mnt/user)"
dahlia-disk add "$cache_uuid" --role cache
mountpoint -q /mnt/cache || fail "/mnt/cache not mounted"
[ -d /mnt/cache/shares ] || fail "/mnt/cache/shares missing"
[ "$(branches /mnt/user)" = "/mnt/cache/shares=RW:/mnt/disk1=RW:/mnt/disk2=RW:/mnt/disk3=RW" ] \
    || fail "unexpected /mnt/user branches: $(branches /mnt/user)"
[ "$(branches /mnt/storage)" = "/mnt/disk1=RW:/mnt/disk2=RW:/mnt/disk3=RW" ] \
    || fail "cache ended up in /mnt/storage: $(branches /mnt/storage)"
[ "$(findmnt -no ID /mnt/user 2>/dev/null || stat -c %d /mnt/user)" = "$user_mount_id" ] \
    || fail "/mnt/user was remounted"
[ "$(getfattr --only-values -n user.mergerfs.category.create /mnt/user/.mergerfs)" = "ff" ] \
    || fail "/mnt/user does not fill the cache first"
[ "$(getfattr --only-values -n user.mergerfs.cache.files /mnt/user/.mergerfs)" = "partial" ] \
    || fail "/mnt/user runs without page cache (no shared mmap)"

step "new files land on the cache, existing files stay visible"
head -c 262144 /dev/urandom > /mnt/user/shares/media/cached.bin
head -c 65536 /dev/urandom > /mnt/user/shares/media/open.bin
(cd /mnt/user && sha256sum shares/media/cached.bin) > "$WORK/cached.sha256"
[ -f /mnt/cache/shares/shares/media/cached.bin ] || fail "new file not on the cache"
for name in d1 d2 d3; do check_data "$name" /mnt/user; done

step "a cache with files cannot be removed by accident"
if dahlia-disk remove "$cache_uuid" 2>/dev/null; then fail "removed a cache that holds files"; fi

step "mover: moves to the data disks, path unchanged, open files stay"
exec 9< /mnt/user/shares/media/open.bin
dahlia-mover
[ -e /mnt/cache/shares/shares/media/cached.bin ] && fail "cached.bin still on the cache"
ls /mnt/disk*/shares/media/cached.bin >/dev/null 2>&1 || fail "cached.bin not on a data disk"
(cd /mnt/user && sha256sum -c --quiet "$WORK/cached.sha256") || fail "cached.bin changed by the move"
[ -f /mnt/cache/shares/shares/media/open.bin ] || fail "mover moved an open file"
exec 9<&-
dahlia-mover
[ -e /mnt/cache/shares/shares/media/open.bin ] && fail "open.bin not moved after close"
[ -d /mnt/cache/shares ] || fail "mover removed the shares directory"

step "remove the empty cache: /mnt/user keeps running on the data disks"
dahlia-disk remove "$cache_uuid"
mountpoint -q /mnt/cache && fail "/mnt/cache still mounted"
[ "$(branches /mnt/user)" = "/mnt/disk1=RW:/mnt/disk2=RW:/mnt/disk3=RW" ] \
    || fail "unexpected /mnt/user branches after cache removal: $(branches /mnt/user)"
(cd /mnt/user && sha256sum -c --quiet "$WORK/cached.sha256") || fail "cached.bin lost"

step "snapraid: first sync builds the parity"
# Keep a file on every data disk: SnapRAID itself refuses to sync a disk
# that became completely empty, which is a different safeguard.
for i in 1 2 3; do head -c 4096 /dev/urandom > "/mnt/disk$i/keep.bin"; done
dahlia-snapraid sync
[ -s /mnt/parity1/snapraid.parity ] || fail "no parity file after sync"
dahlia-snapraid sync | grep -q "parity is up to date" || fail "second sync not a no-op"

step "snapraid: sync stops above the delete threshold, --force overrides"
dahlia-disk set-option snapraid_delete_threshold 1
rm /mnt/disk1/shares/media/d1.bin
find /mnt/disk*/shares/media -name cached.bin -delete
if dahlia-snapraid sync; then fail "sync ran although 2 files were removed (threshold 1)"; fi
dahlia-snapraid sync --force
dahlia-snapraid sync | grep -q "parity is up to date" || fail "forced sync did not update parity"
dahlia-snapraid scrub

step "remove a data disk: leaves pool at runtime, data stays on the disk"
dahlia-disk remove "$(uuid_of d3)"
[ "$(branches /mnt/storage)" = "/mnt/disk1=RW:/mnt/disk2=RW" ] \
    || fail "unexpected branches after remove: $(branches /mnt/storage)"
[ -e /etc/systemd/system/mnt-disk3.mount ] && fail "unit for disk3 not removed"
mkdir -p "$WORK/check-d3"
mount "${LOOPS[2]}" "$WORK/check-d3"
check_data d3 "$WORK/check-d3"
umount "$WORK/check-d3"

step "apply rebuilds everything from storage.json"
systemctl stop mnt-user.mount mnt-storage.mount mnt-disk1.mount mnt-disk2.mount mnt-parity1.mount
dahlia-disk apply
for m in /mnt/disk1 /mnt/disk2 /mnt/parity1 /mnt/storage /mnt/user; do
    mountpoint -q "$m" || fail "$m not mounted after apply"
done
check_data d2 /mnt/user

step "format: only a blank whole disk, one GPT partition, then a candidate"
truncate -s 300M "$WORK/blank.img"
blank="$(losetup --find --show --partscan "$WORK/blank.img")"; LOOPS+=("$blank")
d1_loop="${LOOPS[0]}"
if dahlia-disk format "$d1_loop" --fs xfs 2>/dev/null; then fail "formatted a disk with data"; fi
if dahlia-disk format "$blank" --fs xfs --label "much-too-long-label" 2>/dev/null; then
    fail "accepted a label XFS cannot store"
fi
dahlia-disk format "$blank" --fs xfs --label newdisk --json > "$WORK/format.json"
new_uuid="$(jq -r .uuid "$WORK/format.json")"
[ "$(jq -r .partition "$WORK/format.json")" = "${blank}p1" ] || fail "unexpected partition"
[ "$(blkid -p -s TYPE -o value "${blank}p1")" = "xfs" ] || fail "no XFS on ${blank}p1"
[ "$(blkid -p -s PTTYPE -o value "$blank")" = "gpt" ] || fail "no GPT on $blank"
status="$(dahlia-disk list --json | jq -r --arg u "$new_uuid" '.[] | select(.uuid == $u) | .status')"
[ "$status" = "candidate" ] || fail "formatted disk has status '$status', expected candidate"
if dahlia-disk format "$blank" --fs ext4 2>/dev/null; then fail "formatted the same disk twice"; fi
if dahlia-disk format "${blank}p1" --fs ext4 2>/dev/null; then fail "formatted a partition"; fi
check_data d1 /mnt/disk1

step "existing data never touched"
check_data d2 /mnt/storage

echo
echo "OK: storage integration test passed"
