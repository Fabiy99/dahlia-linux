# Architecture

[Deutsche Fassung](../de/architektur.md)

Status: milestone 1. This document describes the target design. What is already built is
listed in the README under "Milestones".

## Installation

The ISO is a Debian 13 installer with a preseed file. It only asks for the target disk,
hostname and language. At the end, `preseed/late_command` adds the Dahlia apt repository
and installs the `dahlia-*` packages.

Root filesystem: Btrfs with subvolumes `@` and `@home`. `snapper` takes a snapshot before
and after every apt run; `grub-btrfs` makes the snapshots bootable from the GRUB menu.

On first boot, the dashboard only shows the setup wizard. It is protected by a one-time
code printed on tty1 and in `/etc/motd`.

## Packages

| Package | Contents |
| --- | --- |
| `dahlia-base` | Configuration, nftables rules, user `dahlia-ui`, group `dahlia` (GID 1000) |
| `dahlia-agent` | Root service on `/run/dahlia/agent.sock`, fixed action list |
| `dahlia-dashboard` | FastAPI backend behind nginx, vanilla HTML/CSS/JS frontend |
| `dahlia-storage` | disk-manager, mover, SnapRAID wrapper, health checks |
| `dahlia-archive-keyring` | Public key of the apt repository |

## Storage

| Path | Contents |
| --- | --- |
| `/mnt/diskN` | Individual data disks, one filesystem each |
| `/mnt/parityN` | SnapRAID parity, never part of a pool |
| `/mnt/cache` | SSD(s), Btrfs, RAID1 with two SSDs |
| `/mnt/storage` | mergerfs across all data disks (mover target) |
| `/mnt/user` | mergerfs across `/mnt/cache/shares` and all data disks, cache first |
| `/mnt/external` | USB and NTFS disks, outside pool and SnapRAID |

Apps and shares only use `/mnt/user`. New files land on the cache
(`category.create=ff`); the mover moves them to `/mnt/storage` at night. The path under
`/mnt/user` does not change.

Disks are mounted via generated systemd mount units with `nofail` and
`x-systemd.device-timeout=15s`. If a disk is missing, the system still boots and reports
the failure.

SnapRAID runs through a wrapper on a timer: `diff`, abort above a delete threshold, `sync`,
weekly `scrub`. Mover and SnapRAID share a lock and never run at the same time.

## Privileges and security

- `dahlia-dashboard` runs as `dahlia-ui` with systemd hardening and listens on
  `127.0.0.1` only. nginx terminates TLS on port 443.
- `dahlia-agent` runs as root and only accepts actions from its list. Every action
  validates its parameters.
- Docker publishes container ports on `127.0.0.1` only (`daemon.json`: `"ip"`). Apps are
  reached through nginx. Apps that need LAN ports are flagged in their app template.
- The firewall (nftables) opens ports only for enabled features and only for the local
  subnet.

## Network and names

- Server: `dahlia.local` via Avahi.
- Apps: port links (default), single-label mDNS aliases (`jellyfin.local`) or
  `*.dahlia.home.arpa` via AdGuard rewrites.
- Certificates from a local Dahlia CA. The CA certificate is available for download in the
  dashboard.

## Updates

- Debian security updates via `unattended-upgrades`.
- Dahlia packages and other updates are applied from the UI, always with a snapshot first.
- The apt repository is built and signed in CI. The private key exists only as a GitHub
  secret.
