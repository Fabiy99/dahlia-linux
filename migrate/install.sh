#!/bin/bash
# Install Dahlia Linux onto an empty disk from inside a running system (the
# Unraid migration helper's container), without the installer ISO.
#
# Usage: install.sh <settings-file>
# The settings file is written by the helper (helper/job.py) with values it
# has validated; every value is checked here again before the disk is touched:
#   DISK=/dev/sdX  SIZE=<bytes>  SERIAL=<serial or empty>
#   HOSTNAME_NEW=nas  ACCOUNT=fabian  PASSWORD_FILE=/path  SETUP_CODE=ABCDE-FGHJK
#   TIMEZONE=Europe/Berlin  KEYMAP=de
#   NET_NAME=enp3s0  NET_MAC=aa:bb:cc:dd:ee:ff  NET_MODE=dhcp|static
#   NET_ADDRESS=192.168.1.10/24  NET_GATEWAY=192.168.1.1  NET_DNS="192.168.1.1 ..."
#   UNRAID_DIR=/path (copied to /var/lib/dahlia/unraid)  MIRROR=  DAHLIA_REPO=
#   KEYRING=/path/to/dahlia-archive-keyring.asc
#
# Progress goes to stdout as "@@step <name>" lines; the helper shows them.
# Layout matches the ISO install: GPT with BIOS boot, EFI system partition
# and a Btrfs root in the top-level subvolume @rootfs (snapper rollback).
set -euo pipefail

SETTINGS="${1:?settings file}"
# shellcheck source=/dev/null # written and validated by the helper
. "$SETTINGS"

MIRROR="${MIRROR:-http://deb.debian.org/debian}"
DAHLIA_REPO="${DAHLIA_REPO:-https://fabiy99.github.io/dahlia-linux/}"
SUITE=trixie
TARGET=/mnt/dahlia-target
PACKAGES=(base agent dashboard storage archive-keyring)
APT_TIMEOUT=1800

step() { echo "@@step $1"; }
die() { echo "@@error $*"; exit 1; }

# --- check -------------------------------------------------------------------
step check
[[ "$DISK" =~ ^/dev/(sd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+|loop[0-9]+)$ ]] || die "bad disk $DISK"
[ -b "$DISK" ] || die "$DISK is not a block device"
[ "$(lsblk -dnbo SIZE "$DISK" | tr -d ' ')" = "$SIZE" ] || die "size of $DISK changed"
if [ -n "${SERIAL:-}" ]; then
    [ "$(lsblk -dno SERIAL "$DISK" | tr -d ' ')" = "$SERIAL" ] || die "serial of $DISK changed"
