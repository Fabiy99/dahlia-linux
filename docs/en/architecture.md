# Architecture

[Deutsche Fassung](../de/architektur.md)

Status: milestone 8. This document describes the target design. What is already built is
listed in the README under "Milestones".

## Installation (guided from milestone 8)

The ISO is the current Debian 13 netinst image, remastered by `iso/build-iso.sh`.
`iso/preseed.cfg` is embedded in the installer initrd and holds everything both modes
share. The installer is in English. Boot menu (BIOS and UEFI):

| Entry | Mode |
| --- | --- |
| Install Dahlia Linux | guided (default) |
| Install Dahlia Linux (serial console) | guided, output on ttyS0 |
| Automatic install (no questions, default password) | unattended |
| Automatic install (serial console) | unattended on ttyS0 (CI test) |

Guided (`iso/installer/dahlia-guided.cfg`, `guided.sh`): the Debian installer asks for the
keyboard layout and the password of `dahlia-admin`. Then `guided.sh` asks for:

1. System disk, with device, size, model and serial number. The install medium is never
   offered.
2. Server name (one DNS label, default `dahlia`)
3. Time zone
4. Network of the installed system: DHCP or static (address with prefix, gateway, DNS).
   The installer itself needs DHCP and internet access.
5. SSH public key for `dahlia-admin` (optional)
6. Summary with a warning that all data on the chosen disk will be deleted. "No" goes back
   to the disk selection with the answers kept as defaults.

Every question has "Go Back". `late-command.sh` writes the answers into the target with
`apply-answers.sh` (`/etc/hostname`, `/etc/hosts`, `/etc/timezone`,
`/etc/network/interfaces`, `/etc/resolv.conf`, `~dahlia-admin/.ssh/authorized_keys`)
before the Dahlia packages are installed. The password is not expired.

Unattended (`dahlia-auto.cfg`, kernel parameter `dahlia.mode=auto`): no questions. With
exactly one candidate disk, `pick-disk.sh` uses it without asking; with several the
installer asks. Server name `dahlia`, German keyboard, time zone Europe/Berlin, user
`dahlia-admin` with the initial password `dahlia`, which must be changed on first login.

