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

# apt resolves the remaining dependencies from the network mirror. stdin is
# /dev/null and apt has a time limit: a prompt or a hanging download fails
# the install with a log instead of leaving it stuck.
if ! in-target timeout "$APT_TIMEOUT" sh -c \
        'DEBIAN_FRONTEND=noninteractive apt-get install -y /opt/dahlia-install/*.deb' \
        </dev/null; then
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
