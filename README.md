# Dahlia Linux

**[Deutsch](#deutsch) | [English](#english)**

---

## Deutsch

Dahlia Linux ist ein Server-Betriebssystem für das Homelab auf Basis von Debian 13 "Trixie".
Es kombiniert Plattenpooling wie bei Unraid (mergerfs, SnapRAID, Cache mit Mover) mit
einem schlanken Debian-Unterbau. VMs und LXC über Incus kommen in Version 2.

Kein Abo, keine Lizenzschlüssel. Lizenz: GPLv3.

### Stand

Frühe Entwicklung. Es gibt noch kein installierbares Image. Der Fortschritt läuft über
die Meilensteine unten.

### Grundidee

- Die ISO ist ein Debian-Installer mit Preseed. Sie installiert Debian 13 und danach die
  Dahlia-Pakete aus einem eigenen, signierten apt-Repo.
- Updates, Rollback und Upgrades sind normale apt-Vorgänge. Das Root-Dateisystem liegt auf
  Btrfs, `snapper` legt vor jedem apt-Lauf einen Snapshot an.
- Apps und Freigaben sehen nur `/mnt/user`. Dort werden Cache-SSD und HDDs per mergerfs
  zusammengeführt. Der Mover schiebt im Hintergrund vom Cache auf die HDDs, die Pfade
  bleiben gleich.
- Vorhandene Platten mit Daten (ext4, XFS, Btrfs) werden ohne Formatieren eingebunden.
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
6. App-Store (eigenes Repo `dahlia-apps`)
7. Domains, Reverse-Proxy, Zertifikate
8. Überwachung, Benachrichtigungen, USV, Release 1.0
9. Version 2: VMs und LXC über Incus

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

Early development. There is no installable image yet. Progress follows the milestones
below.

### Design in short

- The ISO is a Debian installer with a preseed file. It installs Debian 13, then the
  Dahlia packages from a dedicated, signed apt repository.
- Updates, rollback and upgrades are plain apt operations. The root filesystem is Btrfs,
  and `snapper` takes a snapshot before every apt run.
- Apps and shares only see `/mnt/user`, a mergerfs pool of cache SSD and HDDs. The mover
  shifts data from cache to HDDs in the background; paths stay the same.
- Existing disks with data (ext4, XFS, Btrfs) are added without formatting.
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
6. App store (separate repository `dahlia-apps`)
7. Domains, reverse proxy, certificates
8. Monitoring, notifications, UPS, release 1.0
9. Version 2: VMs and LXC via Incus

### Contributing

Please fill in the pull request template. Check locally before pushing:

```sh
shellcheck $(git ls-files '*.sh')
ruff check .
pytest
```

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).
