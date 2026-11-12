#!/bin/bash
# Disk test before use (burnin@<name>.service): SMART values, then
# badblocks writes and reads back four patterns over the whole disk, then
# SMART values again. Erases the disk. agent only starts it for
# blank disks; the results are read by disks.burnin_status.
set -euo pipefail

name="$1"
case "$name" in
    sd[a-z]|sd[a-z][a-z]|vd[a-z]|vd[a-z][a-z]|nvme[0-9]n[0-9]|nvme[0-9][0-9]n[0-9]|nvme[0-9]n[0-9][0-9]|nvme[0-9][0-9]n[0-9][0-9]) ;;
    *) echo "disk-burnin: invalid disk name '$name'" >&2; exit 2 ;;
esac
dev="/dev/$name"
dir=/var/lib/dahlia/burnin
mkdir -p "$dir"
chmod 0755 "$dir"
rm -f "$dir/$name.done" "$dir/$name.bad" "$dir/$name.smart-after.json"
: > "$dir/$name.log"

smartctl -j -a "$dev" > "$dir/$name.smart-before.json" || true

# badblocks counts blocks in 32 bits: larger disks need larger blocks.
size=$(blockdev --getsize64 "$dev")
bs=4096
while [ $((size / bs)) -ge 4294967295 ]; do
    bs=$((bs * 2))
done
echo "disk-burnin: $dev, $size bytes, block size $bs" >&2
badblocks -b "$bs" -c 256 -w -s -o "$dir/$name.bad" "$dev" 2>> "$dir/$name.log"

smartctl -j -a "$dev" > "$dir/$name.smart-after.json" || true
touch "$dir/$name.done"
echo "disk-burnin: $dev done, $(wc -l < "$dir/$name.bad") bad blocks" >&2
