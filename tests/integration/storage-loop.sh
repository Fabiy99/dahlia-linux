#!/bin/bash
# Integration test for storage against real loop devices.
#
# Creates disks with existing data (ext4, XFS, Btrfs) plus a parity disk,
# adds them with disk and checks: data untouched, systemd units
# active, mergerfs pools grow and shrink at runtime, SnapRAID config.
# Then a Btrfs RAID1 cache: new files land on it, mover moves them
# to the data disks without changing the path; dahlia-snapraid syncs and
# stops above the delete threshold, scrub finds damaged blocks, fix repairs
# them and rebuilds a replaced data disk from the parity.
#
# Must run as root on a disposable machine (CI runner or test VM) with the
# storage package installed.
set -euo pipefail

WORK="$(mktemp -d /var/tmp/storage-test.XXXXXX)"
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
disk set-minfreespace 10M

step "list shows the loop disks as candidates"
disk list --json > "$WORK/list.json"
for name in d1 d2 d3 p1; do
    status="$(jq -r --arg u "$(uuid_of "$name")" '.[] | select(.uuid == $u) | .status' "$WORK/list.json")"
    [ "$status" = "candidate" ] || fail "$name has status '$status', expected candidate"
done

step "add first data disk: mounted, data intact, pools come up"
disk add "$(uuid_of d1)" --role data
systemctl is-active --quiet mnt-disk1.mount || fail "mnt-disk1.mount not active"
check_data d1 /mnt/disk1
mountpoint -q /mnt/storage || fail "/mnt/storage not mounted"
mountpoint -q /mnt/user || fail "/mnt/user not mounted"
check_data d1 /mnt/storage

step "add more data disks: pools grow without remount"
pool_mount_id="$(findmnt -no ID /mnt/storage 2>/dev/null || stat -c %d /mnt/storage)"
disk add "$(uuid_of d2)" --role data
disk add "$(uuid_of d3)" --role data
[ "$(findmnt -no ID /mnt/storage 2>/dev/null || stat -c %d /mnt/storage)" = "$pool_mount_id" ] \
    || fail "/mnt/storage was remounted"
for name in d1 d2 d3; do check_data "$name" /mnt/storage; check_data "$name" /mnt/user; done
[ "$(branches /mnt/storage)" = "/mnt/disk1=RW:/mnt/disk2=RW:/mnt/disk3=RW" ] \
    || fail "unexpected branches: $(branches /mnt/storage)"

step "add parity disk: mounted, not part of any pool"
disk add "$(uuid_of p1)" --role parity
mountpoint -q /mnt/parity1 || fail "/mnt/parity1 not mounted"
branches /mnt/storage | grep -q parity && fail "parity disk ended up in the pool"

step "new files land on a data disk"
head -c 65536 /dev/urandom > /mnt/user/shares/media/new.bin
ls /mnt/disk*/shares/media/new.bin >/dev/null 2>&1 || fail "new file not on any data disk"

step "a disk cannot be added twice"
if disk add "$(uuid_of d1)" --role data 2>/dev/null; then fail "second add succeeded"; fi

step "unmounted mount points are immutable"
systemctl stop mnt-disk3.mount
if touch /mnt/disk3/should-fail 2>/dev/null; then fail "wrote into empty /mnt/disk3"; fi
systemctl start mnt-disk3.mount

step "snapraid config"
disk snapraid-config > "$WORK/snapraid.conf"
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
disk add "$cache_uuid" --role cache
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
if disk remove "$cache_uuid" 2>/dev/null; then fail "removed a cache that holds files"; fi

step "mover: moves to the data disks, path unchanged, open files stay"
exec 9< /mnt/user/shares/media/open.bin
mover
[ -e /mnt/cache/shares/shares/media/cached.bin ] && fail "cached.bin still on the cache"
ls /mnt/disk*/shares/media/cached.bin >/dev/null 2>&1 || fail "cached.bin not on a data disk"
(cd /mnt/user && sha256sum -c --quiet "$WORK/cached.sha256") || fail "cached.bin changed by the move"
[ -f /mnt/cache/shares/shares/media/open.bin ] || fail "mover moved an open file"
exec 9<&-
mover
[ -e /mnt/cache/shares/shares/media/open.bin ] && fail "open.bin not moved after close"
[ -d /mnt/cache/shares ] || fail "mover removed the shares directory"

