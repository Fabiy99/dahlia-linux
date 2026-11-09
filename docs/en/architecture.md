# Architecture

[Deutsche Fassung](../de/architektur.md)

Status: milestone 1. This document describes the target design. What is already built is
listed in the README under "Milestones".

## Installation

The ISO is the current Debian 13 netinst image, remastered by `iso/build-iso.sh`.
`iso/preseed.cfg` is embedded in the installer initrd, so the install runs without
questions. The one exception is the target disk: `iso/pick-disk.sh` picks it
automatically when there is exactly one candidate (the install medium does not count).
With several disks the installer asks. At the end, `iso/late-command.sh` installs the
Dahlia packages shipped on the ISO under `/dahlia/pool`.

Hostname (`dahlia`), language (English, German keyboard) and timezone (Europe/Berlin) are
preset and changed later in the setup wizard. The installer creates the user
`dahlia-admin` (UID 1001, sudo) with the initial password `dahlia`, which must be changed
on first login. root has no password.

Root filesystem: Btrfs; the installer creates the subvolume `@rootfs`. `snapper` takes a
snapshot before and after every apt run; `grub-btrfs` makes the snapshots bootable from
the GRUB menu.

On first boot, the dashboard only shows the setup wizard. It is protected by a one-time
code printed on tty1 and in `/etc/motd` (from milestone 5).

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
