#!/bin/bash
# Install Tailscale from its apt repository, as Tailscale's own instructions
# do: key to /usr/share/keyrings, list to /etc/apt/sources.list.d. Runs as
# dahlia-tailscale-install.service (dahlia-agent: vpn.tailscale_install).
set -euo pipefail

# shellcheck source=/dev/null
. /etc/os-release
codename="${VERSION_CODENAME:-trixie}"
[[ "$codename" =~ ^[a-z]+$ ]] || { echo "tailscale-install: bad codename $codename" >&2; exit 1; }
base="https://pkgs.tailscale.com/stable/debian"
key=/usr/share/keyrings/tailscale-archive-keyring.gpg
list=/etc/apt/sources.list.d/tailscale.list

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL --proto '=https' --max-time 60 "$base/$codename.noarmor.gpg" -o "$tmp"
install -m 0644 "$tmp" "$key"
echo "deb [signed-by=$key] $base $codename main" > "$list"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tailscale
systemctl enable --now tailscaled.service
echo "tailscale-install: $(tailscale version | head -1) installed"
