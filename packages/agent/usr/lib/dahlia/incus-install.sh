#!/bin/bash
# Install Incus from Debian and set it up for Dahlia: storage pool "default"
# (dir driver) on the system SSD or the cache, NAT bridge incusbr0, the
# dashboard account in incus-admin. Runs as incus-install.service
# (agent: vm.install). Usage: incus-install.sh system|cache <account>
set -euo pipefail

location="${1:-}"
account="${2:-}"
case "$location" in
    system) source_dir=/var/lib/incus/storage-pools/default ;;
    cache) source_dir=/mnt/cache/incus ;;
    *) echo "incus-install: location must be system or cache" >&2; exit 2 ;;
esac
[[ "$account" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "incus-install: bad account" >&2; exit 2; }
if [[ "$location" == cache ]] && ! mountpoint -q /mnt/cache; then
    echo "incus-install: /mnt/cache is not mounted" >&2
    exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    incus qemu-system-x86 qemu-utils ovmf swtpm swtpm-tools
systemctl enable --now incus.socket incus.service

if [[ "$location" == cache ]]; then
    mkdir -p "$source_dir"
fi
if ! incus storage show default >/dev/null 2>&1; then
    incus admin init --preseed <<PRESEED
networks:
- name: incusbr0
  type: bridge
  config:
    ipv4.address: auto
    ipv4.nat: "true"
    ipv6.address: none
storage_pools:
- name: default
  driver: dir
  config:
    source: $source_dir
profiles:
- name: default
  devices:
    root:
      path: /
      pool: default
      type: disk
    eth0:
      name: eth0
      network: incusbr0
      type: nic
PRESEED
fi

if id "$account" >/dev/null 2>&1; then
    usermod -aG incus-admin "$account"
fi
systemctl enable --now incus-forward.service
# DNS and DHCP of incusbr0 on the host.
firewall apply
echo "incus-install: $(incus version | head -1) installed, pool on $source_dir"
