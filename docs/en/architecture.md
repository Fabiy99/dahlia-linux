# Architecture

[Deutsche Fassung](../de/architektur.md)

Status: milestone 6. This document describes the target design. What is already built is
listed in the README under "Milestones".

## Installation

The ISO is the current Debian 13 netinst image, remastered by `iso/build-iso.sh`.
`iso/preseed.cfg` is embedded in the installer initrd, so the install runs without
questions. The one exception is the target disk: `iso/pick-disk.sh` picks it
automatically when there is exactly one candidate (the install medium does not count).
With several disks the installer asks. From milestone 8 on, the installer always asks for the
target disk (model, size, serial) and shows a summary before wiping it; automatic selection
remains only in the menu entry for unattended installs. At the end, `iso/late-command.sh` installs the
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
| `dahlia-base` | Configuration, nftables rules, group `dahlia` (GID 1000), pulls in all Dahlia packages |
| `dahlia-agent` | Root service on `/run/dahlia/agent.sock`, fixed action list |
| `dahlia-dashboard` | FastAPI backend as `dahlia-ui` behind nginx, vanilla HTML/CSS/JS frontend |
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

### dahlia-disk (from milestone 3)

`dahlia-disk` from the `dahlia-storage` package implements the layout. It never formats
anything. State lives in `/etc/dahlia/storage.json`; everything else is generated from it.

```sh
dahlia-disk list                          # disks and what Dahlia may do with them
dahlia-disk add <UUID> --role data        # existing ext4/XFS/Btrfs as /mnt/diskN
dahlia-disk add <UUID> --role parity      # as /mnt/parityN, never in a pool
dahlia-disk add <UUID> --role cache       # SSD filesystem as /mnt/cache (from M3b)
dahlia-disk add <UUID> --role external    # e.g. NTFS under /mnt/external/<name>
dahlia-disk remove <UUID>                 # unmount; data stays on the disk
dahlia-disk apply                         # regenerate units and pools from storage.json
dahlia-disk snapraid-config [--write]     # generate /etc/snapraid.conf
```

New data disks join the running pools via `user.mergerfs.branches`, without a remount.
Empty mount points are locked with `chattr +i`, so a missing disk never lets data land on
the system SSD. The units hang off `dahlia-storage.target` with `Wants=`, so a missing disk
does not block boot.

### Cache, mover, SnapRAID (from milestone 3b)

The cache is one filesystem (Btrfs; with two SSDs created as RAID1 beforehand:
`mkfs.btrfs -d raid1 -m raid1 <ssd1> <ssd2>`). `dahlia-disk add --role cache` mounts it at
`/mnt/cache` and puts `/mnt/cache/shares` in front of the data disks in `/mnt/user`, again
without a remount. `/mnt/user` then uses `category.create=ff`; `/mnt/storage` stays at
`mfs`.

```sh
dahlia-mover [--dry-run] [--min-age MIN]       # cache -> /mnt/storage, path unchanged
dahlia-snapraid sync [--force]                 # diff, check delete threshold, sync
dahlia-snapraid scrub                          # verify part of the array against parity
dahlia-disk set-option snapraid_delete_threshold 50
```

The mover leaves open files, recently modified files, files that already exist on the data
disks and hard links on the cache. It copies, verifies, renames and only then deletes.
Timers: mover 03:00, SnapRAID sync 04:30, scrub Sundays 06:00 (8 %, blocks older than
10 days). Mover and SnapRAID share `/run/dahlia/array.lock`.

## Privileges and security

- `dahlia-dashboard` runs as `dahlia-ui` with systemd hardening and listens on
  `127.0.0.1` only. nginx terminates TLS on port 443.
- `dahlia-agent` runs as root and only accepts actions from its list. Every action
  validates its parameters.
- Docker publishes container ports on `127.0.0.1` only (`daemon.json`: `"ip"`). Apps are
  reached through nginx. Apps that need LAN ports are flagged in their app template.
- The firewall (nftables) opens ports only for enabled features and only for the local
  subnet.

### dahlia-agent (from milestone 4)

The agent listens on `/run/dahlia/agent.sock` (root:dahlia-agent, 0660) and is started by
`dahlia-agent.socket` on the first call. On top of the file permissions it checks the peer
via `SO_PEERCRED`: root and members of the group `dahlia-agent` are allowed. Later this is
the dashboard user `dahlia-ui`.

Protocol: one request per connection, one JSON line each way.

```text
-> {"action": "storage.add", "params": {"uuid": "...", "role": "data"}}
<- {"ok": true, "result": {...}}
<- {"ok": false, "error": {"code": "invalid_params", "message": "..."}}
```

