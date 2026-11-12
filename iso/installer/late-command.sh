#!/bin/sh
# Runs inside debian-installer (busybox sh) as preseed/late_command.
# Installs the Dahlia packages shipped on the ISO into the target system.
set -e

DIR="$(dirname "$0")"
STAGE=/target/opt/dahlia-install
APT_TIMEOUT=1200
# Written by guided.sh; missing in the unattended install.
ANSWERS=/tmp/dahlia-answers
KEYFILE=/tmp/dahlia-ssh-key

log() { logger -t dahlia-install "$*"; }

# The account d-i created (asked in the guided install, dahlia-admin in the
# unattended one).
ACCOUNT="$(debconf-get passwd/username)"

if [ -f "$ANSWERS" ]; then
    if ! sh "$DIR/apply-answers.sh" /target "$ANSWERS" "$KEYFILE" 2>/tmp/dahlia-apply.err; then
        log "FAILED: apply-answers: $(cat /tmp/dahlia-apply.err)"
        exit 1
    fi
    # Package scripts (certificate for <name>.local) read the running host
    # name, which is still the installer's.
    hostname "$(cat /target/etc/hostname)"
    if [ -d "/target/home/$ACCOUNT/.ssh" ]; then
        in-target chown -R "$ACCOUNT:$ACCOUNT" "/home/$ACCOUNT/.ssh"
    fi
    log "guided answers applied: $(tr '\n' ' ' < "$ANSWERS")"
fi

mkdir -p "$STAGE"
cp /cdrom/dahlia/pool/*.deb "$STAGE/"

# The target's sources.list still has the install medium ("deb cdrom:...")
# and some dependencies come from it. Without the disc at /media/cdrom apt
# waits for "insert the disc and press Enter" forever, so bind it there.
mkdir -p /target/media/cdrom
mount --bind /cdrom /target/media/cdrom

# apt resolves the remaining dependencies from the network mirror. apt has
# a time limit and reads stdin from /dev/null, so a prompt or a hanging
# download fails the install with a log instead of leaving it stuck.
# The redirect must stay inside the chroot command: stdin of late_command is
# the debconf channel. Redirecting it for in-target itself leaves debconf
# replies unread in the pipe, finish-install then reads them and stops.
if ! in-target timeout "$APT_TIMEOUT" sh -c \
        'DEBIAN_FRONTEND=noninteractive apt-get install -y /opt/dahlia-install/*.deb </dev/null'; then
    umount /target/media/cdrom || true
    log "FAILED: apt-get install of the Dahlia packages"
    if [ -f /target/var/log/apt/term.log ]; then
        tail -n 60 /target/var/log/apt/term.log | while IFS= read -r line; do
            log "term.log: $line"
        done
    fi
    ps | while IFS= read -r line; do log "ps: $line"; done
    exit 1
fi

umount /target/media/cdrom

# Unattended install: the initial password "dahlia" must be changed on
# first login. The guided install asked for the password.
if [ ! -f "$ANSWERS" ]; then
    in-target chage -d 0 "$ACCOUNT"
fi

rm -rf "$STAGE"
