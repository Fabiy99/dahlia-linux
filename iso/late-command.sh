#!/bin/sh
# Runs inside debian-installer (busybox sh) as preseed/late_command.
# Installs the Dahlia packages shipped on the ISO into the target system.
set -e

STAGE=/target/opt/dahlia-install

mkdir -p "$STAGE"
cp /cdrom/dahlia/pool/*.deb "$STAGE/"

# apt resolves the Debian dependencies from the network mirror.
in-target sh -c 'DEBIAN_FRONTEND=noninteractive apt-get install -y /opt/dahlia-install/*.deb'

# Initial password "dahlia" must be changed on first login.
in-target chage -d 0 dahlia-admin

rm -rf "$STAGE"