| Action | Parameters | Effect |
| --- | --- | --- |
| `system.info` | - | Hostname, version, kernel, uptime, load, memory |
| `system.set_hostname` | `hostname` (one DNS label, lower case) | `hostnamectl`, `/etc/hosts`, Avahi |
| `system.reboot`, `system.poweroff` | - | `systemctl --no-block ...` |
| `storage.list` | - | `dahlia-disk list --json` |
| `storage.add` | `uuid`, `role` (data, parity, cache, external) | `dahlia-disk add` |
| `storage.remove` | `uuid`, `force` (optional) | `dahlia-disk remove` |
| `storage.set_option` | `name`, `value` (integer) | `dahlia-disk set-option` |
| `job.start` | `job` (mover, snapraid-sync, snapraid-scrub) | starts the unit, returns at once |
| `job.status` | `job` (plus `update`) | State and last result |
| `updates.check` | - | `apt-get update`, list of upgrades |
| `updates.apply` | - | `apt-get full-upgrade` as unit `dahlia-update` |
| `apps.catalog` | `refresh` (optional) | App catalog, `refresh` downloads it first |
| `apps.list` | - | installed apps with container state |
| `apps.install`, `apps.update` | `id` | in the background as unit `dahlia-app-<id>` |
| `apps.job` | `id` | state of the last install or update |
| `apps.start`, `apps.stop` | `id` | start or stop, also for the next boot |
| `apps.remove` | `id`, `delete_data` (optional) | remove the container, data only on request |
| `agent.actions` | - | this list |

Unknown actions, unknown or missing parameters and wrong types are rejected before any
command runs. Commands run as argument lists, never through a shell. Changing actions run
one after another. Every call is logged with UID and result in the journal
(`journalctl -u dahlia-agent`).

For testing on the console:

```sh
sudo dahlia-agent-call system.info
sudo dahlia-agent-call storage.add '{"uuid": "<UUID>", "role": "data"}'
```

### Dashboard (from milestone 5)

Open `https://<server>.local/` (port 80 redirects to 443). Until milestone 7 the
certificate is self-signed (`/etc/dahlia/tls/dashboard.crt`); the browser warns once.

The first visit only shows the setup wizard. It asks for the one-time code the server shows
on the console (tty1 and serial console) and on SSH login. The wizard creates the dashboard
account and optionally sets the server name. The account is for the web UI only, not for
SSH or the console.

| Part | Implementation |
| --- | --- |
| Backend | FastAPI/uvicorn as `dahlia-ui` on `127.0.0.1:8080`, systemd hardening (`ProtectSystem=strict`, no capabilities) |
| Frontend | static files in `/usr/share/dahlia-dashboard/www`, served by nginx, texts from `locales/de.json` and `locales/en.json` |
| Accounts | `/var/lib/dahlia-dashboard/users.json` (0600), scrypt password hashes |
| Sessions | random token in memory, cookie `HttpOnly`, `Secure`, `SameSite=Strict`; 2 h idle, 12 h at most |
| Protection | changing requests need the header `X-Dahlia-Request`; 5 failures per IP within 5 minutes, then blocked |
| Root actions | only through `dahlia-agent`; `dahlia-ui` is a member of the group `dahlia-agent` |

Pages: overview (system data, server name, reboot, power off), disks (add in a role,
remove), jobs (start mover, SnapRAID sync and scrub, state), updates (check, install),
account (change password).

Forgotten password: `sudo rm /var/lib/dahlia-dashboard/users.json` and
`sudo systemctl restart dahlia-dashboard` open the setup wizard with a new code.

### Apps (from milestone 6)

Apps are Docker containers built from app templates. The catalog lives in the repository
[dahlia-apps](https://github.com/Fabiy99/dahlia-apps) and is loaded from
`https://raw.githubusercontent.com/Fabiy99/dahlia-apps/main/`
(`/etc/dahlia/apps.json`, `file:///` also works for local catalogs).

A template (YAML, `schema: 1`) only knows: an image with a fixed version, a web port with a
public port, LAN ports, volumes under `appdata` or a share, and environment variables with
the placeholders `{TZ}`, `{PUID}`, `{PGID}`. Everything else is rejected, including
`privileged`, host networking, devices and capabilities.

| Topic | Implementation |
| --- | --- |
| Web port | Docker publishes it on `127.0.0.1:20000-29999`, nginx listens in the LAN on the template's public port (port link `http://<server>:<port>/`) |
| LAN ports | only if the template lists them; the dashboard shows them before installing |
| Data | `/mnt/user/appdata/<id>/...` (owner `dahlia-apps`), shares `/mnt/user/<name>`; no install without a mounted array |
| Start | Docker starts nothing by itself (`restart: no`); `dahlia-apps.service` starts the enabled apps after the disks |
| Containers | `no-new-privileges`, log driver `local` with 3 x 10 MB |

```sh
dahlia-apps catalog --refresh
dahlia-apps install jellyfin
dahlia-apps list
dahlia-apps remove jellyfin [--delete-data]
dahlia-apps check app.yaml        # validate a template
```

## Network and names

- Server: `dahlia.local` via Avahi.
- Apps: port links (default), single-label mDNS aliases (`jellyfin.local`) or
  `*.dahlia.home.arpa` via AdGuard rewrites.
- Certificates from a local Dahlia CA. The CA certificate is available for download in the
  dashboard.

## Updates

- Debian security updates via `unattended-upgrades`.
- Dahlia packages and other updates are applied from the UI, always with a snapshot first.
- The apt repository is built and signed in CI and published on GitHub Pages
  (`https://fabiy99.github.io/dahlia-linux/`). The private key exists only as a GitHub
  secret. Details: [apt-repo.md](apt-repo.md).