In both modes: user `dahlia-admin` (UID 1001, sudo), root has no password. At the end,
`late-command.sh` installs the Dahlia packages shipped on the ISO under `/dahlia/pool`.

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
The pools run with `cache.files=partial`: SQLite in WAL mode and MariaDB need shared
mmap, which mergerfs does not offer with `cache.files=off` (direct_io).
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
| `system.stats` | - | CPU counters, memory, swap, network, usage of `/` and `/mnt/*` |
| `system.hardware` | - | mainboard (DMI), CPU temperature (hwmon), SMART per disk with `smartctl -n standby` (does not wake a sleeping disk) |
| `system.log` | `lines`, `priority`, `unit` (optional) | `journalctl -n … -p …` |
| `settings.time` / `settings.set_time` | `timezone`, `ntp` | `timedatectl set-timezone`, `set-ntp` |
| `settings.network` / `settings.set_network` | `mode` (`dhcp`/`static`), `address` (CIDR), `gateway`, `dns` | `/etc/network/interfaces`, applied by `dahlia-network` with rollback |
| `settings.confirm_network` | – | confirms the new network settings |
| `settings.spindown` / `settings.set_spindown` | `default`, `disks` (serial → minutes) | `/etc/dahlia/spindown.json`, `hdparm -S` |
| `settings.schedules` / `settings.set_schedule` | `job`, `frequency`, `time`, `weekday`, `monthday` | drop-in `OnCalendar=` for the timers |
| `shares.list` | – | shares, SMB users, whether `/mnt/user` is mounted |
| `shares.set` / `shares.remove` | `name`, `comment`, `security` (`public`/`secure`/`private`), `users` (name → `rw`/`ro`), `time_machine` | generates `/etc/samba/smb.conf`, checks it with `testparm`, starts `smbd` |
| `shares.user_add` / `shares.user_password` / `shares.user_remove` | `name`, `password` | `useradd`/`userdel` (groups `dahlia`, `dahlia-smb`), `smbpasswd` via stdin |
| `docker.list`, `docker.stats`, `docker.disk` | - | all containers, CPU/RAM per container, space used by images, volumes, build cache |
| `docker.container` | `name`, `op` (`start`, `stop`, `restart`) | `docker <op> -- <name>` |
| `docker.logs` | `name`, `lines` (10 to 5000) | `docker logs --tail` |
| `docker.prune` | - | `docker image prune -a`, `docker builder prune` |
| `system.set_hostname` | `hostname` (one DNS label, lower case) | `hostnamectl`, `/etc/hosts`, Avahi, new certificate |
| `system.reboot`, `system.poweroff` | - | `systemctl --no-block ...` |
| `storage.list` | - | `dahlia-disk list --json` |
| `storage.add` | `uuid`, `role` (data, parity, cache, external) | `dahlia-disk add` |
| `storage.remove` | `uuid`, `force` (optional) | `dahlia-disk remove` |
| `storage.set_option` | `name`, `value` (integer) | `dahlia-disk set-option` |
| `job.start` | `job` (mover, snapraid-sync, snapraid-scrub) | starts the unit, returns at once |
| `job.status` | `job` (plus `update`) | State and last result |
| `updates.check` | - | `apt-get update`, list of upgrades |
| `updates.apply` | - | `apt-get full-upgrade` through the unit `dahlia-update.service` |
| `apps.catalog` | `refresh` (optional) | App catalog and its age, `refresh` downloads it first |
| `apps.list` | - | installed apps with container state, version, available update |
| `apps.install`, `apps.update` | `id` | in the background as unit `dahlia-app-<id>` |
| `apps.job` | `id` | state of the last install or update |
| `apps.start`, `apps.stop` | `id` | start or stop, also for the next boot |
| `apps.remove` | `id`, `delete_data` (optional) | remove the container, data only on request |
| `domains.get` | - | domain, mDNS aliases, DNS records needed, certificates |
| `domains.set` | `domain`, `mdns_aliases` | `dahlia-domains set`, new certificate and proxy |
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

Open `https://<server>.local/` (port 80 redirects to 443). The local Dahlia CA issues the
certificate (see "Network and names"). Until the CA is imported, the browser warns.

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

Layout: sidebar with server name and version, content in tiles. Confirmations use an own
dialog, messages appear at the bottom right. Below 48rem width (phones) the sidebar becomes
a menu and tables turn into cards. A web app manifest allows "add to home screen" on phones
and tablets.

| Page | Content |
| --- | --- |
| Overview | system (version, kernel, CPU, address, reboot, power off), CPU and RAM usage with history, network (throughput per interface), usage of the system SSD, the pools and every disk under `/mnt`, disks with temperature and SMART state, apps with state, jobs with last result |
| Disks | usage of `/mnt/user`, `/mnt/storage` and `/mnt/cache`; one card per physical disk with model, serial number, temperature, SMART state and its filesystems (status, usage, add in a role, remove) |
| Apps | installed apps as cards (state, addresses, updates, start/stop), catalog with search and categories |
| Docker | all containers (also foreign ones) with state, ports, CPU/RAM, start/stop/restart, logs; disk usage and cleanup |
| Domains | domain, mDNS aliases, DNS records, CA download |
| Jobs | start mover, SnapRAID sync and scrub, state |
| System log | journal filtered by level and service |
| Updates | check, install |
| Shares | SMB shares on `/mnt/user`, SMB users |
| Settings | time and time zone, network (DHCP/static), disk spindown, mover and SnapRAID schedules, dashboard password, server name |

SMB shares (M9c):

