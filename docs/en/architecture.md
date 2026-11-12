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

Rollback (from milestone 18): the dashboard lists the snapshots under Updates, takes new
ones, deletes them and resets the system to one. `snapper rollback` has no effect with
Debian's layout because fstab and GRUB mount `@rootfs` by name. So
`/usr/lib/dahlia/rollback.sh N` (as `dahlia-rollback.service`) does what `snapper-rollback`
does: a writable snapshot of `.snapshots/N/snapshot` next to the root, the running root
becomes `@rootfs.pre-rollback-<time>`, the new snapshot takes the name `@rootfs`,
`.snapshots` moves along, then a reboot. Carried over from the running system (reflink
copy): `/home`, `/root`, `/var/log`, `/etc/dahlia`, `/var/lib/dahlia`,
`/var/lib/dahlia-dashboard` and `/var/lib/docker`; `/srv` (`@srv`) and the array are not part
of it anyway. The old state stays until it is deleted in the dashboard
(`rollback.sh --delete-old NAME`).

On first boot, the dashboard only shows the setup wizard. It is protected by a one-time
code printed on tty1 and in `/etc/motd` (from milestone 5).

## Packages

| Package | Contents |
| --- | --- |
| `dahlia-base` | Configuration, group `dahlia` (GID 1000), pulls in all Dahlia packages. Masks `systemd-ssh-generator` (SSH over AF_VSOCK), which prints errors to the console on every `daemon-reload` under Hyper-V; SSH runs through plain `sshd` |
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
| `/mnt/remotes/<name>` | SMB and NFS shares of other devices (M24), only for app and backup folders |

SMB shares only use `/mnt/user`. Apps live on `/mnt/user` or, when chosen at install or
without an array, on the system SSD under `/srv` (see Apps). New files land on the cache
(`category.create=ff`); the mover moves them to `/mnt/storage` at night. The path under
`/mnt/user` does not change.

Disks are mounted via generated systemd mount units with `nofail` and
`x-systemd.device-timeout=15s`. If a disk is missing, the system still boots and reports
the failure.

SnapRAID runs through a wrapper on a timer: `diff`, abort above a delete threshold, `sync`,
weekly `scrub`. Mover and SnapRAID share a lock and never run at the same time.

### dahlia-disk (from milestone 3)

`dahlia-disk` from the `dahlia-storage` package implements the layout. `add` never
formats; only `format` creates a filesystem: on a blank disk, or on one whose data `import`
has copied to `/mnt/user` and verified. `import` skips `lost+found`,
`System Volume Information` and `$RECYCLE.BIN` at the top level. A disk without a readable
signature that is mounted anyway, or with several signatures, never counts as blank.
State lives in `/etc/dahlia/storage.json`; everything else is generated from it.

```sh
dahlia-disk list                          # disks and what Dahlia may do with them
dahlia-disk add <UUID> --role data        # existing ext4/XFS/Btrfs as /mnt/diskN
dahlia-disk add <UUID> --role parity      # as /mnt/parityN, never in a pool
dahlia-disk add <UUID> --role cache       # SSD filesystem as /mnt/cache (from M3b)
dahlia-disk add <UUID> --role external    # e.g. NTFS under /mnt/external/<name>
dahlia-disk remove <UUID>                 # unmount; data stays on the disk
dahlia-disk format /dev/sdX --fs xfs      # blank disk: GPT, one partition, XFS/ext4/Btrfs
dahlia-disk import <UUID> --to <name>     # mount read-only, rsync to /mnt/user/<name>, compare
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
dahlia-snapraid fix --disk N                   # rebuild the files of /mnt/diskN from parity, verify
dahlia-snapraid fix --bad                      # repair bad blocks found by a scrub
dahlia-snapraid status [--json]                # coverage, scrub state, errors, last runs
dahlia-disk set-option snapraid_delete_threshold 50
dahlia-disk set-option mover_min_age_minutes 1440
dahlia-disk set-minfreespace 100G               # free space per disk for mergerfs
dahlia-disk settings                           # all settings as JSON
```

The dashboard sets the same values under Disks > Settings (from milestone 17).

The mover leaves open files, recently modified files, files that already exist on the data
disks and hard links on the cache. It copies, verifies, renames and only then deletes.
Timers: mover 03:00, SnapRAID sync 04:30, scrub Sundays 06:00 (8 %, blocks older than
10 days). Mover and SnapRAID share `/run/dahlia/array.lock`.

Parity in the dashboard (from milestone 14): page "Parity" with state, disks, manual sync
and scrub, settings (delete threshold, scrub share and age) and the repairs. Result and
counts of the last runs are in `/var/lib/dahlia/snapraid.json`; `snapraid status` only runs
when the content file changed. A sync stopped by the delete threshold can run with
`--force` after a confirmation. A scrub that finds bad blocks ends with an error
(notification) and `fix --bad` repairs them.

