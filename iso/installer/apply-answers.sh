#!/bin/sh
# Writes the answers of the guided installer into the installed system.
# Runs in debian-installer from late-command.sh, before the Dahlia packages
# are installed, so their postinst scripts see the final host name.
#
# Usage: apply-answers.sh <target-root> <answers-file> [ssh-key-file]
#
# Answers file (written by guided.sh), one KEY=value per line:
#   ACCOUNT=fabian
#   HOSTNAME=nas
#   TIMEZONE=Europe/Berlin
#   NETWORK=dhcp | static
#   ADDRESS=192.168.1.10/24   GATEWAY=192.168.1.1   DNS=192.168.1.1   (static only)
set -e

TARGET="${1:?target root}"
ANSWERS="${2:?answers file}"
KEYFILE="${3:-}"

# shellcheck source=/dev/null # iso/installer/lib.sh
. "$(dirname "$0")/lib.sh"

fail() { echo "apply-answers: $*" >&2; exit 1; }

account="$(answer ACCOUNT "$ANSWERS")"
valid_account "$account" || fail "invalid account name '$account'"
host="$(answer HOSTNAME "$ANSWERS")"
tz="$(answer TIMEZONE "$ANSWERS")"
net="$(answer NETWORK "$ANSWERS")"

valid_hostname "$host" || fail "invalid host name '$host'"
printf '%s\n' "$host" > "$TARGET/etc/hostname"
# Debian maps the host name to 127.0.1.1; replace that line or add it.
hosts="$TARGET/etc/hosts"
touch "$hosts"
grep -v '^127\.0\.1\.1[[:space:]]' "$hosts" > "$hosts.new" || true
printf '127.0.1.1\t%s\n' "$host" >> "$hosts.new"
cat "$hosts.new" > "$hosts"
rm -f "$hosts.new"

if [ -n "$tz" ]; then
    case "$tz" in
        *..*|/*) fail "invalid time zone '$tz'" ;;
    esac
    [ -f "$TARGET/usr/share/zoneinfo/$tz" ] || fail "unknown time zone '$tz'"
    printf '%s\n' "$tz" > "$TARGET/etc/timezone"
    ln -sf "/usr/share/zoneinfo/$tz" "$TARGET/etc/localtime"
fi

case "$net" in
    dhcp|'') ;;
    static)
        addr="$(answer ADDRESS "$ANSWERS")"
        gw="$(answer GATEWAY "$ANSWERS")"
        dns="$(answer DNS "$ANSWERS")"
        valid_cidr "$addr" || fail "invalid address '$addr'"
        valid_ipv4 "$gw" || fail "invalid gateway '$gw'"
        valid_ipv4 "$dns" || fail "invalid DNS server '$dns'"
        ifaces="$TARGET/etc/network/interfaces"
        # netcfg wrote "iface <nic> inet dhcp" for the interface it used.
        nic="$(sed -n 's/^iface \([^ ]*\) inet dhcp.*/\1/p' "$ifaces" | head -n1)"
        [ -n "$nic" ] || fail "no DHCP interface in $ifaces"
        awk -v nic="$nic" -v addr="$addr" -v gw="$gw" '
            $1 == "iface" && $2 == nic && $4 == "dhcp" {
                print "iface " nic " inet static"
                print "    address " addr
                print "    gateway " gw
                skip = 1
                next
            }
            skip && /^[ \t]/ { next }
            { skip = 0; print }
        ' "$ifaces" > "$ifaces.new"
        cat "$ifaces.new" > "$ifaces"
        rm -f "$ifaces.new"
        rm -f "$TARGET/etc/resolv.conf"
        printf 'nameserver %s\n' "$dns" > "$TARGET/etc/resolv.conf"
        ;;
    *) fail "invalid network mode '$net'" ;;
esac

if [ -n "$KEYFILE" ] && [ -s "$KEYFILE" ]; then
    key="$(head -n1 "$KEYFILE")"
    valid_ssh_key "$key" || fail "invalid SSH key"
    ssh_dir="$TARGET/home/$account/.ssh"
    mkdir -p "$ssh_dir"
    printf '%s\n' "$key" > "$ssh_dir/authorized_keys"
    chmod 0700 "$ssh_dir"
    chmod 0600 "$ssh_dir/authorized_keys"
    # Owner is set by late-command.sh inside the target, where the
    # admin account's names resolve.
fi