- A share is a folder directly under `/mnt/user` (group `dahlia`, mode 2775, like the
  apps' shares). Without a mounted `/mnt/user` Dahlia does not create a share.
- `/etc/samba/smb.conf` is generated from `/etc/dahlia/shares.json`; the Debian original
  stays as `smb.conf.dahlia-orig`. SMB2 or newer, no printers, no NetBIOS (`nmbd` off).
- Access: public (guests read and write), secure (guests read, selected users write),
  private (selected users only). Files get the group `dahlia`.
- SMB users are system accounts without shell and home in the groups `dahlia-smb` and
  `dahlia`. The password only goes to `smbpasswd` via stdin.
- Discovery: `wsdd` for Windows, an Avahi file (`_smb._tcp`, `_adisk._tcp` for Time
  Machine) for macOS. Without a share `smbd` and `wsdd` are off.

Settings (M9b):

- Network: `settings.set_network` writes the new configuration to
  `/etc/network/interfaces.dahlia-new` and starts `dahlia-network apply` as a
  transient unit. It takes the interface down with the old configuration, swaps
  the files and brings it up again. Unless the change is confirmed with
  `settings.confirm_network` within 180 seconds, the old configuration is restored.
- Spindown: a default for all spinning disks plus values per serial number.
  `dahlia-spindown.service` applies them at boot, a udev rule for disks plugged in
  later. SSDs and NVMe are skipped.
- Schedules: drop-ins under `/etc/systemd/system/<timer>.d/dahlia-schedule.conf`,
  saved values in `/etc/dahlia/schedules.json`. "Off" disables the timer.

The live values come from the agent action `system.stats` (`/proc`, `statvfs`,
`ip -j addr`); the overview polls it every 3 seconds while it is open. The frontend
computes the CPU usage from two samples.

Forgotten password: `sudo rm /var/lib/dahlia-dashboard/users.json` and
`sudo systemctl restart dahlia-dashboard` open the setup wizard with a new code.

### Apps (from milestone 6)

Apps are Docker containers built from app templates. The catalog lives in the repository
[dahlia-apps](https://github.com/Fabiy99/dahlia-apps) and is loaded from
`https://raw.githubusercontent.com/Fabiy99/dahlia-apps/main/`
(`/etc/dahlia/apps.json`, `file:///` also works for local catalogs).

A template (YAML) describes one app. From `schema: 2` on it is a small stack: the main
service `app` plus helpers such as a database or Redis. They are installed, updated and
removed together. Per service there is only: an image with a fixed version, LAN ports,
volumes under `appdata` or a share, environment variables, `command`, `user` and
`depends_on`. On top come category, description, notes, website and a login shown in the
dashboard. Everything else is rejected, including `privileged`, host networking, devices
and capabilities. `schema: 1` (one container) is still read.

Placeholders in environment, `command` and login: `{TZ}`, `{PUID}`, `{PGID}`,
`{HOSTNAME}`, `{DOMAIN}` and `{SECRET_<NAME>}`. Secrets are random values (32 hex
characters), made at install time and kept in `/var/lib/dahlia/apps/<id>/secrets.json`
(0600); updates keep them. The dashboard shows the login a template names under `login`.

| Topic | Implementation |
| --- | --- |
| Web port | Docker publishes it on `127.0.0.1:41000-41999`; nginx listens in the LAN on the template's public port (port link `http://<server>:<port>/`) and on 443 for `<id>.<domain>` and `<id>.local` |
| LAN ports | only if the template lists them; the dashboard shows them before installing |
| Data | `/mnt/user/appdata/<id>/...` (owner `dahlia-apps`), shares `/mnt/user/<name>`; no install without a mounted array |
| Start | Docker starts nothing by itself (`restart: no`); `dahlia-apps.service` starts the enabled apps after the disks |
| Containers | `no-new-privileges`, log driver `local` with 3 x 10 MB; main service `dahlia-<id>`, helpers `dahlia-<id>-<service>` |
| Stack | own Docker network per app, services reach each other by service name; helpers publish no ports |
| Catalog | `catalog.json` (all templates in one file), otherwise `index.json` and `apps/<id>.yaml`; templates with a newer schema are skipped |
| Updates | `dahlia-apps-catalog.timer` downloads the catalog every 6 hours; when an installed app's template differs, the dashboard shows "Update to <version>" and "Update all". Old images are deleted afterwards |

```sh
dahlia-apps catalog --refresh
dahlia-apps install jellyfin
dahlia-apps list
dahlia-apps updates                # apps with a newer template in the catalog
dahlia-apps update jellyfin
dahlia-apps remove jellyfin [--delete-data]
dahlia-apps check app.yaml        # validate a template
```

## Network and names (from milestone 7)

| Name | Path | Needs |
| --- | --- | --- |
| `https://<server>.local/` | dashboard, Avahi host name | nothing |
| `https://<domain>/` | dashboard | DNS record `<domain>` pointing at the server |
| `https://<app>.<domain>/` | app through nginx | DNS record `*.<domain>` pointing at the server |
| `https://<app>.local/` | app through nginx, mDNS alias | mDNS aliases switched on |
| `http://<server>.local:<port>/` | port link | nothing |

The domain defaults to `dahlia.home.arpa` (RFC 8375) and is stored in
`/etc/dahlia/domains.json`. Multi-level `.local` names (`jellyfin.dahlia.local`) are
rejected because mDNS clients do not resolve them reliably. Create the DNS records in the
local DNS, e.g. in AdGuard Home under Filters > DNS rewrites
(`*.dahlia.home.arpa` -> IP of the server). The dashboard shows the records with the
current address.

mDNS aliases: `dahlia-mdns.service` (unprivileged, user `dahlia-mdns`) publishes `<app>.local`
for every app with a web UI via `avahi-publish` on the primary IPv4 address. When the
address changes, the aliases follow within 10 seconds. An app named like the server gets no
alias.

Certificates (`dahlia-domains`, part of `dahlia-agent`):

| File | Content |
| --- | --- |
| `/etc/dahlia/tls/ca.key`, `ca.crt` | local CA, EC P-256, 20 years; key readable by root only |
| `/etc/dahlia/tls/server.key`, `server.crt` | server certificate for nginx, 825 days |

The CA carries name constraints (`nameConstraints`): it can only sign names under `.local`,
`home.arpa`, the configured domain and private IPv4 addresses (10/8, 172.16/12,
192.168/16). Importing it therefore does not make it trusted for any other website. The
server certificate holds `<server>.local`, `<domain>`, `*.<domain>`, the mDNS aliases and
the server's private IPv4 addresses. `dahlia-domains.timer` checks daily and after boot: a
new address, a new app or less than 30 days left lead to a new certificate. A domain outside
`home.arpa` needs a new CA; the dashboard then says that the CA must be imported again.

Importing the CA: download `dahlia-ca.crt` in the dashboard under Domains and compare the
SHA-256 fingerprint with the one shown after login (console or SSH). Then on Windows run
`certutil -addstore -f Root dahlia-ca.crt` (as administrator), on macOS use Keychain Access
(System, "Always Trust"), on Debian/Ubuntu copy it to
`/usr/local/share/ca-certificates/dahlia-ca.crt` and run `update-ca-certificates`; Firefox
uses its own store (Settings > Certificates > Import).

```sh
dahlia-domains show
dahlia-domains set --domain lab.home.arpa --mdns off
dahlia-domains apply              # check certificate and proxy, renew only when needed
```

## Updates

- Debian security updates via `unattended-upgrades`.
- Dahlia packages and other updates are applied from the UI, always with a snapshot first.
- The apt repository is built and signed in CI and published on GitHub Pages
  (`https://fabiy99.github.io/dahlia-linux/`). The private key exists only as a GitHub
  secret. Details: [apt-repo.md](apt-repo.md).