Replacing a failed data disk: remove the old disk under Disks, format the new one and add it
as a data disk. It gets the lowest free number, i.e. that of the old one. Then
`fix --disk N`: `snapraid -d dN fix` (log in `/var/log/dahlia/snapraid-fix.log`) and
`snapraid -d dN -a check`. The state of the last sync is restored. Until then SnapRAID refuses
to sync because dN is missing or empty. Forced sync and repairs run as unit
`dahlia-parity.service`.

## Privileges and security

- `dahlia-dashboard` runs as `dahlia-ui` with systemd hardening and listens on
  `127.0.0.1` only. nginx terminates TLS on port 443.
- `dahlia-agent` runs as root and only accepts actions from its list. Every action
  validates its parameters.
- Docker publishes container ports on `127.0.0.1` only (`daemon.json`: `"ip"`). Apps are
  reached through nginx. Apps that need LAN ports are flagged in their app template.
- The firewall (nftables) opens ports only for enabled features and only for the local
  network, see [Firewall](#firewall-from-milestone-10).

### Firewall (from milestone 10)

`dahlia-agent` manages exactly one table, `inet dahlia`. It is generated from what is in
use and replaced as a whole in one transaction; other tables (Docker) are left alone.
`dahlia-firewall.service` loads it at boot before the network,
`/etc/network/if-up.d/dahlia-firewall` after every interface comes up, apps and shares
after every change.

- Incoming (`input`, policy drop): established connections, `lo`, ICMPv6, DHCP and ping
  from the LAN. New connections only from local sources: private IPv4 ranges
  (`10/8`, `172.16/12`, `192.168/16`, `100.64/10`, `169.254/16`), `fc00::/7`, `fe80::/10`,
  the subnets of the server's own interfaces (including the ISP's IPv6 prefix) and the
  extra networks entered by the admin (e.g. VPN).
- Open ports: 22 (SSH), 80/443 (nginx), 5353/udp (mDNS); 445, 5357 and 3702/udp only
  while shares exist; 2049 (NFS) while a share is exported over NFS; the port link and LAN ports of every installed app.
  Closed ports answer the LAN at once with `admin-prohibited`.
- Forwarded (`forward`): ports Docker hands to containers via DNAT, only for local sources.

Settings in `/etc/dahlia/firewall.json` (`enabled`, `networks`), in the dashboard under
Settings. The loaded ruleset is `/run/dahlia/firewall.nft`.

```
dahlia-firewall status     # settings, local networks, open ports (JSON)
dahlia-firewall show       # ruleset apply would load
dahlia-firewall apply      # reload
sudo dahlia-firewall disable   # escape hatch: remove the table
```

### Remote access: WireGuard and Tailscale (from milestone 20)

WireGuard: Dahlia runs the interface `dahlia-wg` with `wg-quick@dahlia-wg.service`.
Settings and devices are kept in `/etc/dahlia/wireguard.json` (0600), the server key in
`/etc/wireguard/dahlia-wg.key`; `/etc/wireguard/dahlia-wg.conf` is generated from both.
For a device the server makes a key pair and a preshared key; the dashboard shows its
configuration and QR code (`qrencode`) once, only the public key is kept. Added or removed
devices are loaded with `wg syncconf`, without dropping the other tunnels.

| Item | Implementation |
| --- | --- |
| Network | tunnel `10.253.0.0/24` (changeable while there are no devices, must not overlap a local network), server `.1`, devices from `.2` |
| Routing | "only the home network": tunnel and the server's IPv4 subnets; "all traffic": `0.0.0.0/0, ::/0`. DNS for clients optional, e.g. AdGuard Home |
| Firewall | UDP port (default 51820) open from the internet, masquerading of the tunnel network towards the LAN in `inet dahlia`, also with the firewall turned off. The tunnel counts as local (`10/8`) |
| Forwarding | `/etc/sysctl.d/60-dahlia-forward.conf` (`net.ipv4.ip_forward = 1`). Docker sets the iptables chain `FORWARD` to DROP; `/usr/lib/dahlia/wireguard-forward.sh` (PostUp/PostDown) allows the tunnel in `DOCKER-USER` |
| Router | forward the UDP port to the server; the address on the internet (dynamic DNS name or IP) is entered in the dashboard |

Tailscale is not in Debian. "Install" starts `dahlia-tailscale-install.service`
(`/usr/lib/dahlia/tailscale-install.sh`): key to
`/usr/share/keyrings/tailscale-archive-keyring.gpg`, source
`/etc/apt/sources.list.d/tailscale.list`, package `tailscale`, as in Tailscale's
instructions. "Connect" starts `tailscale up` as `dahlia-tailscale-up.service` (10 minutes to
log in); the dashboard reads the login link from `tailscale status --json`. Options: offer
the server's IPv4 subnets as a route (approve it in the Tailscale admin console) and exit
node. Tailscale addresses (`100.64/10`) count as local for the firewall.

Debian's `nftables.service` stays off; its `/etc/nftables.conf` starts with
`flush ruleset` and would delete Docker's rules as well.

### dahlia-agent (from milestone 4)

