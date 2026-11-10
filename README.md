<p align="center"><img src="docs/img/dahlia-logo.svg" width="120" alt=""></p>

# Dahlia Linux

**[Deutsch](#deutsch) | [English](#english)**

---

## Deutsch

Dahlia Linux ist ein Server-Betriebssystem für das Homelab auf Basis von Debian 13 "Trixie".
Es kombiniert Plattenpooling wie bei Unraid (mergerfs, SnapRAID, Cache mit Mover) mit
einem schlanken Debian-Unterbau. VMs und LXC über Incus kommen in Version 2.

Kein Abo, keine Lizenzschlüssel. Lizenz: GPLv3.

### Stand

Frühe Entwicklung. Ab Meilenstein 2 gibt es eine installierbare ISO mit dem Basissystem,
noch ohne Web-UI und Storage-Funktionen.

### In einer VM testen

1. ISO bauen (auf Debian/Ubuntu oder in WSL2): `tools/build-packages.sh && iso/build-iso.sh`.
   Ergebnis: `build/dahlia-linux-<version>-amd64.iso`. Getaggte Versionen hängen die ISO
   zusätzlich an ein GitHub-Release.
2. VM anlegen: 2 GB RAM, eine Platte ab 20 GB, Netzwerk gebridged. Hyper-V: Generation 2,
   Secure Boot mit der Vorlage "Microsoft UEFI Certificate Authority", fester Arbeitsspeicher
   (kein dynamischer).
3. Von der ISO booten und "Install Dahlia Linux" wählen. Der Installer (englisch) fragt
   nach Tastatur, Passwort, Systemplatte, Servername, Zeitzone, Netzwerk und optional einem
   SSH-Schlüssel und zeigt vor dem Löschen eine Zusammenfassung. Für Rechner ohne
   Bildschirm gibt es die Einträge "serial console" (ttyS0, 115200 Baud), für Tests
   "Automatic install" ohne Fragen (Startpasswort `dahlia`, muss beim ersten Login
   geändert werden).
4. Anmelden als `dahlia-admin`, per SSH: `ssh dahlia-admin@<servername>.local`.
5. Dashboard: `https://dahlia.local/` im Browser öffnen, die Zertifikatswarnung bestätigen
   und den Einrichtungscode eingeben, der auf dem Bildschirm des Servers und beim SSH-Login steht.
   Damit die Warnung verschwindet: unter "Domains" die lokale CA herunterladen und importieren
   (siehe [Netzwerk und Namen](docs/de/architektur.md#netzwerk-und-namen-ab-meilenstein-7)).
6. Updates: im Dashboard unter "Updates" oder `sudo apt update && sudo apt upgrade`
   (siehe [docs/de/apt-repo.md](docs/de/apt-repo.md)).

**Achtung:** "Automatic install" löscht eine einzelne Platte ohne Rückfrage.

### Grundidee

- Die ISO ist ein Debian-Installer mit Preseed. Sie installiert Debian 13 und danach die
  Dahlia-Pakete aus einem eigenen, signierten apt-Repo.
- Updates, Rollback und Upgrades sind normale apt-Vorgänge. Das Root-Dateisystem liegt auf
  Btrfs, `snapper` legt vor jedem apt-Lauf einen Snapshot an.
- Freigaben sehen nur `/mnt/user`. Apps liegen dort oder auf der System-SSD (`/srv/dahlia`),
  auch ganz ohne Datenplatten. In `/mnt/user` werden Cache-SSD und HDDs per mergerfs
  zusammengeführt. Der Mover schiebt im Hintergrund vom Cache auf die HDDs, die Pfade
  bleiben gleich.
- Vorhandene Platten mit Daten (ext4, XFS, Btrfs) werden ohne Formatieren eingebunden.
  Leere Platten lassen sich im Dashboard formatieren; von anderen Platten (auch NTFS/exFAT)
  kopiert Dahlia vorher die Daten nach `/mnt/user` und prüft sie.
- Das Web-UI läuft ohne root-Rechte. Systemänderungen gehen über einen kleinen root-Dienst
  mit einer festen Liste erlaubter Aktionen.

Details: [docs/de/architektur.md](docs/de/architektur.md)

### Pakete

| Paket | Inhalt |
| --- | --- |
| `dahlia-base` | Grundkonfiguration, Firewall, Benutzer und Gruppen |
| `dahlia-agent` | root-Dienst mit fester Aktionsliste |
| `dahlia-dashboard` | Web-UI (FastAPI, Vanilla JS) |
| `dahlia-storage` | disk-manager, Mover, SnapRAID-Wrapper |
| `dahlia-archive-keyring` | Signaturschlüssel des apt-Repos |

### Meilensteine

1. Repo-Gerüst, Lizenz, CI-Linting
2. Pakete und apt-Repo, ISO mit Preseed installiert sich in QEMU
3. `dahlia-storage` als CLI, getestet mit Loop-Devices
4. `dahlia-agent` mit Aktionsliste
5. Dashboard: Backend, Login, Frontend mit DE/EN
6. App-Store (eigenes Repo `dahlia-apps`), später mit Stacks (App mit Datenbank usw.) und
   Update-Anzeige
7. Domains, Reverse-Proxy, Zertifikate
8. Geführter Installer: Zielplatte auswählen, Servername, Passwort, Netzwerk, Zusammenfassung
   mit Lösch-Warnung. Der unbeaufsichtigte Modus bleibt als eigener Menüeintrag.
9. Überwachung, Benachrichtigungen, USV, Release 1.0
10. Version 2: VMs und LXC über Incus

### Mitmachen

Pull Requests bitte mit ausgefüllter Vorlage. Vor dem Push lokal prüfen:

```sh
shellcheck $(git ls-files '*.sh')
ruff check .
pytest
```

---

## English

Dahlia Linux is a homelab server operating system based on Debian 13 "Trixie".
It combines Unraid-style disk pooling (mergerfs, SnapRAID, a cache with a mover) with a
lean Debian base. VMs and LXC via Incus follow in version 2.

No subscription, no license keys. License: GPLv3.

### Status

Early development. From milestone 2 on there is an installable ISO with the base system,
without web UI or storage features yet.

### Testing in a VM

1. Build the ISO (on Debian/Ubuntu or in WSL2): `tools/build-packages.sh && iso/build-iso.sh`.
   Output: `build/dahlia-linux-<version>-amd64.iso`. Tagged versions also attach the ISO to
   a GitHub release.
2. Create a VM: 2 GB RAM, one disk of 20 GB or more, bridged network. Hyper-V: generation 2,
   Secure Boot with the "Microsoft UEFI Certificate Authority" template, static memory
   (not dynamic).
3. Boot from the ISO and pick "Install Dahlia Linux". The installer asks for the
   keyboard, password, system disk, server name, time zone, network and optionally an SSH
   key, and shows a summary before wiping the disk. Headless machines use the "serial
   console" entries (ttyS0, 115200 baud); for tests there is "Automatic install" without
   questions (initial password `dahlia`, must be changed on first login).
4. Log in as `dahlia-admin`, via SSH: `ssh dahlia-admin@<server name>.local`.
5. Dashboard: open `https://dahlia.local/` in a browser, accept the certificate warning and
   enter the setup code shown on the server's screen and on SSH login.
   To get rid of the warning: download and import the local CA under "Domains"
   (see [Network and names](docs/en/architecture.md#network-and-names-from-milestone-7)).
6. Updates: in the dashboard under "Updates" or `sudo apt update && sudo apt upgrade`
   (see [docs/en/apt-repo.md](docs/en/apt-repo.md)).

**Warning:** "Automatic install" wipes a single disk without asking.

### Design in short

- The ISO is a Debian installer with a preseed file. It installs Debian 13, then the
  Dahlia packages from a dedicated, signed apt repository.
- Updates, rollback and upgrades are plain apt operations. The root filesystem is Btrfs,
  and `snapper` takes a snapshot before every apt run.
- Shares only see `/mnt/user`, a mergerfs pool of cache SSD and HDDs. Apps live there or on
  the system SSD (`/srv/dahlia`), even with no data disks at all. The mover
  shifts data from cache to HDDs in the background; paths stay the same.
- Existing disks with data (ext4, XFS, Btrfs) are added without formatting.
  Blank disks can be formatted in the dashboard; other disks (also NTFS/exFAT) have their
  data copied to `/mnt/user` and verified first.
- The web UI runs without root. System changes go through a small root service that only
  accepts a fixed list of actions.

Details: [docs/en/architecture.md](docs/en/architecture.md)

### Packages

| Package | Contents |
| --- | --- |
| `dahlia-base` | Base configuration, firewall, users and groups |
| `dahlia-agent` | Root service with a fixed action list |
| `dahlia-dashboard` | Web UI (FastAPI, vanilla JS) |
| `dahlia-storage` | disk-manager, mover, SnapRAID wrapper |
| `dahlia-archive-keyring` | apt repository signing key |

### Milestones

1. Repository skeleton, license, CI linting
2. Packages and apt repository, preseeded ISO installs in QEMU
3. `dahlia-storage` as CLI, tested with loop devices
4. `dahlia-agent` with action list
5. Dashboard: backend, login, frontend with DE/EN
6. App store (separate repository `dahlia-apps`), later with stacks (app with database etc.)
   and update notices
7. Domains, reverse proxy, certificates
8. Guided installer: pick the target disk, server name, password, network, summary with a
   wipe warning. The unattended mode stays as a separate menu entry.
9. Monitoring, notifications, UPS, release 1.0
10. Version 2: VMs and LXC via Incus

### Contributing

Please fill in the pull request template. Check locally before pushing:

```sh
shellcheck $(git ls-files '*.sh')
ruff check .
pytest
```

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).
