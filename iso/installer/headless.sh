#!/bin/sh
# Install without a screen: runs inside debian-installer as
# partman/early_command when the stick was prepared by the USB Creator of
# the Dahlia app (boot entry "Install without screen", dahlia.mode=headless).
#
# The USB Creator wrote /cdrom/dahlia/headless.cfg (a fixed-size slot in the
# ISO, see build-iso.sh) with the server name, time zone, keyboard layout,
# the PC's addresses and a random token. Nothing secret is on the stick:
# this script reports the disks to the app on the PC over HTTP, waits until
# the person picks the system disk there and gets the account, its password
# and the setup code in the answer. Without a pick nothing is wiped; a disk
# with data needs its serial number (or name) typed in the app.
#
# Protocol (the app listens on the PC, port in headless.cfg):
#   GET /headless/<token>/hello?disks=<list>  disks "device.bytes.model.serial.data"
#                                             joined by "~", only [A-Za-z0-9/_-]
#   GET /headless/<token>/answer              204 until picked, then KEY=value
#   GET /headless/<token>/progress?step=<s>   report.sh, late-command.sh
set -e

DIR="$(dirname "$0")"
CFG=/cdrom/dahlia/headless.cfg
ANSWERS=/tmp/dahlia-answers
WAIT_SECONDS=86400

# shellcheck source=/dev/null # iso/installer/lib.sh
. "$DIR/lib.sh"
# shellcheck source=/dev/null # part of debian-installer
. /usr/share/debconf/confmodule

log() { logger -t dahlia-headless "$*"; }
fail() { log "FAILED: $*"; exit 1; }

token="$(answer TOKEN "$CFG")"
valid_token "$token" || fail "no valid token in $CFG (stick not prepared by the USB Creator?)"
host="$(answer HOSTNAME "$CFG")"
valid_hostname "$host" || fail "invalid host name '$host'"
tz="$(answer TIMEZONE "$CFG")"
keymap="$(answer KEYMAP "$CFG")"

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

# Partitions or a filesystem signature: the disk holds data.
has_data() {
    name="${1#/dev/}"
    for p in "/sys/block/$name/$name"*; do
        [ -e "$p" ] && return 0
    done
    [ -n "$(blkid -p -o value -s TYPE "$1" 2>/dev/null || true)" ]
}

# Text for the URL: spaces become "_", anything else outside [A-Za-z0-9_-] goes.
url_word() { tr ' ' '_' | tr -cd 'A-Za-z0-9_-'; }

disks=/tmp/dahlia-disks
: > "$disks"
skip="$(boot_disk)"
for dev in $(list-devices disk); do
    case "$dev" in /dev/sr*) continue ;; esac
    [ "$dev" = "$skip" ] && continue
    name="${dev#/dev/}"
    bytes=$(( $(cat "/sys/block/$name/size" 2>/dev/null || echo 0) * 512 ))
    model="$(udev_prop "$dev" ID_MODEL | url_word)"
    serial="$(udev_prop "$dev" ID_SERIAL_SHORT | url_word)"
    data=0
    has_data "$dev" && data=1
    printf '%s.%s.%s.%s.%s\n' "$dev" "$bytes" "$model" "$serial" "$data" >> "$disks"
done
[ -s "$disks" ] || fail "no disk to install on"
log "disks: $(tr '\n' ' ' < "$disks")"

# The first PC address that answers.
list="$(tr '\n' '~' < "$disks")"
pc=""
start="$(date +%s)"
while [ -z "$pc" ]; do
    for a in $(answer PC "$CFG"); do
        valid_endpoint "$a" || continue
        if wget -q -T 10 -O /tmp/dahlia-hello "http://$a/headless/$token/hello?disks=$list"; then
            pc="$a"
            break
        fi
    done
    if [ -z "$pc" ]; then
        [ $(( $(date +%s) - start )) -lt "$WAIT_SECONDS" ] || fail "the Dahlia app did not answer"
        log "waiting for the Dahlia app on: $(answer PC "$CFG")"
        sleep 10
    fi
done
log "talking to the Dahlia app on $pc"
echo "$pc" > /tmp/dahlia-headless-pc
echo "$token" > /tmp/dahlia-headless-token

# Wait for the pick. A wrong answer is reported and asked again.
reply=/tmp/dahlia-reply
while :; do
    rm -f "$reply"
    if wget -q -T 10 -O "$reply" "http://$pc/headless/$token/answer" && [ -s "$reply" ]; then
        disk="$(answer DISK "$reply")"
        account="$(answer ACCOUNT "$reply")"
        pw="$(answer PASSWORD "$reply")"
        code="$(answer SETUP_CODE "$reply")"
        confirm="$(answer CONFIRM "$reply")"
        line="$(grep "^$disk\." "$disks" || true)"
        problem=""
        if [ -z "$disk" ] || [ -z "$line" ]; then
            problem=disk
        elif ! valid_account "$account"; then
            problem=account
        elif ! valid_hex "$pw"; then
            problem=password
        elif ! valid_setup_code "$code"; then
            problem=code
        elif [ "$(echo "$line" | cut -d. -f5)" = 1 ]; then
            serial="$(echo "$line" | cut -d. -f4)"
            [ "$confirm" = "${serial:-${disk#/dev/}}" ] || problem=confirm
        fi
        [ -z "$problem" ] && break
        log "answer rejected: $problem"
        wget -q -T 10 -O /dev/null "http://$pc/headless/$token/progress?step=rejected_$problem" || true
    fi
    sleep 5
done
rm -f "$reply"
log "system disk: $disk, account: $account"

{
    echo "DISK=$disk"
    echo "ACCOUNT=$account"
    echo "HOSTNAME=$host"
    echo "TIMEZONE=$tz"
    echo "KEYMAP=$keymap"
    echo "NETWORK=dhcp"
} > "$ANSWERS"
printf '%s\n' "$pw" > /tmp/dahlia-password.hex
printf '%s\n' "$code" > /tmp/dahlia-setup-code
: > /tmp/dahlia-ssh-key

db_set passwd/username "$account"
db_fset passwd/username seen true
db_set partman-auto/disk "$disk"
db_fset partman-auto/disk seen true
sh "$DIR/report.sh" partition