The agent listens on `/run/dahlia/agent.sock` (root:dahlia-agent, 0660) and is started by
`dahlia-agent.socket` on the first call. On top of the file permissions it checks the peer
via `SO_PEERCRED`: root and members of the group `dahlia-agent` are allowed, which
includes the dashboard user `dahlia-ui`.

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
| `history.get` | `range` (`hour`/`day`/`week`/`month`/`year`) | history from `/var/lib/dahlia/history.db`, averaged per step (1 min to 1 day) |
| `history.influx` | – | InfluxDB target without token/password, result of the last export |
| `history.set_influx` | `enabled`, `url`, `version` (1/2), `org`, `bucket`, `token` or `database`, `username`, `password` (left out = keep) | test write, then `/etc/dahlia/history.json` (0600) |
| `system.hardware` | - | mainboard (DMI), CPU temperature (hwmon), SMART per disk with `smartctl -n standby` (does not wake a sleeping disk), running and last self-test |
| `disks.smart_test` | `device`, `kind` (`short`/`long`/`abort`) | `smartctl -t short\|long` or `smartctl -X` |
| `disks.burnin_start` | `device`, `confirm` (disk name) | disk test as `dahlia-burnin@<name>.service`, blank disks only |
| `disks.burnin_status`, `disks.burnin_stop`, `disks.burnin_clear` | for `stop`/`clear`: `device` | progress and result, abort, forget the result |
| `system.log` | `lines`, `priority`, `unit` (optional) | `journalctl -n … -p …` |
| `system.diagnostics` | – | Diagnostics bundle to `/run/dahlia-dashboard/diagnostics.tar.gz` (0600, dashboard user) |
| `settings.time` / `settings.set_time` | `timezone`, `ntp` | `timedatectl set-timezone`, `set-ntp` |
| `settings.network` / `settings.set_network` | `mode` (`dhcp`/`static`), `address` (CIDR), `gateway`, `dns` | `/etc/network/interfaces`, applied by `dahlia-network` with rollback |
| `settings.confirm_network` | – | confirms the new network settings |
| `settings.spindown` / `settings.set_spindown` | `default`, `disks` (serial → minutes) | `/etc/dahlia/spindown.json`, `hdparm -S` |
| `power.get` / `power.set_governor` | `governor` (one the kernel offers) | cpufreq in `/sys`, kept over reboots with `/etc/tmpfiles.d/dahlia-cpu-governor.conf` |
| `system.devices` | - | PCI (`lspci -vmm -nn -k`: class, IDs, driver, IOMMU group) and USB (`lsusb`) |
| `vpn.status` | - | WireGuard (settings, devices with last handshake and traffic) and Tailscale (installed, state, login link, addresses) |
| `vpn.wireguard_set` | `enabled`, `port`, `endpoint`, `route` (`lan`/`all`), `dns`, `subnet` | writes the configuration, loads firewall and interface |
| `vpn.wireguard_add_peer`, `vpn.wireguard_remove_peer` | `name` | new device (configuration and QR code once) or remove it |
| `vpn.tailscale_install`, `vpn.tailscale_up`, `vpn.tailscale_logout` | for `up`: `routes`, `exit_node` | install Tailscale, connect, log out |
| `settings.firewall` / `settings.set_firewall` | `enabled`, `networks` (CIDR list, at most 20) | `/etc/dahlia/firewall.json`, `nft -c -f` and `nft -f` with `/run/dahlia/firewall.nft` |
| `settings.schedules` / `settings.set_schedule` | `job`, `frequency`, `time`, `weekday`, `monthday` | drop-in `OnCalendar=` for the timers |
| `shares.list` | – | shares, SMB users, whether `/mnt/user` is mounted, whether `smbd` and `nfs-server` run |
| `shares.set` / `shares.remove` | `name`, `comment`, `security` (`public`/`secure`/`private`), `users` (name → `rw`/`ro`), `time_machine`, `time_machine_size` (GB, 0 = no limit), `nfs` | generates `/etc/samba/smb.conf`, checks it with `testparm`, starts `smbd`; with `nfs` `/etc/exports.d/dahlia.exports`, `exportfs -ra`, `nfs-server` |
| `shares.user_add` / `shares.user_password` / `shares.user_remove` | `name`, `password` | `useradd`/`userdel` (groups `dahlia`, `dahlia-smb`), `smbpasswd` via stdin |
| `notify.get` | – | channels without secrets, switches, recent notifications |
| `notify.set` | `channel` (`email`/`ntfy`/`gotify`), `settings` | `/etc/dahlia/notify.json` (0600); an empty password/token keeps the old one |
| `notify.set_events` / `notify.test` | `events` (name → bool) | notification types on/off, test message to all enabled channels |
| `ups.get` / `ups.set` | `mode` (`off`/`usb`/`remote`), `host`, `port`, `name`, `user`, `password`, `shutdown_minutes` (0 to 120) | generates `/etc/nut/*`, `upsc` for the state |
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
| `storage.settings` | – | `dahlia-disk settings` |
| `storage.set_minfreespace` | `size` (e.g. `100G`, M/G/T) | `dahlia-disk set-minfreespace` |
| `storage.format` | `device` (whole disk), `filesystem` (`xfs`, `ext4`, `btrfs`), `confirm` (disk name repeated) | `dahlia-disk format`, blank or imported disks only |
| `storage.import` / `storage.import_status` | `uuid`, `target` (name below `/mnt/user`) | `dahlia-disk import` as unit `dahlia-import`, progress in `/run/dahlia/import.json` |
| `job.start` | `job` (mover, snapraid-sync, snapraid-scrub, backup) | starts the unit, returns at once |
| `job.status` | `job` (plus `update`) | State and last result |
| `parity.get` | – | `dahlia-snapraid status --json` and the state of sync, scrub, mover and repair |
| `parity.sync` | `force` (optional) | start a sync; with `force` without the delete threshold as `dahlia-parity.service` |
| `parity.fix` | `what` (`bad` or `diskN`) | `dahlia-snapraid fix --bad` or `--disk N` as `dahlia-parity.service` |
| `updates.check` | - | `apt-get update`, list of upgrades |
| `updates.apply` | - | `apt-get full-upgrade` through the unit `dahlia-update.service`; the planned packages (simulation) in `/run/dahlia/update.json` |
| `updates.auto` | - | Switch and reboot settings, last unattended-upgrades run, pending restart |
| `snapshots.list` | – | `snapper --jsonout list`, `btrfs subvolume list /`, `/var/lib/dahlia/rollback.json` |
| `snapshots.create` | `description` | `snapper create --cleanup-algorithm number` |
| `snapshots.delete` | `number` | `snapper delete` |
| `snapshots.rollback` | `number` | `dahlia-rollback.service` → `rollback.sh N --reboot` |
| `snapshots.delete_old_root` | `name` (`<subvol>.pre-rollback-<time>`) | `rollback.sh --delete-old` |
| `updates.set_auto` | `enabled`, `reboot`, `reboot_time` (`HH:MM`) | `20auto-upgrades`, `52dahlia-auto-reboot`, `/etc/dahlia/updates.json` |
| `updates.progress` | - | state, percent, per package waiting/downloaded/setting up/done from the apt output in this run's journal; shown on the dashboard's update page |
| `apps.catalog` | `refresh` (optional) | App catalog and its age, `refresh` downloads it first |
| `apps.list` | - | installed apps with container state, version, available update |
| `apps.install`, `apps.update` | `id`, for `install` `location` (optional, `/mnt/user` or `/srv`) and `folders` (optional, share -> folder below `/mnt/user` or `/srv`) | in the background as unit `dahlia-app-<id>`; folders are kept on updates |
| `apps.custom_install`, `apps.custom_update` | `id`, `spec` (form, see Apps), for `install` `location` | custom container; the agent writes `spec` to `/run/dahlia/apps-custom/<id>.json`, `dahlia-apps` checks every field |
| `apps.job` | `id` | state of the last install or update |
| `apps.start`, `apps.stop` | `id` | start or stop, also for the next boot |
| `apps.remove` | `id`, `delete_data` (optional) | remove the container, data only on request |
| `remotes.list` | – | mounted shares of other devices with state (`mounted`, `idle`, `error`, `off`), never the password |
| `remotes.set` | `name`, `type` (`smb`/`nfs`), `host`, `share`, `username`, `password` (left out = keep), `readonly` | test mount, then `/etc/dahlia/remotes.json`, credentials `/etc/dahlia/remotes/<name>.cred` (0600), `.mount` and `.automount` unit |
| `remotes.remove` | `name`, `force` | delete units and credentials; without `force` refused while an app uses a folder on it |
| `files.browse` | `root` (`/mnt/user`, `/srv` or `/mnt/remotes`), `path` (relative to it) | subfolders for the folder picker, no hidden folders or symlinks, at most 500; plus which locations can be used |
| `files.mkdir` | `root`, `path` | create a folder (group `dahlia`, 2775), not below `appdata` |
| `domains.get` | - | mDNS aliases, names in the certificates, certificates |
| `domains.set` | `mdns_aliases` | `dahlia-domains set`, new app certificate and proxy |
| `backup.get` | – | Target, last result, public SSH key, possible target disks (`/mnt/diskN`, `/mnt/cache`, `/mnt/user`, `/media/…`) |
| `backup.set` | `target` (`{"type": "folder", "path": …}` or `{"type": "sftp", "host", "port", "user", "path"}`), `stop_apps`, `folders` (optional, more folders below `/mnt/user` or `/srv`) | `/etc/dahlia/backup.json`, creates password and SSH key, enables `dahlia-backup.timer` |
| `backup.key` | – | Repository password |
| `backup.snapshots` | – | `restic snapshots` |
| `backup.restore` | `snapshot`, `what` (`config` or an app id) | in the background as unit `dahlia-backup-restore` |
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
| Second factor | optional TOTP (RFC 6238, SHA-1, 6 digits, 30 s, ±1 step), each code once; 10 recovery codes (SHA-256, each once); QR code via `qrencode` |
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
| Disks | usage of `/mnt/user`, `/mnt/storage` and `/mnt/cache`; one card per physical disk with model, serial number, temperature, SMART state, last self-test (start a short/long test), disk test for blank disks and its filesystems (status, usage, add in a role, remove) |
| Apps | installed apps as cards (state, addresses, updates, start/stop), catalog with search and categories, custom containers |
| Docker | all containers (also foreign ones) with state, ports, CPU/RAM, start/stop/restart, logs; disk usage and cleanup |
| Addresses | mDNS aliases on/off, CA download and certificates |
| Jobs | start mover, SnapRAID sync and scrub, state |
| System log | journal filtered by level and service |
| Updates | check, install |
| Shares | SMB and NFS shares on `/mnt/user`, Time Machine, SMB users |
| Settings | time and time zone, network (DHCP/static), disk spindown, schedules for mover, SnapRAID, backup and SMART self-tests, notifications, UPS, remote access (WireGuard, Tailscale), dashboard password, server name |