fi
[[ "$HOSTNAME_NEW" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || die "bad host name"
[[ "$ACCOUNT" =~ ^[a-z][a-z0-9_-]{1,31}$ ]] || die "bad account name"
[[ "$SETUP_CODE" =~ ^[2-9A-HJKMNP-Z]{5}-[2-9A-HJKMNP-Z]{5}$ ]] || die "bad setup code"
[[ "$TIMEZONE" =~ ^[A-Za-z]+(/[A-Za-z0-9_+-]+){1,2}$ ]] || die "bad time zone"
[[ "$KEYMAP" =~ ^[a-z]{2,8}$ ]] || die "bad keymap"
[[ "$NET_NAME" =~ ^[a-z0-9]{2,15}$ ]] || die "bad interface name"
[[ "$NET_MAC" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]] || die "bad MAC address"
[ -s "$PASSWORD_FILE" ] || die "no password"
[ -s "$KEYRING" ] || die "no archive keyring"
# Nothing of the disk may be mounted.
if lsblk -nro MOUNTPOINTS "$DISK" | grep -q .; then
    die "$DISK has mounted partitions"
fi

part() { case "$DISK" in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }

cleanup() {
    for m in dev/pts dev proc sys run boot/efi ""; do
        if mountpoint -q "$TARGET/$m" 2>/dev/null; then
            umount -l "$TARGET/$m" || true
        fi
    done
}
trap cleanup EXIT

# --- partition and format ------------------------------------------------------
step partition
wipefs -aq "$DISK"
sgdisk -Z "$DISK" >/dev/null
sgdisk -n1:1M:+1M -t1:EF02 -c1:bios \
       -n2:0:+512M -t2:EF00 -c2:efi \
       -n3:0:0 -t3:8300 -c3:dahlia "$DISK" >/dev/null
partprobe "$DISK" 2>/dev/null || true
udevadm settle 2>/dev/null || true
for _ in $(seq 1 20); do [ -b "$(part 3)" ] && break; sleep 0.5; done
[ -b "$(part 3)" ] || die "partitions did not appear"

step format
mkfs.vfat -F 32 -n EFI "$(part 2)" >/dev/null
mkfs.btrfs -q -f -L dahlia "$(part 3)"
mkdir -p "$TARGET"
mount "$(part 3)" "$TARGET"
btrfs -q subvolume create "$TARGET/@rootfs"
umount "$TARGET"
mount -o subvol=@rootfs "$(part 3)" "$TARGET"
mkdir -p "$TARGET/boot/efi"
mount "$(part 2)" "$TARGET/boot/efi"

# --- Debian base -----------------------------------------------------------------
step debootstrap
debootstrap --arch=amd64 --components=main,contrib,non-free-firmware \
    --include=ca-certificates,locales,keyboard-configuration,console-setup \
    "$SUITE" "$TARGET" "$MIRROR" >/dev/null

for m in dev dev/pts proc sys run; do
    mkdir -p "$TARGET/$m"
    mount --bind "/$m" "$TARGET/$m"
done
# Package scripts must not start services inside the chroot.
printf '#!/bin/sh\nexit 101\n' > "$TARGET/usr/sbin/policy-rc.d"
chmod 0755 "$TARGET/usr/sbin/policy-rc.d"

# Package scripts read the host name (certificate for <name>.local), so the
# chroot runs in its own UTS namespace with the new name.
in_target() {
    # shellcheck disable=SC2016 # expanded by the inner sh
    unshare --uts sh -c 'hostname "$1"; shift; exec chroot "$@"' sh "$HOSTNAME_NEW" \
        "$TARGET" env DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 "$@" </dev/null
}

step system
cat > "$TARGET/etc/apt/sources.list" <<SOURCES
deb $MIRROR $SUITE main contrib non-free-firmware
deb $MIRROR $SUITE-updates main contrib non-free-firmware
deb http://security.debian.org/debian-security $SUITE-security main contrib non-free-firmware
SOURCES
root_uuid="$(blkid -s UUID -o value "$(part 3)")"
efi_uuid="$(blkid -s UUID -o value "$(part 2)")"
cat > "$TARGET/etc/fstab" <<FSTAB
# Written by the Dahlia migration helper. Data disks use systemd mount units.
UUID=$root_uuid /         btrfs subvol=@rootfs,noatime 0 0
UUID=$efi_uuid  /boot/efi vfat  umask=0077             0 1
FSTAB
printf '%s\n' "$HOSTNAME_NEW" > "$TARGET/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\t\tlocalhost ip6-localhost ip6-loopback\n' \
    "$HOSTNAME_NEW" > "$TARGET/etc/hosts"
ln -sf "/usr/share/zoneinfo/$TIMEZONE" "$TARGET/etc/localtime"
printf '%s\n' "$TIMEZONE" > "$TARGET/etc/timezone"
sed -i 's/^# *\(en_US.UTF-8\|de_DE.UTF-8\)/\1/' "$TARGET/etc/locale.gen"
in_target locale-gen >/dev/null
printf 'LANG=en_US.UTF-8\n' > "$TARGET/etc/default/locale"
sed -i "s/^XKBLAYOUT=.*/XKBLAYOUT=\"$KEYMAP\"/" "$TARGET/etc/default/keyboard"

# Network: the interface keeps a fixed name, bound to its MAC address.
mkdir -p "$TARGET/etc/systemd/network"
printf '[Match]\nMACAddress=%s\n\n[Link]\nName=%s\n' "$NET_MAC" "$NET_NAME" \
    > "$TARGET/etc/systemd/network/10-dahlia-lan.link"
{
    printf 'source /etc/network/interfaces.d/*\n\nauto lo\niface lo inet loopback\n\n'
    printf 'allow-hotplug %s\n' "$NET_NAME"
    if [ "$NET_MODE" = static ]; then
        printf 'iface %s inet static\n    address %s\n    gateway %s\n' \
            "$NET_NAME" "$NET_ADDRESS" "$NET_GATEWAY"
    else
        printf 'iface %s inet dhcp\n' "$NET_NAME"
    fi
} > "$TARGET/etc/network/interfaces.new"

in_target apt-get update -q >/dev/null
in_target apt-get install -y -q --no-install-recommends \
    linux-image-amd64 firmware-linux-free firmware-misc-nonfree firmware-realtek \
    intel-microcode amd64-microcode systemd-sysv ifupdown dhcpcd-base \
    openssh-server sudo btrfs-progs dosfstools efibootmgr \
    grub-efi-amd64 grub-pc-bin less nano curl >/dev/null
mkdir -p "$TARGET/etc/network"
mv "$TARGET/etc/network/interfaces.new" "$TARGET/etc/network/interfaces"
if [ "$NET_MODE" = static ]; then
    rm -f "$TARGET/etc/resolv.conf"
    for dns in $NET_DNS; do printf 'nameserver %s\n' "$dns"; done > "$TARGET/etc/resolv.conf"
fi

# Account as the ISO installer creates it: UID 1001, sudo, no root password.
in_target useradd -m -u 1001 -s /bin/bash -G sudo,users "$ACCOUNT"
# chpasswd reads stdin, which in_target closes: pass a file in the target.
install -m 0600 /dev/null "$TARGET/root/.dahlia-pw"
printf '%s:%s\n' "$ACCOUNT" "$(cat "$PASSWORD_FILE")" > "$TARGET/root/.dahlia-pw"
in_target sh -c 'chpasswd < /root/.dahlia-pw'
rm -f "$TARGET/root/.dahlia-pw"
in_target passwd -l root >/dev/null

# --- Dahlia ------------------------------------------------------------------------
step dahlia
install -m 0644 "$KEYRING" "$TARGET/usr/share/keyrings/dahlia-archive-keyring.asc"
cat > "$TARGET/etc/apt/sources.list.d/dahlia.sources" <<DAHLIA
Types: deb
URIs: $DAHLIA_REPO
Suites: ./
Signed-By: /usr/share/keyrings/dahlia-archive-keyring.asc
DAHLIA
in_target apt-get update -q >/dev/null
# A time limit and the tail of dpkg's log, so a hanging package script ends
# with a message in the helper instead of a step that never finishes.
if ! in_target timeout "$APT_TIMEOUT" apt-get install -y -q "${PACKAGES[@]}" >/dev/null; then
    tail -n 40 "$TARGET/var/log/apt/term.log" 2>/dev/null || true
    ps -eo pid,etimes,args | tail -n 30 || true
    die "installing the Dahlia packages failed"
fi

# The setup code the person saw in the helper.
install -d -m 0700 "$TARGET/var/lib/dashboard"
printf '%s\n' "$SETUP_CODE" > "$TARGET/var/lib/dashboard/setup-code.preset"
chmod 0600 "$TARGET/var/lib/dashboard/setup-code.preset"
in_target chown -R ui:ui /var/lib/dashboard

# --- Unraid settings ------------------------------------------------------------------
step unraid
if [ -n "${UNRAID_DIR:-}" ] && [ -d "$UNRAID_DIR" ]; then
    install -d -m 0700 "$TARGET/var/lib/dahlia/unraid"
    cp -a "$UNRAID_DIR/." "$TARGET/var/lib/dahlia/unraid/"
    chown -R 0:0 "$TARGET/var/lib/dahlia/unraid"
    chmod -R go-rwx "$TARGET/var/lib/dahlia/unraid"
fi

# --- boot loader -------------------------------------------------------------------
step boot
in_target update-initramfs -u -k all >/dev/null
# Removable EFI path: boots without an NVRAM entry, which a container cannot
# write reliably. The BIOS core goes into the bios_grub partition.
in_target grub-install --target=x86_64-efi --efi-directory=/boot/efi --removable \
    --no-nvram >/dev/null
in_target grub-install --target=i386-pc "$DISK" >/dev/null
in_target update-grub >/dev/null 2>&1

rm -f "$TARGET/usr/sbin/policy-rc.d"
in_target apt-get clean
sync
step finished
