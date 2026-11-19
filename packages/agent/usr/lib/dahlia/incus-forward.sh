#!/bin/bash
# Docker sets the iptables FORWARD policy to DROP. Let traffic of the Incus
# bridge pass through DOCKER-USER, which Docker keeps across restarts.
# Runs as incus-forward.service, after docker.service.
set -euo pipefail

bridge=incusbr0
iptables -N DOCKER-USER 2>/dev/null || true
while read -r -a rule; do
    iptables -C DOCKER-USER "${rule[@]}" 2>/dev/null || iptables -I DOCKER-USER "${rule[@]}"
done <<RULES
-i $bridge -j ACCEPT
-o $bridge -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
RULES