SMART self-tests and disk test (M23):

- Self-tests run inside the disk; it stays usable meanwhile. `dahlia-smart-short.timer`
  (weekly, Saturday 05:00) and `dahlia-smart-long.timer` (off by default, a long test of a
  large HDD takes a day) start `dahlia-smart-test@short|long.service`, which starts the test on
  every disk. Times under Settings → Schedules. A short test wakes sleeping disks.
- Result from the self-test log (ATA and NVMe): running with percent left, last test with
  kind, result and power-on hours. A failed test is sent as a critical notification.
- Disk test before use: only for disks without partition and filesystem (status `empty`),
  `confirm` repeats the name. `/usr/lib/dahlia/disk-burnin.sh` reads SMART, writes four
  patterns over the whole disk with `badblocks -w` and reads them back, reads SMART again.
  Passed means: no bad blocks, no SMART counter grew. Files under `/var/lib/dahlia/burnin`.
  While it runs the disk cannot be formatted.

Notifications and UPS (M9d):

- Channels: email (SMTP with STARTTLS or TLS), ntfy, Gotify. Credentials live in
  `/etc/dahlia/notify.json` (root, 0600); the dashboard only sees whether one is set.
- `dahlia-notify.timer` checks SMART (also a failed self-test), disk temperature (from 55 °C HDD, 70 °C SSD) and
  usage (from 90 %, critical from 97 %) every 10 minutes. Only changes are sent, a fixed
  problem as "Resolved". State in `/var/lib/dahlia/notify-state.json`.