step "remove the empty cache: /mnt/user keeps running on the data disks"
disk remove "$cache_uuid"
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
disk set-option snapraid_delete_threshold 1
rm /mnt/disk1/shares/media/d1.bin
find /mnt/disk*/shares/media -name cached.bin -delete
if dahlia-snapraid sync; then fail "sync ran although 2 files were removed (threshold 1)"; fi
dahlia-snapraid sync --force
dahlia-snapraid sync | grep -q "parity is up to date" || fail "forced sync did not update parity"
dahlia-snapraid scrub

step "parity status: files covered, last runs recorded"
dahlia-snapraid status --json > "$WORK/parity.json"
jq -e '.configured and .status.bad == 0 and .status.files > 0' "$WORK/parity.json" >/dev/null \
    || fail "unexpected parity status: $(cat "$WORK/parity.json")"
jq -e '.runs.sync.result == "no_changes" and .runs.scrub.result == "ok"' "$WORK/parity.json" \
    >/dev/null || fail "runs not recorded: $(jq -c .runs "$WORK/parity.json")"

step "scrub finds a silently damaged file, fix --bad repairs it"
disk set-option scrub_percent 100
disk set-option scrub_older_than_days 0
damaged=/mnt/disk2/shares/media/d2.bin
stamp="$(stat -c %y "$damaged")"
printf 'broken' | dd of="$damaged" bs=1 seek=4096 conv=notrunc status=none
touch -d "$stamp" "$damaged"
if dahlia-snapraid scrub; then fail "scrub did not report the damaged block"; fi
dahlia-snapraid status --json | jq -e '.status.bad > 0' >/dev/null || fail "status shows no bad blocks"
dahlia-snapraid fix --bad
check_data d2 /mnt/disk2
dahlia-snapraid status --json | jq -e '.status.bad == 0' >/dev/null || fail "bad blocks left"

step "replace a failed data disk: new disk gets the same number, fix rebuilds it"
disk remove "$(uuid_of d2)"
truncate -s 400M "$WORK/new2.img"
new2="$(losetup --find --show "$WORK/new2.img")"; LOOPS+=("$new2")
mkfs.ext4 -q -L new2 "$new2"
udevadm trigger --action=change "$new2"; udevadm settle
disk add "$(blkid -s UUID -o value "$new2")" --role data
mountpoint -q /mnt/disk2 || fail "replacement is not /mnt/disk2"
dahlia-snapraid fix --disk 2
check_data d2 /mnt/disk2
check_data d2 /mnt/user
jq -e '.fix.result == "ok" and .fix.what == "d2" and .fix.unrecoverable == 0' \
    /var/lib/dahlia/snapraid.json >/dev/null || fail "fix not recorded"
dahlia-snapraid sync | grep -q "parity is up to date" || fail "sync after fix found changes"

step "remove a data disk: leaves pool at runtime, data stays on the disk"
disk remove "$(uuid_of d3)"
[ "$(branches /mnt/storage)" = "/mnt/disk1=RW:/mnt/disk2=RW" ] \
    || fail "unexpected branches after remove: $(branches /mnt/storage)"
[ -e /etc/systemd/system/mnt-disk3.mount ] && fail "unit for disk3 not removed"
mkdir -p "$WORK/check-d3"
mount "${LOOPS[2]}" "$WORK/check-d3"
check_data d3 "$WORK/check-d3"
umount "$WORK/check-d3"

step "apply rebuilds everything from storage.json"
systemctl stop mnt-user.mount mnt-storage.mount mnt-disk1.mount mnt-disk2.mount mnt-parity1.mount
disk apply
for m in /mnt/disk1 /mnt/disk2 /mnt/parity1 /mnt/storage /mnt/user; do
    mountpoint -q "$m" || fail "$m not mounted after apply"
done
check_data d2 /mnt/user

step "format: only a blank whole disk, one GPT partition, then a candidate"
# mkfs.xfs needs more than 300 MB.
truncate -s 512M "$WORK/blank.img"
blank="$(losetup --find --show --partscan "$WORK/blank.img")"; LOOPS+=("$blank")
d1_loop="${LOOPS[0]}"
if disk format "$d1_loop" --fs xfs 2>/dev/null; then fail "formatted a disk with data"; fi
if disk format "$blank" --fs xfs --label "much-too-long-label" 2>/dev/null; then
    fail "accepted a label XFS cannot store"
