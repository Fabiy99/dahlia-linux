#!/bin/sh
# Helpers shared by the installer scripts. POSIX sh: runs in the busybox ash
# of debian-installer and is tested with dash.

# One DNS label, lower case, as dahlia-agent's system.set_hostname accepts.
valid_hostname() {
    case "$1" in
        ''|-*|*-|*[!a-z0-9-]*) return 1 ;;
    esac
    [ "${#1}" -le 63 ]
}

valid_ipv4() {
    case "$1" in
        *[!0-9.]*|.*|*.|*..*) return 1 ;;
    esac
    old_ifs=$IFS
    IFS=.
    # shellcheck disable=SC2086 # split on dots on purpose
    set -- $1
    IFS=$old_ifs
    [ "$#" -eq 4 ] || return 1
    for octet in "$@"; do
        # No leading zeros: 010 would be read as octal by some tools.
        case "$octet" in
            0?*) return 1 ;;
        esac
        [ "$octet" -le 255 ] || return 1
    done
}

# 192.168.1.10/24, prefix 8 to 30.
valid_cidr() {
    case "$1" in
        */*) ;;
        *) return 1 ;;
    esac
    prefix="${1##*/}"
    case "$prefix" in
        ''|*[!0-9]*|0?*) return 1 ;;
    esac
    [ "$prefix" -ge 8 ] && [ "$prefix" -le 30 ] && valid_ipv4 "${1%/*}"
}

# One OpenSSH public key line: type, base64 blob, optional comment.
valid_ssh_key() {
    case "$1" in
        *"
"*) return 1 ;;
    esac
    # shellcheck disable=SC2086 # split into words on purpose
    set -- $1
    [ "$#" -ge 2 ] || return 1
    case "$1" in
        ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521) ;;
        sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com) ;;
        *) return 1 ;;
    esac
    case "$2" in
        AAAA*) ;;
        *) return 1 ;;
    esac
    case "$2" in
        *[!A-Za-z0-9+/=]*) return 1 ;;
    esac
    [ "${#2}" -ge 60 ]
}

# Bytes to "500 GB" / "1.8 TB" (decimal units, as disk vendors print them).
format_size() {
    awk -v b="$1" 'BEGIN {
        if (b >= 1e12) printf "%.1f TB", b / 1e12
        else if (b >= 1e9) printf "%.0f GB", b / 1e9
        else printf "%.0f MB", b / 1e6
    }'
}

# Value of KEY from an answers file (KEY=value lines, no quoting).
answer() {
    sed -n "s/^$1=//p" "$2" | head -n1
}