- Mover, SnapRAID sync/scrub and update have `OnFailure=dahlia-notify-failure@%n`;
  the message holds the last 15 journal lines.
- UPS through NUT: USB (`usbhid-ups`, `upsd` on localhost only) or a NUT server in the
  network (`netclient`). `upsmon` calls `dahlia-notify ups-event` for every event. On
  battery a timer `dahlia-ups-shutdown` powers off after the set time; back on mains it
  is stopped. Without a time `upsmon` shuts down on low battery.

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
- Time Machine: `fruit:time machine` per share, optionally with a size limit
  (`fruit:time machine max size`). Several Macs on one share share the limit.

NFS shares (M21):

- A public or secure share can also be exported over NFS: public read-write, secure
  read-only. Private shares never, since NFS has no login without Kerberos.
- NFSv4 only (`/etc/nfs.conf.d/dahlia.conf`: `vers3=n`, `udp=n`), so 2049/tcp is enough;
  rpcbind and mountd stay behind the firewall.
- `/etc/exports.d/dahlia.exports` is generated from `shares.json`. Allowed are the same
  networks as in the firewall: the private ranges and the extra networks entered there (a
  change there rewrites the exports). Options: `all_squash`, `anonuid=65534` (nobody),
  `anongid` = group `dahlia`, `sync`, `no_subtree_check` and a fixed `fsid` derived from the
  name, because nfsd has no device number for mergerfs (FUSE). mergerfs already runs with
  `inodecalc=path-hash` and `noforget` for this.
- `nfs-server` runs only while a share is exported over NFS.
- Mounting on Linux: `mount -t nfs4 dahlia.local:/mnt/user/<name> /mnt/target`.

Shares of other devices (M24):

- Shares → *Shares of other devices* mounts SMB shares (NAS, Windows) and NFS exports. They
  live under `/mnt/remotes/<name>` and show up in the folder picker as a location of their
  own, e.g. for an app's media folders. App data (`appdata`) never goes there.
- Before saving, the agent test-mounts the share once on `/run/dahlia/remote-test`; a wrong
  password or path is reported right away and nothing is saved.
- Like the disks, no `/etc/fstab`: one `.mount` and one `.automount` unit per share in
  `/etc/systemd/system` with `_netdev,nofail` and a 30 s timeout. A device that is off does
  not hold up the boot; the share is mounted on first access.
