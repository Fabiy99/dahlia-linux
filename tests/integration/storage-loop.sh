#!/bin/bash
# Integration test for dahlia-storage against real loop devices.
#
# Creates disks with existing data (ext4, XFS, Btrfs) plus a parity disk,
# adds them with dahlia-disk and checks: data untouched, systemd units
# active, mergerfs pools grow and shrink at runtime, SnapRAID config.
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
    for unit in mnt-user.mount mnt-storage.mount; do systemctl stop "$unit" 2>/dev/null; done
    for m in /mnt/disk* /mnt/parity*; do
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
check_data d1 /mnt/user
check_data d2 /mnt/user

step "existing data never touched"
for name in d1 d2; do check_data "$name" /mnt/storage; done

echo
echo "OK: storage integration test passed"
