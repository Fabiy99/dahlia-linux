#!/bin/bash
# Scheduled SMART self-test (smart-test@short|long.service): start
# the test on every disk. The disks run it themselves; this returns at once.
# Disks in a disk test (burnin@) are skipped.
set -euo pipefail

kind="$1"
case "$kind" in
    short|long) ;;
    *) echo "smart-test: kind must be short or long" >&2; exit 2 ;;
esac
while read -r name type; do
    [ "$type" = disk ] || continue
    case "$name" in zram*|loop*|ram*|sr*) continue ;; esac
    if systemctl is-active --quiet "burnin@$name.service"; then
        echo "smart-test: /dev/$name skipped, disk test running"
        continue
    fi
    # Exit bits 0-2: command line, open, SMART command failed; disks without
    # SMART (virtual disks, some USB bridges) end up here.
    rc=0
    smartctl -t "$kind" "/dev/$name" > /dev/null || rc=$?
    if [ $((rc & 7)) -ne 0 ]; then
        echo "smart-test: /dev/$name: no SMART self-test (smartctl exit $rc)"
    else
        echo "smart-test: /dev/$name: $kind self-test started"
    fi
done < <(lsblk -dn -o NAME,TYPE)
