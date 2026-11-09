#!/bin/sh
# Runs inside debian-installer (busybox sh) as preseed/late_command.
# Installs the Dahlia packages shipped on the ISO into the target system.
set -e

STAGE=/target/opt/dahlia-install
APT_TIMEOUT=1200

log() { logger -t dahlia-install "$*"; }

mkdir -p "$STAGE"
cp /cdrom/dahlia/pool/*.deb "$STAGE/"

# apt resolves the Debian dependencies from the network mirror. A hanging
# download or maintainer script must fail the install with a log instead
# of leaving it stuck forever.
if ! in-target timeout "$APT_TIMEOUT" sh -c \
        'DEBIAN_FRONTEND=noninteractive apt-get install -y /opt/dahlia-install/*.deb'; then
    log "FAILED: apt-get install of the Dahlia packages"
    if [ -f /target/var/log/apt/term.log ]; then
        tail -n 60 /target/var/log/apt/term.log | while IFS= read -r line; do
            log "term.log: $line"
        done
    fi
    ps | while IFS= read -r line; do log "ps: $line"; done
    exit 1
fi

# Initial password "dahlia" must be changed on first login.
in-target chage -d 0 dahlia-admin

rm -rf "$STAGE"
