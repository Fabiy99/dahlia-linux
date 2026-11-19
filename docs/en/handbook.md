# Dahlia Linux – Handbook

This handbook covers installation and day-to-day use. How Dahlia is built inside is described in
the [architecture](architecture.md). Deutsch: [Handbuch](../de/handbuch.md).

## Contents

1. [What you need](#what-you-need)
2. [Making a USB stick](#making-a-usb-stick)
3. [Installing](#installing)
4. [First setup](#first-setup)
5. [Desktop app, browser and phone](#desktop-app-browser-and-phone)
6. [Start screen](#start-screen)
7. [Disks and array](#disks-and-array)
8. [Parity](#parity)
9. [Shares](#shares)
10. [Files](#files)
11. [Apps](#apps)
12. [VMs and containers](#vms-and-containers)
13. [Backup](#backup)
14. [Remote access](#remote-access)
15. [Updates and snapshots](#updates-and-snapshots)
16. [Notifications and weekly letter](#notifications-and-weekly-letter)
17. [Terminal, Docker and log](#terminal-docker-and-log)
18. [When something does not work](#when-something-does-not-work)

## What you need

- A PC with a 64-bit processor (x86_64) and at least 2 GB of RAM, much more for VMs.
- An SSD of 20 GB or more for the system. It is erased during installation.
- Any number of data disks. Disks with data (ext4, XFS, Btrfs) are taken over without
  formatting.
- A network cable and internet access during installation (DHCP).
- A USB stick of 2 GB or more for the installer.

For VMs, virtualisation must be enabled in the BIOS (Intel VT-x or AMD-V/SVM).

## Making a USB stick

**Windows:** install the desktop app from the [latest release](https://github.com/Fabiy99/dahlia-linux/releases/latest)
(`Dahlia-<version>-setup.exe`) and start it. At the bottom of the first page there is
"Make a USB stick":

1. "Download" saves the ISO of the latest release to the Downloads folder and checks it against
   the release's `.sha256` file. An ISO you already have can be used with "Choose ISO file".
2. Choose the USB stick. Only drives on the USB bus are offered.
3. "Write to the stick": Windows asks for administrator rights. Dahlia erases the stick, writes
   the ISO and reads it back to compare.

**Linux and macOS:** download the ISO from the release and write it with a tool such as
balenaEtcher, or on Linux:

```sh
sha256sum -c dahlia-linux-<version>-amd64.iso.sha256
sudo dd if=dahlia-linux-<version>-amd64.iso of=/dev/sdX bs=4M conv=fsync status=progress
```

`/dev/sdX` is the stick, not a disk with data (`lsblk` shows size and model).

## Installing

Boot the PC from the stick (boot menu usually F11, F12 or Esc) and choose "Install Dahlia Linux".
The installer is in English and asks for:

1. Keyboard layout
2. Account name and password. This account logs in on the console, over SSH and in the
   dashboard's terminal. The name is also suggested for the dashboard account.
3. System disk. The stick is never offered.
4. Server name (default `dahlia`, reachable as `dahlia.local`)
5. Time zone
6. Network: DHCP or a static address
7. Optionally an SSH key
8. Summary. The system disk is erased only after "Yes".

After the reboot, a screen on the server shows the dahlia, the address, a QR code and the setup
code. Servers without a screen: choose "serial console" in the boot menu.

"Automatic install" is meant for tests: no questions, account `dahlia-admin` with the initial
password `dahlia`. With a single disk, that disk is erased without asking.

## First setup

1. Open `https://dahlia.local/` in a browser (or the IP address shown on the server's screen).
2. Accept the certificate warning. The server has its own local certificate authority. It can be
   downloaded under Settings > Addresses and imported into the operating system; then the
   warning goes away.
3. Enter the setup code. It is on the server's screen and in the SSH login message.
4. Create the dashboard account: name (the installer's account is suggested) and password.

The start screen follows, and on its first start the app shows a short tour. Restart the tour
any time from the account menu at the top right.

## Desktop app, browser and phone

**Desktop app** (Windows, macOS, Linux, from the release): it finds servers on the network by
itself. On the first connection it shows the fingerprint of the local CA; it must match the one
under Settings > Addresses. The app always has the same version as the server and updates itself
when the server is newer.

**Browser:** the dashboard at `https://dahlia.local/` offers the same. It is the emergency access
in case the app does not start.

**Phone:** "Phone" at the top right shows a QR code. The phone opens the dashboard in its browser
with it; "Add to Home Screen" keeps it like an app. "Phone" also offers a pairing code to pair
the desktop app again.

Two-factor login (TOTP): Settings > Security.

## Start screen

Tiles: greeting with the state of the server, weather for several places, apps, storage, system,
recently finished jobs, weekly letter and content from apps. "Customize" switches tiles on and
off and changes size, order, colour scheme, accent colour and background. The layout is kept
across updates.

**Content from apps** (off until switched on under "Customize"):

| Tile | App | Setup |
| --- | --- | --- |
| Photos | Immich | API key from Immich (Account settings > API keys) |
| New in Jellyfin | Jellyfin | API key from Jellyfin (Dashboard > API keys) |
| Downloads | qBittorrent or Transmission | qBittorrent: user and password of the web UI; Transmission: nothing |

Keys are entered in the tile itself. The server asks the apps on its own machine; nothing leaves
the server.

## Disks and array

"Disks" lists all drives with size, temperature and SMART values.

- **Data disks** are mounted one by one at `/mnt/disk1`, `/mnt/disk2` … A disk with data is
  taken over without formatting. Empty disks are formatted on request (XFS, ext4 or Btrfs).
- **Cache:** one or two SSDs at `/mnt/cache` (Btrfs, RAID1 with two SSDs). New files land there
  first; the **mover** moves them to the data disks at night. Paths stay the same.
- **Pool:** `/mnt/user` combines cache and data disks. Shares and apps use this path.
  `/mnt/storage` shows only the data disks.
- **Parity:** see below.

Before using a new disk, run the disk test (burn-in): it writes and reads the whole disk and
takes a day or longer depending on the size. Disks with data on other file systems (NTFS and
exFAT too) are copied to `/mnt/user` by the import, which checks the copy before the disk can be
formatted.

Encryption (LUKS) can be chosen when formatting. The key is stored on the server or entered in
the dashboard after each boot.

## Parity

Parity protects against the failure of single data disks (SnapRAID). The parity disk must be at
least as large as the largest data disk; two parity disks survive two failures.

- **Sync** recomputes the parity, by default every night.
- **Scrub** checks part of the data against the parity, by default weekly.
- **Restore a disk:** fit a replacement, choose the failed disk and the new one under Parity.
  Dahlia rebuilds the state of the last sync.

Parity is not a backup: deleted files are gone there too after the next sync.

## Shares

Shares make folders from `/mnt/user` available on the network.

- **SMB** (Windows, macOS, Linux): public (no password), protected (everyone reads, SMB users
  write) or private (only chosen SMB users). SMB users are created under Shares; they are separate
  from the dashboard account.
- **Time Machine** for Macs: an option on a share.
- **NFS** for Linux machines and other servers.
- **Shares of other devices** (NAS, PC) are mounted under `/mnt/remotes`; apps can use them.

Windows: `\\dahlia.local\<share>` in Explorer. macOS: Finder > Go > Connect to Server,
`smb://dahlia.local/<share>`.

## Files

The file manager shows the pool, the system SSD and mounted shares: create folders, search,
upload and download, copy, move, rename, delete. Copy the path of a folder with one click, for
example for an app.

## Apps

"Apps" holds the catalogue with more than 100 templates. Installing asks for:

- **Location** of the app data: system SSD (`/srv/appdata`) or pool (`/mnt/user/appdata`).
  Without data disks only the SSD is available.
- **Folders** for media, photos or documents, chosen in the folder tree.

The app is then reachable through a port link and as `<app>.local`. A graphics card (`/dev/dri`)
for transcoding can be given to apps such as Jellyfin. Containers without a template go through
"Custom container".

**Updates:** when an update is available, the card shows "Update". Before updating, Dahlia copies
the app's data and configuration (`appdata/.rollback/<app>`) if there is enough free space. If
the new version does not work, "Back to the old version" restores app, data and images.
The copy is kept until the app's next update.

## VMs and containers

Under More > VMs. The first time, Dahlia installs Incus from Debian; you choose whether the disks
live on the system SSD (`/var/lib/incus`) or on the cache (`/mnt/cache/incus`).

**New:**

- **Container (LXC):** a small Linux system sharing the server's kernel. Fast and light. Image
  from the list (Debian, Ubuntu, Alpine …).
- **VM:** a machine of its own with its own operating system. From a ready-made image or from an
  ISO file on the server (for example Home Assistant OS, Windows, another distribution).
- **Network:** "Behind the server (NAT)" gives a small network of its own; the server reaches the
  machine, the home network does not. "In the home network" gets an own address from the
  router; then the server itself cannot reach the machine (a macvlan limit), all other devices
  can.
- **Windows:** tick "Windows". The VM then gets a TPM and an NVMe disk, so setup finds the disk
  without extra drivers. For the network, install the virtio-win drivers in Windows.

**Console:** for a running VM, "Console" opens the VM's screen, in the app and in the browser.
"Ctrl+Alt+Del" sends that key combination to the VM. After installing from an ISO, shut the VM
down and choose "Eject ISO".

**Shell of a container:** "Shell" copies the command `incus exec <name> -- bash -l` and opens the
terminal. Paste it there.

Settings (cores, memory, start with the server) apply to VMs from their next start. Deleting is
possible for stopped machines only and removes their disk.

## Backup

Under More > Backup. The backup holds the configuration, the dashboard accounts and the data of
all apps, not films or photos in shares.

1. Choose a target: a folder on another disk (best a USB disk) or an SFTP server.
2. **Keep the password.** The backup is encrypted; without the password shown nothing can be
   restored.
3. The backup runs daily (time under Settings > Services > Schedules). Running apps pause briefly
   for it.

**Backup probe:** once a month Dahlia restores a sample of files from the latest backup into a
test folder and compares them. The result is shown on the Backup page; a failure is sent as a
notification.

**Restore:** per app or the configuration. On a new server: install Dahlia, enter the same target
and the old password, restore the configuration first, then the apps.

## Remote access

Settings > Security.

- **WireGuard:** Dahlia is the server. Add a peer per device and scan its QR code with the
  WireGuard app. The router must forward the UDP port (default 51820) to the server.
- **Tailscale:** install, sign in, done; no port on the router needed.

Otherwise the firewall accepts connections from local networks only. Own rules go under
Settings > Security > Firewall.

## Updates and snapshots

Settings > Updates shows pending package updates; the app also points them out at the top.
Security updates can be installed automatically.

Before each update, snapper takes a snapshot of the system. Under "System snapshots", "Roll back"
returns the system to a snapshot and reboots. Configuration, accounts, logs and Docker data are
taken from the running system; the pool and `/srv` are not touched. Snapshots can also be booted
from the boot menu.

## Notifications and weekly letter

The bell at the top shows messages from the server (backups, disks, updates). The desktop app
also shows new messages as notifications of the operating system; this can be switched off in
the bell. More channels (e-mail, ntfy, Gotify) under Settings > Services.

**Weekly letter and year in review:** every Monday Dahlia sums up the week (data growth, new
photos, backups, updates, new apps, warnings, network traffic), on 1 January the year. The
"Weekly letter" tile shows the latest one; older ones are in the tile's archive.

## Terminal, Docker and log

- **Terminal** (More > Terminal): a shell on the server as the Linux account with the dashboard
  account's name. Copy and paste with Ctrl+Shift+C/V (macOS: Cmd+C/V).
- **Docker** (More > Docker): all containers with CPU and RAM, start, stop, logs, clean-up.
- **System log** (More > System log): the journal with filters; "Diagnostics" saves everything
  that helps with troubleshooting, without passwords.

## When something does not work

| Problem | Fix |
| --- | --- |
| `dahlia.local` does not work | Use the IP address from the server's screen or from the router. Some routers or VPNs block mDNS. |
| The app does not find the server | Type the address. In the browser, get a pairing code under "Phone" and pair the app again. |
| Certificate warning | Download the local CA under Settings > Addresses and import it. |
| Dashboard password forgotten | Over SSH or on the console: `sudo rm /var/lib/dashboard/users.json && sudo systemctl restart dashboard`, then set up again. Apps and data stay. |
| Two-factor phone lost | `sudo dashboard --disable-totp <account>` |
| The system does not start properly after an update | Boot a snapshot from before the update in the boot menu, then roll back for good in the dashboard. |
| An app does not run after its update | Apps > the app > "Back to the old version". |
| A disk failed | Fit a replacement, Parity > Restore a disk. |
| A VM does not start ("no KVM") | Enable virtualisation in the BIOS (VT-x/AMD-V). |

Report problems: [GitHub issues](https://github.com/Fabiy99/dahlia-linux/issues), ideally with the
diagnostics bundle.
