#!/bin/sh
# Runs inside debian-installer (busybox sh) as partman/early_command.
# Chooses the installation target so the installer never silently wipes a
# data disk:
#   - exactly one candidate disk: use it without asking
#   - several candidates: raise the question priority so d-i asks
# The installation medium itself is never a candidate.
set -e

# Device the installer booted from, reduced to its whole-disk name
# (/dev/sdb1 -> /dev/sdb, /dev/nvme0n1p1 -> /dev/nvme0n1).
boot_part="$(grep ' /cdrom ' /proc/mounts | cut -d' ' -f1 || true)"
boot_disk=""
if [ -n "$boot_part" ]; then
    case "$boot_part" in
        /dev/nvme*|/dev/mmcblk*) boot_disk="$(echo "$boot_part" | sed 's/p[0-9]*$//')" ;;
        *) boot_disk="$(echo "$boot_part" | sed 's/[0-9]*$//')" ;;
    esac
fi

candidates=""
count=0
for dev in $(list-devices disk); do
    case "$dev" in
        /dev/sr*) continue ;;
    esac
    if [ -n "$boot_disk" ] && [ "$dev" = "$boot_disk" ]; then
        continue
    fi
    candidates="$candidates $dev"
    count=$((count + 1))
done

logger -t dahlia "install disk candidates ($count):$candidates"

if [ "$count" -eq 1 ]; then
    # shellcheck disable=SC2086 # single word by construction
    debconf-set partman-auto/disk $candidates
else
    # Zero or several disks: let the installer ask.
    debconf-set debconf/priority high
fi