fi
disk format "$blank" --fs xfs --label newdisk --json > "$WORK/format.json"
new_uuid="$(jq -r .uuid "$WORK/format.json")"
[ "$(jq -r .partition "$WORK/format.json")" = "${blank}p1" ] || fail "unexpected partition"
[ "$(blkid -p -s TYPE -o value "${blank}p1")" = "xfs" ] || fail "no XFS on ${blank}p1"
[ "$(blkid -p -s PTTYPE -o value "$blank")" = "gpt" ] || fail "no GPT on $blank"
status="$(disk list --json | jq -r --arg u "$new_uuid" '.[] | select(.uuid == $u) | .status')"
[ "$status" = "candidate" ] || fail "formatted disk has status '$status', expected candidate"
if disk format "$blank" --fs ext4 2>/dev/null; then fail "formatted the same disk twice"; fi
if disk format "${blank}p1" --fs ext4 2>/dev/null; then fail "formatted a partition"; fi
check_data d2 /mnt/storage

step "import: copy a disk with data to /mnt/user, verify, then format it"
truncate -s 512M "$WORK/imp.img"
imp="$(losetup --find --show --partscan "$WORK/imp.img")"; LOOPS+=("$imp")
mkfs.ext4 -q -L imp "$imp"
mkdir -p "$WORK/seed-imp"; mount "$imp" "$WORK/seed-imp"
mkdir -p "$WORK/seed-imp/photos/2024"
head -c 2097152 /dev/urandom > "$WORK/seed-imp/photos/2024/a.jpg"
echo "hello" > "$WORK/seed-imp/notes.txt"
(cd "$WORK/seed-imp" && sha256sum photos/2024/a.jpg notes.txt) > "$WORK/imp.sha256"
umount "$WORK/seed-imp"
udevadm trigger --action=change "$imp"; udevadm settle
imp_uuid="$(blkid -s UUID -o value "$imp")"
if disk format "$imp" --fs xfs 2>/dev/null; then fail "formatted a disk with data before import"; fi
if disk import "$imp_uuid" --to ../etc 2>/dev/null; then fail "accepted a bad target"; fi
disk import "$imp_uuid" --to import-test --check
disk import "$imp_uuid" --to import-test
check_data imp /mnt/user/import-test
[ "$(stat -c %G /mnt/user/import-test/notes.txt)" = "dahlia" ] || fail "imported files not in group dahlia"
[ "$(jq -r .phase /run/dahlia/import.json)" = "done" ] || fail "import status is not done"
imported="$(disk list --json | jq -r --arg u "$imp_uuid" '.[] | select(.uuid == $u) | .imported')"
[ "$imported" = "true" ] || fail "list does not mark the disk as imported"
findmnt -S "$imp" >/dev/null && fail "source still mounted after import"
if disk import "$imp_uuid" --to import-test 2>/dev/null; then fail "imported into an existing target"; fi
disk format "$imp" --fs ext4 --json > "$WORK/format-imp.json"
[ "$(blkid -p -s TYPE -o value "${imp}p1")" = "ext4" ] || fail "imported disk not formatted"
check_data imp /mnt/user/import-test

step "encryption: LUKS format, add, lock, unlock with passphrase and key file"
truncate -s 512M "$WORK/enc.img"
enc="$(losetup --find --show --partscan "$WORK/enc.img")"; LOOPS+=("$enc")
PASS="Dahlia Test-Passwort 1"
if printf 'kurz\n' | disk format "$enc" --fs xfs --encrypt 2>/dev/null; then
    fail "accepted a short passphrase"
