#!/bin/bash
# wg-quick PostUp/PostDown of wg: let forwarded tunnel traffic past
# Docker's FORWARD chain.
#
#   wireguard-forward.sh up|down IFACE
#
# When Docker switches on IPv4 forwarding itself, it sets the policy of the
# iptables FORWARD chain to DROP. DOCKER-USER is the chain Docker keeps for
# the admin's rules and jumps to first; it is created here if Docker has not
# run yet, and Docker keeps it. NAT and the open UDP port are in the
# nftables table inet dahlia (firewall).
set -euo pipefail

op="${1:-}"
iface="${2:-}"
[[ "$iface" =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || { echo "usage: $0 up|down IFACE" >&2; exit 2; }
command -v iptables >/dev/null || exit 0

rules=(
    "-i $iface -j ACCEPT"
    "-o $iface -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
)
case "$op" in
    up)
        iptables -N DOCKER-USER 2>/dev/null || true
        for r in "${rules[@]}"; do
            # shellcheck disable=SC2086 # the rule is split into arguments on purpose
            iptables -C DOCKER-USER $r 2>/dev/null || iptables -I DOCKER-USER $r
        done
        ;;
    down)
        for r in "${rules[@]}"; do
            # shellcheck disable=SC2086
            iptables -D DOCKER-USER $r 2>/dev/null || true
        done
        ;;
    *)
        echo "usage: $0 up|down IFACE" >&2
        exit 2
        ;;
esac