- SMB: user and password in `/etc/dahlia/remotes/<name>.cred` (root, 0600), guest without a
  user. Files belong to `dahlia-apps:dahlia` (0664/0775, `noperm`) so apps can write. NFS:
  `soft,timeo=300,retrans=4`, owners come from the other device.
- Optionally read-only (`ro`). A share can only be removed once no app uses a folder on it,
  or explicitly anyway. Files on the other device are never touched.

History and InfluxDB (M25):

- `dahlia-history.service` (`/usr/sbin/dahlia-history run`) samples CPU usage, used memory,
  load, CPU temperature (hwmon, usually none in VMs) and the data rate of each physical network
  interface every full minute. No disk temperatures, so sleeping disks stay asleep.
- Storage: SQLite in `/var/lib/dahlia/history.db`. Minute values for two days, 15-minute
  averages for 35 days, hourly averages for 400 days; the averages are updated with every
  sample. The file stays at a few MB.
- The overview shows the history for hour, day, week, month and year as plain SVG lines,
  without a chart library.
- Optionally the service writes every sample to an InfluxDB using line protocol (1.x with a
  database, 2.x with organization, bucket and token), measurements `dahlia` and `dahlia_net`
  (tag `interface`), both tagged `host`. A test value is sent before saving; the result of the
  last export is in `/var/lib/dahlia/history-influx.json` and in the settings.
- The service runs with `ProtectSystem=strict` and may only write to `/var/lib/dahlia`.

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

Two-factor login (M22): set up under Settings with the password, scan the QR code, confirm
with a code. Only then is it on; other logged-in browsers are logged out. At login the form
asks for the code only once the password is right. Wrong codes count as failed attempts.
Turning it off needs password and code. Phone and recovery codes lost:
`sudo dahlia-dashboard --disable-totp <account>`.

### Terminal (from milestone 12)

The dashboard's Terminal page is a shell in the browser (xterm.js, MIT, in
`www/vendor/xterm`, canvas renderer because of the CSP). Flow:

1. Browser → `wss://<server>/api/terminal` (nginx with upgrade headers). The backend checks
   the dashboard session and that `Origin` is this server.
2. Backend → Unix socket `/run/dahlia-terminal/terminal.sock` (group `dahlia-ui`, 0660).
3. `dahlia-terminal.service` runs as `dahlia-term` without privileges and starts
   `su -l <user>` in a pseudo-terminal per session. `su` asks for the Linux password; root
   is then available with `sudo` as over SSH. Only accounts with UID 1000 or higher and a
   login shell are accepted, `dahlia-admin` by default.

At most 4 sessions, disconnect after 15 minutes without input. Start and end of each
session (Linux account, dashboard account, IP) are logged in the journal of
`dahlia-terminal`. An update of `dahlia-dashboard` restarts the backend and ends open
sessions; run package updates from the update page or inside `tmux`/`screen`.

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
dashboard. Everything else is rejected, including `privileged`, host networking, arbitrary
devices and capabilities. `schema: 1` (one container) is still read.

GPU (from milestone 11): `schema: 3` also allows `gpu: true` on a service. If the server has
a render node (`/dev/dri/renderD*`, Intel or AMD), the container gets `/dev/dri` and the
groups `render` and `video`, e.g. for VA-API/Quick Sync in Jellyfin or Immich. Without a GPU
the app runs on the CPU. NVIDIA needs NVIDIA's container toolkit and is not supported. Older
Dahlia versions skip `schema: 3` templates. `system.hardware` reports the graphics devices
(`gpus`).

Placeholders in environment, `command` and login: `{TZ}`, `{PUID}`, `{PGID}`,
`{HOSTNAME}`, `{DOMAIN}` and `{SECRET_<NAME>}`. Secrets are random values (32 hex
characters), made at install time and kept in `/var/lib/dahlia/apps/<id>/secrets.json`
(0600); updates keep them. The dashboard shows the login a template names under `login`.