fi
printf '%s\n' "$PASS" | disk format "$enc" --fs xfs --label enc --encrypt --json > "$WORK/format-enc.json"
enc_uuid="$(jq -r .uuid "$WORK/format-enc.json")"
enc_luks="$(jq -r .luks "$WORK/format-enc.json")"
[ "$(blkid -p -s TYPE -o value "${enc}p1")" = "crypto_LUKS" ] || fail "no LUKS on ${enc}p1"
[ -e "/dev/mapper/luks-$enc_luks" ] || fail "new container not open"
status="$(disk list --json | jq -r --arg u "$enc_uuid" '.[] | select(.uuid == $u) | .status')"
[ "$status" = "candidate" ] || fail "open encrypted disk has status '$status', expected candidate"
disk add "$enc_uuid" --role data
enc_idx="$(jq -r --arg u "$enc_uuid" '.disks[] | select(.uuid == $u) | .index' /etc/dahlia/storage.json)"
enc_mnt="/mnt/disk$enc_idx"
enc_unit="$(systemd-escape --path "$enc_mnt").mount"
[ "$(jq -r --arg u "$enc_uuid" '.disks[] | select(.uuid == $u) | .luks' /etc/dahlia/storage.json)" = "$enc_luks" ] \
    || fail "storage.json does not record the LUKS UUID"
grep -qx "ConditionPathExists=/dev/mapper/luks-$enc_luks" "/etc/systemd/system/$enc_unit" \
    || fail "mount unit does not wait for the unlocked container"
mountpoint -q "$enc_mnt" || fail "encrypted disk not mounted"
echo "geheim" > "$enc_mnt/enc-marker"

# As after a reboot without key file: locked, apply does not count it as missing.
systemctl stop "$enc_unit"; cryptsetup close "luks-$enc_luks"
disk apply > /dev/null || fail "apply failed with a locked disk"
mountpoint -q "$enc_mnt" && fail "locked disk got mounted"
[ "$(disk crypt-status | jq -r '.disks[0].open')" = "false" ] || fail "crypt-status says open"
if printf 'falsches Passwort\n' | disk unlock 2>/dev/null; then fail "unlocked with a wrong passphrase"; fi
printf '%s\n' "$PASS" | disk unlock --json | jq -e '.opened | length == 1' > /dev/null \
    || fail "unlock did not open the disk"
mountpoint -q "$enc_mnt" || fail "unlocked disk not mounted"
[ "$(cat /mnt/storage/enc-marker)" = "geheim" ] || fail "unlocked disk not back in /mnt/storage"

# A second encrypted disk must use the same passphrase.
truncate -s 512M "$WORK/enc2.img"
enc2="$(losetup --find --show --partscan "$WORK/enc2.img")"; LOOPS+=("$enc2")
if printf 'anderes Passwort\n' | disk format "$enc2" --fs xfs --encrypt 2>/dev/null; then
    fail "second encrypted disk with another passphrase"
fi

# Key file on the system SSD: /etc/crypttab, opened by systemd-cryptsetup as at boot.
printf '%s\n' "$PASS" | disk auto-unlock on
[ "$(stat -c %a /etc/dahlia/luks/array.key)" = "400" ] || fail "key file missing or readable"
grep -qx "luks-$enc_luks UUID=$enc_luks /etc/dahlia/luks/array.key luks,nofail" /etc/crypttab \
    || fail "no crypttab entry"
systemctl stop "$enc_unit"; cryptsetup close "luks-$enc_luks"
systemctl daemon-reload
systemctl start "systemd-cryptsetup@$(systemd-escape "luks-$enc_luks").service"
systemctl start "$enc_unit"
mountpoint -q "$enc_mnt" || fail "crypttab entry did not unlock the disk"

# New passphrase; the key file keeps working, the old passphrase does not.
printf '%s\n%s\n' "$PASS" "Neues Passwort 2" | disk passphrase
printf '%s' "$PASS" | cryptsetup open --test-passphrase --key-file=- "${enc}p1" 2>/dev/null \
    && fail "old passphrase still works"
printf '%s' "Neues Passwort 2" | cryptsetup open --test-passphrase --key-file=- "${enc}p1" \
    || fail "new passphrase does not work"
disk auto-unlock off
[ -e /etc/dahlia/luks/array.key ] && fail "key file still there"
grep -q "luks-$enc_luks" /etc/crypttab && fail "crypttab entry still there"
cryptsetup open --test-passphrase --key-file /dev/null "${enc}p1" 2>/dev/null && fail "empty key accepted"
[ "$(cryptsetup luksDump "${enc}p1" | grep -c '^  [0-9]*: luks2')" = "1" ] || fail "key file slot left behind"

disk remove "$enc_uuid"
[ -e "/dev/mapper/luks-$enc_luks" ] && fail "container still open after remove"

step "existing data never touched"
check_data d2 /mnt/storage

echo
echo "OK: storage integration test passed"
