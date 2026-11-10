#!/bin/sh
# Guided install: runs inside debian-installer as partman/early_command,
# when disks and network are up and the admin password is already set.
# Asks for the system disk, server name, time zone, network and SSH key,
# shows a summary with the wipe warning and hands the disk to partman.
# The answers go to /tmp/dahlia-answers for late-command.sh.
#
# Any question can be preseeded (preseed/file=...), which the CI install
# test uses; a preseeded but invalid answer is asked again.
set -e

DIR="$(dirname "$0")"
ANSWERS=/tmp/dahlia-answers
KEYFILE=/tmp/dahlia-ssh-key

# shellcheck source=/dev/null # iso/installer/lib.sh
. "$DIR/lib.sh"
# shellcheck source=/dev/null # part of debian-installer
. /usr/share/debconf/confmodule

log() { logger -t dahlia-guided "$*"; }

debconf-loadtemplate dahlia "$DIR/dahlia.templates"
db_capb backup

# Whole-disk device the installer booted from (never offered).
boot_disk() {
    part="$(grep ' /cdrom ' /proc/mounts | cut -d' ' -f1 || true)"
    case "$part" in
        '') ;;
        /dev/nvme*|/dev/mmcblk*) echo "$part" | sed 's/p[0-9]*$//' ;;
        *) echo "$part" | sed 's/[0-9]*$//' ;;
    esac
}

udev_prop() {
    udevadm info --query=property --name="$1" 2>/dev/null | sed -n "s/^$2=//p" | head -n1
}

# "vda  21 GB  QEMU HARDDISK  (serial 1234)"; no commas, they split choices.
disk_label() {
    name="${1#/dev/}"
    bytes=$(( $(cat "/sys/block/$name/size" 2>/dev/null || echo 0) * 512 ))
    model="$(udev_prop "$1" ID_MODEL | tr '_' ' ')"
    [ -n "$model" ] || model="$(cat "/sys/block/$name/device/model" 2>/dev/null || true)"
    serial="$(udev_prop "$1" ID_SERIAL_SHORT)"
    [ -n "$serial" ] || serial="$(cat "/sys/block/$name/device/serial" 2>/dev/null || true)"
    label="$name  $(format_size "$bytes")  ${model:-unknown model}"
    [ -n "$serial" ] && label="$label  (serial $serial)"
    echo "$label" | tr -s ' ' | tr -d ','
}

choices=""
choices_c=""
skip="$(boot_disk)"
for dev in $(list-devices disk); do
    case "$dev" in /dev/sr*) continue ;; esac
    [ "$dev" = "$skip" ] && continue
    choices="${choices:+$choices, }$(disk_label "$dev")"
    choices_c="${choices_c:+$choices_c, }$dev"
done
log "disk candidates: $choices_c"
if [ -z "$choices_c" ]; then
    db_input critical dahlia/no_disk || true
    db_go || true
    exit 1
fi
db_subst dahlia/disk CHOICES "$choices"
db_subst dahlia/disk CHOICES_C "$choices_c"

invalid() {
    db_subst dahlia/invalid MESSAGE "$1"
    db_fset dahlia/invalid seen false
    db_input critical dahlia/invalid || true
    db_go || true
    # Ask the question again even when it was preseeded.
    db_fset "$2" seen false
}

# Show one question. Returns 1 when the user pressed "Go Back".
ask() {
    db_input critical "$1" || true
    if db_go; then return 0; fi
    return 1
}

get() {
    db_get "$1"
    printf '%s' "$RET"
}

step=disk
while :; do
    case "$step" in
        disk)
            ask dahlia/disk || continue
            step=hostname
            ;;
        hostname)
            ask dahlia/hostname || { step=disk; continue; }
            if valid_hostname "$(get dahlia/hostname)"; then
                step=timezone
            else
                invalid "Use one word with lower case letters, digits and hyphens, at most 63 characters." dahlia/hostname
            fi
            ;;
        timezone)
            ask dahlia/timezone || { step=hostname; continue; }
            step=network
            ;;
        network)
            ask dahlia/network || { step=timezone; continue; }
            if [ "$(get dahlia/network)" = static ]; then step=address; else step=ssh_key; fi
            ;;
        address)
            ask dahlia/address || { step=network; continue; }
            if valid_cidr "$(get dahlia/address)"; then
                step=gateway
            else
                invalid "Enter the address with prefix length, for example 192.168.1.10/24." dahlia/address
            fi
            ;;
        gateway)
            ask dahlia/gateway || { step=address; continue; }
            if valid_ipv4 "$(get dahlia/gateway)"; then
                step=dns
            else
                invalid "Enter an IPv4 address, for example 192.168.1.1." dahlia/gateway
            fi
            ;;
        dns)
            ask dahlia/dns || { step=gateway; continue; }
            if valid_ipv4 "$(get dahlia/dns)"; then
                step=ssh_key
            else
                invalid "Enter an IPv4 address, for example 192.168.1.1." dahlia/dns
            fi
            ;;
        ssh_key)
            if ask dahlia/ssh_key; then :; else
                if [ "$(get dahlia/network)" = static ]; then step=dns; else step=network; fi
                continue
            fi
            key="$(get dahlia/ssh_key)"
            if [ -z "$key" ] || valid_ssh_key "$key"; then
                step=confirm
            else
                invalid "This is not an OpenSSH public key line (ssh-ed25519 AAAA... comment)." dahlia/ssh_key
            fi
            ;;
        confirm)
            disk="$(get dahlia/disk)"
            db_subst dahlia/confirm DISK "$disk"
            db_subst dahlia/confirm DISK_LABEL "$(disk_label "$disk")"
            db_subst dahlia/confirm HOSTNAME "$(get dahlia/hostname)"
            db_subst dahlia/confirm TIMEZONE "$(get dahlia/timezone)"
            if [ "$(get dahlia/network)" = static ]; then
                db_subst dahlia/confirm NETWORK "static $(get dahlia/address) gateway $(get dahlia/gateway) DNS $(get dahlia/dns)"
            else
                db_subst dahlia/confirm NETWORK "DHCP"
            fi
            if [ -n "$(get dahlia/ssh_key)" ]; then
                db_subst dahlia/confirm SSH_KEY "$(get dahlia/ssh_key | cut -d' ' -f1,3-)"
            else
                db_subst dahlia/confirm SSH_KEY "none (password login)"
            fi
            ask dahlia/confirm || { step=ssh_key; continue; }
            if [ "$(get dahlia/confirm)" = true ]; then
                break
            fi
            # "No": start over at the disk, keeping the answers as defaults.
            for q in disk hostname timezone network address gateway dns ssh_key confirm; do
                db_fset "dahlia/$q" seen false
            done
            step=disk
            ;;
    esac
done

disk="$(get dahlia/disk)"
{
    echo "DISK=$disk"
    echo "HOSTNAME=$(get dahlia/hostname)"
    echo "TIMEZONE=$(get dahlia/timezone)"
    echo "NETWORK=$(get dahlia/network)"
    if [ "$(get dahlia/network)" = static ]; then
        echo "ADDRESS=$(get dahlia/address)"
        echo "GATEWAY=$(get dahlia/gateway)"
        echo "DNS=$(get dahlia/dns)"
    fi
} > "$ANSWERS"
get dahlia/ssh_key > "$KEYFILE"
log "answers: $(tr '\n' ' ' < "$ANSWERS")"

# partman-auto partitions this disk without asking again; the wipe was
# confirmed above.
db_set partman-auto/disk "$disk"
db_fset partman-auto/disk seen true