| Topic | Implementation |
| --- | --- |
| Web port | Docker publishes it on `127.0.0.1:41000-41999`; nginx listens in the LAN on the template's public port (port link `http://<server>:<port>/`) and, with an mDNS alias, on 443 for `<id>.local` |
| LAN ports | only if the template lists them; the dashboard shows them before installing |
| Location | array `/mnt/user` or system SSD `/srv` (FHS: data served by this system). Chosen at install, `/srv` when there is no array. Apps from before 0.7 are on the array |
| System SSD | On Btrfs, `/srv` is its own top-level subvolume `@srv` as on openSUSE, mounted by `srv.mount` (created by `/usr/lib/dahlia/system-data.sh` in the postinst). It is not part of the snapper snapshots of `/`: a rollback neither resets nor loses app data. On other filesystems a plain directory |
| Data | `<location>/appdata/<id>/...` (owner `dahlia-apps`), shares `<location>/<name>`; apps on the array only start with the array mounted |
| Setup | Before installing, the dashboard shows the requirements and the app's shares. You pick the location; "Change" opens a folder tree on the system SSD or the array (with "New folder"), mixing both is fine; the path inside the container stays the same |
| Start | Docker starts nothing by itself (`restart: no`); `dahlia-apps.service` starts the enabled apps after the disks |
| Containers | `no-new-privileges`, log driver `local` with 3 x 10 MB; main service `dahlia-<id>`, helpers `dahlia-<id>-<service>` |
| Stack | own Docker network per app, services reach each other by service name; helpers publish no ports |
| Catalog | `catalog.json` (all templates in one file), otherwise `index.json` and `apps/<id>.yaml`; templates with a newer schema are skipped |
| Logos | `catalog.json` lists `icons/<id>.svg` or `.png` with its SHA-256 per app under `icons`. The agent downloads only changed logos, checks hash, size (64 KB) and file type and stores them in `/var/lib/dahlia/apps-catalog/icons/`; nginx serves them as `/app-icons/` with a `sandbox` CSP. Without a logo the dashboard shows the first letter |
| Updates | `dahlia-apps-catalog.timer` downloads the catalog every 6 hours; when an installed app's template differs, the dashboard shows "Update to <version>" and "Update all". Old images are deleted afterwards |

```sh
dahlia-apps catalog --refresh
dahlia-apps install jellyfin [--location /srv] [--folder media=/mnt/user/Movies/Shows]
dahlia-apps list
dahlia-apps updates                # apps with a newer template in the catalog
dahlia-apps update jellyfin
dahlia-apps remove jellyfin [--delete-data]
dahlia-apps check app.yaml        # validate a template
dahlia-apps install kuma --spec kuma.json [--location /srv]   # custom container
dahlia-apps update kuma [--spec kuma.json]
```

Custom containers (from milestone 19): for an image that is not in the catalog, Apps has
"Custom container". The form asks for ID, name, image with a fixed version, optionally web
port and network port, more LAN ports, folders (app data below `<location>/appdata/<id>/` or
a folder from the folder dialog), environment variables with the placeholders above and GPU.
`dahlia-apps` builds a template with only the service `app` from it and checks it like any
catalog template, so there is no `privileged`, no host network and no free devices here
either. Folders become the shares `folder1`, `folder2` and so on. The form is kept in
`/var/lib/dahlia/apps/<id>/custom.json` (0600), `state.json` has `"custom": true`. Custom
containers never take a template from the catalog, even if an app with the same ID shows up
there later; an ID from the catalog is refused at install. "Edit" changes the form and
reinstalls (data and secrets stay), "Update" pulls the image again.

## Backup (from milestone 13)

`dahlia-backup` uses restic to save what is needed to restore a server:

- `/etc/dahlia`
- `/var/lib/dahlia/apps` (templates, state and secrets of the installed apps)
- `/var/lib/dahlia-dashboard` (dashboard accounts)
- `<location>/appdata/<id>` of every installed app
- more folders from `folders` in `/etc/dahlia/backup.json`

Shares with films or photos are not included. Parity and a second copy cover those.

The target is a folder `dahlia-backup` on another disk (safest: a USB disk under `/media`)
or an SFTP server. For SFTP Dahlia creates `/etc/dahlia/backup_ed25519`; the dashboard
shows the public key, which goes into `~/.ssh/authorized_keys` on the server. The
repository is encrypted with a random password from `/etc/dahlia/backup.pass` (0600).
Nothing can be restored without it, so the dashboard shows it for safe keeping.

Running apps are stopped for the backup and started again afterwards so that databases are
consistent (can be turned off). Then restic keeps 7 daily, 4 weekly and 6 monthly
snapshots. `dahlia-backup.timer` runs daily at 02:00 and is only enabled once a target is
saved; the time is under Settings > Schedules. Failures go to the notifications like the
other jobs.

Restore works per app (containers stopped, `appdata` replaced, started again) or for the
configuration (`/etc/dahlia` and the app list).

```sh
sudo dahlia-backup run
sudo dahlia-backup snapshots
sudo dahlia-backup restore 4f2a9c1d paperless
sudo dahlia-backup restore 4f2a9c1d config
```

On a new server: install Dahlia, write the old password to `/etc/dahlia/backup.pass` (0600),
enter the same target in the dashboard and restore `config`, then restore the apps one by one
and start them under Apps.

## Network and names (from milestone 7)

| Name | Path | Needs |
| --- | --- | --- |
| `https://<server>.local/` | dashboard, Avahi host name | nothing |
| `https://<app>.local/` | app through nginx, mDNS alias | mDNS aliases switched on |
| `http://<server>.local:<port>/` | port link | nothing |

Dahlia needs no DNS server or domain of its own (up to 1.0 there was
`*.dahlia.home.arpa`; since 1.1 there is not, an old `domain` entry in
`/etc/dahlia/domains.json` is ignored). There are no multi-level `.local` names
(`jellyfin.dahlia.local`) because mDNS clients do not resolve them reliably. The placeholder
`{DOMAIN}` in app templates stands for `local`, so `https://<app>.{DOMAIN}` is the app's mDNS
alias.

