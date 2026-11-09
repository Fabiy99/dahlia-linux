#!/bin/sh
# Runs inside debian-installer (busybox sh) as preseed/late_command.
# Installs the Dahlia packages shipped on the ISO into the target system.
set -e

STAGE=/target/opt/dahlia-install
APT_TIMEOUT=1200

log() { logger -t dahlia-install "$*"; }

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

# Initial password "dahlia" must be changed on first login.
in-target chage -d 0 dahlia-admin

rm -rf "$STAGE"