mDNS aliases: `dahlia-mdns.service` (unprivileged, user `dahlia-mdns`) publishes `<app>.local`
for every app with a web UI via `avahi-publish` on the primary IPv4 address. When the
address changes, the aliases follow within 10 seconds. An app named like the server gets no
alias.

Certificates (`dahlia-domains`, part of `dahlia-agent`):

| File | Content |
| --- | --- |
| `/etc/dahlia/tls/ca.key`, `ca.crt` | local CA, EC P-256, 20 years; key readable by root only |
| `/etc/dahlia/tls/server.key`, `server.crt` | certificate of the server (dashboard), 825 days |
| `/etc/dahlia/tls/apps.key`, `apps.crt` | certificate of the app proxies, 825 days |

The CA carries name constraints (`nameConstraints`): it can only sign names under `.local`,
and private IPv4 addresses (10/8, 172.16/12, 192.168/16) (CAs from before 1.1 also
`home.arpa`; they are kept so nobody has to import again). Importing it therefore does not
make it trusted for any other website. The server certificate holds `<server>.local` and
the server's private IPv4 addresses, the app certificate the mDNS aliases. Without aliases
the app proxy listens on the port link only. Installing or
removing an app therefore only changes the app certificate; a browser that accepted the
server certificate once does not lose the dashboard. `dahlia-domains.timer` checks daily and
after boot: a new address, a new app or less than 30 days left lead to a new certificate.

Importing the CA: download `dahlia-ca.crt` in the dashboard under Addresses and compare the
SHA-256 fingerprint with the one shown after login (console or SSH). Then on Windows run
`certutil -addstore -f Root dahlia-ca.crt` (as administrator), on macOS use Keychain Access
(System, "Always Trust"), on Debian/Ubuntu copy it to
`/usr/local/share/ca-certificates/dahlia-ca.crt` and run `update-ca-certificates`; Firefox
uses its own store (Settings > Certificates > Import).

```sh
dahlia-domains show
dahlia-domains set --mdns off     # port links only
dahlia-domains apply              # check certificate and proxy, renew only when needed
```

## Power saving and devices (from milestone 28)

Settings > Power saving sets the CPU governor on all cpufreq policies
(`/sys/devices/system/cpu/cpufreq/policy*/scaling_governor`). To keep it after a reboot the
agent writes a `w` line to `/etc/tmpfiles.d/dahlia-cpu-governor.conf`; systemd-tmpfiles sets
the value at boot. Only governors the kernel lists in `scaling_available_governors` can be
chosen (with `intel_pstate` usually just `powersave` and `performance`). Without cpufreq, as
in most VMs, the card only shows a note.

Settings > Devices lists PCI and USB devices (`pciutils`, `usbutils`) with driver and IOMMU
group. The group matters for passing devices to VMs from version 2; without an active IOMMU
(VT-d/AMD-Vi in the UEFI) there are no groups.

## Diagnostics bundle (from milestone 16)

For bug reports: System log > "Download diagnostics bundle". The agent collects versions
(`dpkg-query`, `uname`, `/etc/os-release`), services and timers, the journal of the current
boot (warnings, `dahlia*`, kernel) and errors of the previous one, disks (`lsblk`,
`findmnt`, `df`, `dahlia-disk list`, parity status, `/etc/snapraid.conf`), network (`ip`,
`nft list ruleset`, `ss -tulpn`), Docker (`ps`, `info`, `system df`), the tails of the apt
and unattended-upgrades logs and each app's `state.json` and `app.yaml`. Left out: the
apps' `compose.json` and `secrets.json`, `/etc/dahlia/backup.pass`, SSH and TLS keys; in
JSON settings, keys such as `password`, `token` or `key` are replaced by `***`. The archive
is placed in the dashboard's runtime directory and deleted after the first download.

## Updates

- Debian security updates via `unattended-upgrades` (from milestone 15), on by default.
  `dahlia-base` ships `/etc/apt/apt.conf.d/51dahlia-unattended-upgrades`: only
  `${distro_codename}-security`, old kernels are removed. The switch is Debian's
  `20auto-upgrades`; the automatic reboot (off, time selectable) is in
  `52dahlia-auto-reboot`; the dashboard sets both under Updates. `apt-daily-upgrade.timer`
  runs it (daily around 06:00); snapper takes snapshots as for every apt run. The dashboard
  shows the last run from `/var/log/unattended-upgrades/unattended-upgrades.log` and a
  pending restart (`/run/reboot-required`). Without the automatic reboot `dahlia-notify`
  reports it once (event `updates`).
- Dahlia packages and other updates are applied from the UI, always with a snapshot first.
- The apt repository is built and signed in CI and published on GitHub Pages
  (`https://fabiy99.github.io/dahlia-linux/`). The private key exists only as a GitHub
  secret. Details: [apt-repo.md](apt-repo.md).
