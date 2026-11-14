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

Aktuelle Version: 1.3.0. Die ISO und die Desktop-App (Windows, macOS, Linux) hängen am jeweiligen
[GitHub-Release](https://github.com/Fabiy99/dahlia-linux/releases). Installierte Server holen
Updates über das apt-Repo, im Dashboard unter "Updates".

### Funktionen

- **Platten und Array:** Datenplatten einzeln unter `/mnt/diskN`, Pool über mergerfs, Cache-SSD
  (Btrfs, RAID1 bei zwei SSDs) mit Mover, SnapRAID-Parity mit Sync, Scrub und Wiederherstellen.
  Platten mit Daten werden ohne Formatieren übernommen, leere lassen sich im Dashboard
  formatieren. Spindown, SMART-Werte, geplante SMART-Selbsttests und ein Plattentest
  (badblocks) vor dem Einsatz.
- **Freigaben:** SMB mit öffentlichen, geschützten und privaten Freigaben, eigene SMB-Benutzer,
  Time Machine, NFSv4. Freigaben anderer Geräte (NAS, PC) lassen sich unter `/mnt/remotes`
  einbinden und von Apps nutzen.
- **Apps:** Katalog mit über 100 Vorlagen ([dahlia-apps](https://github.com/Fabiy99/dahlia-apps)),
  Stacks mit Datenbank, Update-Anzeige, Ordnerwahl über einen Ordnerbaum, Grafikkarte für
  Transcoding, eigene Container ohne Katalog. Apps liegen auf dem Array oder auf der System-SSD.
- **Netzwerk:** Port-Links und mDNS-Namen (`<app>.local`), lokale CA für HTTPS, nftables-Firewall
  nur für lokale Netze, Fernzugriff per WireGuard oder Tailscale.
- **System:** Btrfs-Root mit snapper, Rollback im Dashboard, automatische Sicherheitsupdates,
  Backup von Appdaten und Konfiguration (lokal oder per SFTP), Benachrichtigungen (E-Mail, ntfy,
  Gotify), USV über NUT, Terminal im Browser, System-Log, Diagnosepaket.
- **Startbildschirm:** animierte Dahlie im Hintergrund, Begrüßung und ein Satz zum Zustand,
  Widgets ein- und ausblenden, Wetter für mehrere Orte (Open-Meteo); der Hintergrund folgt dem
  Wetter. Ein Monitor am Server zeigt denselben Startbildschirm mit Uhr, Adresse und QR-Code,
  vor der Einrichtung auch den Einrichtungscode.
- **Desktop-App:** für Windows, macOS und Linux (`desktop/`). Findet den Server im Netz, koppelt
  sich über den Fingerabdruck der lokalen CA und zeigt das Dashboard im eigenen Fenster; Apps wie
  Jellyfin öffnen sich per Klick in einem App-Fenster, ohne IP-Adressen.
- **Handy:** das Handy-Symbol im Dashboard zeigt einen QR-Code; das Handy öffnet dasselbe
  Dashboard im Browser und legt es als Lesezeichen ab. Darüber bleibt der Server immer erreichbar
  und die Desktop-App lässt sich mit einem kurzen Kopplungscode neu koppeln.
- **Anmeldung:** eigenes Dashboard-Konto aus dem Einrichtungs-Assistenten, optional mit
  Zwei-Faktor-Login (TOTP).

### In einer VM testen

1. ISO vom [neuesten Release](https://github.com/Fabiy99/dahlia-linux/releases/latest)
   herunterladen oder selbst bauen (auf Debian/Ubuntu oder in WSL2):
   `tools/build-packages.sh && iso/build-iso.sh`, Ergebnis:
   `build/dahlia-linux-<version>-amd64.iso`.
2. VM anlegen: 2 GB RAM, eine Platte ab 20 GB, Netzwerk gebridged. Hyper-V: Generation 2,
   Secure Boot mit der Vorlage "Microsoft UEFI Certificate Authority", fester Arbeitsspeicher
   (kein dynamischer).
3. Von der ISO booten und "Install Dahlia Linux" wählen. Der Installer (englisch) fragt
   nach Tastatur, Kontoname, Passwort, Systemplatte, Servername, Zeitzone, Netzwerk und
   optional einem SSH-Schlüssel und zeigt vor dem Löschen eine Zusammenfassung. Für Rechner
   ohne Bildschirm gibt es die Einträge "serial console" (ttyS0, 115200 Baud), für Tests
   "Automatic install" ohne Fragen (Konto `dahlia-admin`, Startpasswort `dahlia`, muss beim
   ersten Login geändert werden).
4. Mit dem gewählten Konto anmelden, per SSH: `ssh <konto>@<servername>.local`.
5. Dashboard: `https://dahlia.local/` im Browser öffnen, die Zertifikatswarnung bestätigen
   und den Einrichtungscode eingeben, der auf dem Bildschirm des Servers und beim SSH-Login steht.
   Damit die Warnung verschwindet: unter "Adressen" die lokale CA herunterladen und importieren
   (siehe [Netzwerk und Namen](docs/de/architektur.md#netzwerk-und-namen-ab-meilenstein-7)).
6. Updates: im Dashboard unter "Updates" oder `sudo apt update && sudo apt upgrade`
   (siehe [docs/de/apt-repo.md](docs/de/apt-repo.md)).

**Achtung:** "Automatic install" löscht eine einzelne Platte ohne Rückfrage.

### Grundidee

- Die ISO ist ein Debian-Installer mit Preseed. Sie installiert Debian 13 und danach die
  Dahlia-Pakete aus einem eigenen, signierten apt-Repo.
- Updates, Rollback und Upgrades sind normale apt-Vorgänge. Das Root-Dateisystem liegt auf
  Btrfs, `snapper` legt vor jedem apt-Lauf einen Snapshot an.
- Freigaben sehen nur `/mnt/user`. Apps liegen dort oder auf der System-SSD (`/srv`),
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
| `base` | Grundkonfiguration, Firewall, Benutzer und Gruppen |
| `agent` | root-Dienst mit fester Aktionsliste |
| `dashboard` | Web-UI (FastAPI, Vanilla JS) |
| `storage` | disk-manager, Mover, SnapRAID-Wrapper |
| `archive-keyring` | Signaturschlüssel des apt-Repos |

### Meilensteine

Erledigt: 1 bis 40.

1. Repo-Gerüst, Lizenz, CI-Linting
2. Pakete und apt-Repo, ISO mit Preseed installiert sich in QEMU
3. `storage` als CLI, getestet mit Loop-Devices
4. `agent` mit Aktionsliste
5. Dashboard: Backend, Login, Frontend mit DE/EN
6. App-Store (eigenes Repo `dahlia-apps`), später mit Stacks (App mit Datenbank usw.) und
   Update-Anzeige
7. Domains, Reverse-Proxy, Zertifikate
8. Geführter Installer: Zielplatte auswählen, Servername, Passwort, Netzwerk, Zusammenfassung
   mit Lösch-Warnung. Der unbeaufsichtigte Modus bleibt als eigener Menüeintrag.
9. Überwachung, Benachrichtigungen, USV
10. Firewall: nur lokale Netze, nur Ports genutzter Dienste und Apps
11. Grafikkarte (`/dev/dri`) für Apps, z. B. Transcoding in Jellyfin
12. Terminal im Dashboard
13. Backup: Appdaten und Konfiguration nach Zeitplan
14. Parity im Dashboard: einrichten, Sync, Scrub, Wiederherstellen
15. Automatische Sicherheitsupdates (unattended-upgrades)
16. Diagnosepaket zum Herunterladen, danach Release 1.0

Nach 1.0 (1.x):

17. Speicher-Einstellungen im UI: freier Platz pro Platte, Mover-Mindestalter, Lösch-Schwelle
18. Snapshot-Rollback im UI
19. Eigene Container ohne Katalog, mit denselben Grenzen wie die Vorlagen
20. Fernzugriff per WireGuard oder Tailscale
21. NFS-Freigaben, Time Machine über SMB
22. Zwei-Faktor-Login (TOTP)
23. Geplante SMART-Selbsttests, Platten-Test vor dem Einsatz
24. SMB-/NFS-Freigaben anderer Geräte einbinden
25. Verlaufsgrafiken (CPU, RAM, Netz, Temperaturen), Export nach InfluxDB
26. API-Tokens für Home Assistant, Homepage usw.
27. Eigene Firewall-Regeln
28. Energiesparmodus (CPU-Governor), Liste der PCI- und USB-Geräte
29. Gesundheitscheck mit Hinweisen
30. Remote-Syslog, Platte per LED finden, Verlauf der Parity-Läufe, alle Apps starten/stoppen
31. Bridge, VLAN, Bond, Wake-on-LAN
32. SMB-Papierkorb
33. Mehrere und nur-lesende Dashboard-Konten
34. Dateimanager
35. Verschlüsselung (LUKS)
36. Eigener Kontoname im Installer statt `dahlia-admin`; Dashboard-Konto und Terminal-Benutzer
    heißen gleich, angezeigt wird nur der Name
37. Startbildschirm mit animierter Dahlie, anpassbaren Widgets und Wetter
38. Startbildschirm auf dem Monitor am Server
39. Desktop-App für Windows, macOS und Linux; Apps öffnen sich in der App
40. Handy über den Browser per QR-Code, neu koppeln der Desktop-App
41. Native Desktop-App (Flutter): Kopplung, Anmeldung, Start, Server-Update; immer dieselbe
    Version wie der Server, aktualisiert sich selbst aus dem Repo
42. Native App: Bereich Apps (installierte Apps, Katalog, Installieren mit Ort und Ordnerwahl)
43. Native App: Bereich Platten (Pools, SMART, Selbsttests, Burn-in, Import, Formatieren,
    Verschlüsselung, Einstellungen)
44. Native App: Bereich Dateien (Ordner, Suche, Pfad kopieren, Hoch- und Herunterladen,
    Kopieren, Verschieben, Umbenennen, Löschen)

Später: USB-Creator für Windows, Installationsanleitung.

Version 2: VMs und LXC über Incus.

### Mitmachen

Pull Requests bitte mit ausgefüllter Vorlage. Vor dem Push lokal prüfen:

```sh
shellcheck $(git ls-files '*.sh')
ruff check .
pytest
```

### Code-Signierung

Die Windows-Pakete der Desktop-App (`.exe`, `.msi`) werden ab dem nächsten Release signiert:
kostenlose Code-Signierung über [SignPath.io](https://about.signpath.io/), Zertifikat der
[SignPath Foundation](https://signpath.org/). Signiert werden nur Dateien, die GitHub Actions aus
diesem Repo baut; jede Signierung gibt der Maintainer einzeln frei.

- Autoren und Freigabe: [Fabiy99](https://github.com/Fabiy99)
- Datenschutz: Die Desktop-App überträgt keine Daten an andere Systeme als den Dahlia-Server, mit
  dem sie gekoppelt ist. Der Server selbst holt Updates aus dem apt-Repo, den App-Katalog von
  GitHub und, falls Orte eingetragen sind, das Wetter von Open-Meteo.

---

## English

Dahlia Linux is a homelab server operating system based on Debian 13 "Trixie".
It combines Unraid-style disk pooling (mergerfs, SnapRAID, a cache with a mover) with a
lean Debian base. VMs and LXC via Incus follow in version 2.

No subscription, no license keys. License: GPLv3.

### Status

Current version: 1.3.0. The ISO and the desktop app (Windows, macOS, Linux) are attached to each
[GitHub release](https://github.com/Fabiy99/dahlia-linux/releases). Installed servers get
updates from the apt repository, in the dashboard under "Updates".

### Features

- **Disks and array:** data disks individually under `/mnt/diskN`, pool via mergerfs, cache SSD
  (Btrfs, RAID1 with two SSDs) with a mover, SnapRAID parity with sync, scrub and restore.
  Disks with data are added without formatting, blank ones can be formatted in the dashboard.
  Spindown, SMART values, scheduled SMART self-tests and a disk test (badblocks) before use.
- **Shares:** SMB with public, secure and private shares, separate SMB users, Time Machine,
  NFSv4. Shares of other devices (NAS, PC) can be mounted under `/mnt/remotes` and used by apps.
- **Apps:** catalog with over 100 templates ([dahlia-apps](https://github.com/Fabiy99/dahlia-apps)),
  stacks with a database, update notices, folders chosen from a folder tree, GPU for
  transcoding, custom containers without the catalog. Apps live on the array or the system SSD.
- **Network:** port links and mDNS names (`<app>.local`), local CA for HTTPS, nftables firewall
  for local networks only, remote access via WireGuard or Tailscale.
- **System:** Btrfs root with snapper, rollback in the dashboard, automatic security updates,
  backup of app data and configuration (local or via SFTP), notifications (email, ntfy,
  Gotify), UPS via NUT, terminal in the browser, system log, diagnostics bundle.
- **Start screen:** animated dahlia in the background, greeting and one sentence on the state,
  widgets can be shown or hidden, weather for several places (Open-Meteo); the background follows
  the weather. A monitor attached to the server shows the same start screen with clock, address and
  QR code, before setup also the setup code.
- **Desktop app:** for Windows, macOS and Linux (`desktop/`). Finds the server in the network,
  pairs via the fingerprint of the local CA and shows the dashboard in its own window; apps such as
  Jellyfin open in an app window with one click, no IP addresses.
- **Phone:** the phone icon in the dashboard shows a QR code; the phone opens the same dashboard in
  the browser and keeps it as a bookmark. That way the server stays reachable, and the desktop app
  can be paired again with a short pairing code.
- **Login:** own dashboard account from the setup wizard, optionally with two-factor login (TOTP).

### Testing in a VM

1. Download the ISO from the [latest release](https://github.com/Fabiy99/dahlia-linux/releases/latest)
   or build it yourself (on Debian/Ubuntu or in WSL2):
   `tools/build-packages.sh && iso/build-iso.sh`, output:
   `build/dahlia-linux-<version>-amd64.iso`.
2. Create a VM: 2 GB RAM, one disk of 20 GB or more, bridged network. Hyper-V: generation 2,
   Secure Boot with the "Microsoft UEFI Certificate Authority" template, static memory
   (not dynamic).
3. Boot from the ISO and pick "Install Dahlia Linux". The installer asks for the
   keyboard, account name, password, system disk, server name, time zone, network and
   optionally an SSH key, and shows a summary before wiping the disk. Headless machines use
   the "serial console" entries (ttyS0, 115200 baud); for tests there is "Automatic install"
   without questions (account `dahlia-admin`, initial password `dahlia`, must be changed on
   first login).
4. Log in with the chosen account, via SSH: `ssh <account>@<server name>.local`.
5. Dashboard: open `https://dahlia.local/` in a browser, accept the certificate warning and
   enter the setup code shown on the server's screen and on SSH login.
   To get rid of the warning: download and import the local CA under "Addresses"
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
  the system SSD (`/srv`), even with no data disks at all. The mover
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
| `base` | Base configuration, firewall, users and groups |
| `agent` | Root service with a fixed action list |
| `dashboard` | Web UI (FastAPI, vanilla JS) |
| `storage` | disk-manager, mover, SnapRAID wrapper |
| `archive-keyring` | apt repository signing key |

### Milestones

Done: 1 to 40.

1. Repository skeleton, license, CI linting
2. Packages and apt repository, preseeded ISO installs in QEMU
3. `storage` as CLI, tested with loop devices
4. `agent` with action list
5. Dashboard: backend, login, frontend with DE/EN
6. App store (separate repository `dahlia-apps`), later with stacks (app with database etc.)
   and update notices
7. Domains, reverse proxy, certificates
8. Guided installer: pick the target disk, server name, password, network, summary with a
   wipe warning. The unattended mode stays as a separate menu entry.
9. Monitoring, notifications, UPS
10. Firewall: local networks only, only ports of services and apps in use
11. GPU (`/dev/dri`) for apps, e.g. transcoding in Jellyfin
12. Terminal in the dashboard
13. Backup: app data and configuration on a schedule
14. Parity in the dashboard: set up, sync, scrub, restore
15. Automatic security updates (unattended-upgrades)
16. Diagnostics bundle to download, then release 1.0

After 1.0 (1.x):

17. Storage settings in the UI: free space per disk, mover minimum age, delete threshold
18. Snapshot rollback in the UI
19. Custom containers without the catalog, with the same limits as templates
20. Remote access via WireGuard or Tailscale
21. NFS shares, Time Machine over SMB
22. Two-factor login (TOTP)
23. Scheduled SMART self-tests, disk test before use
24. Mount SMB/NFS shares of other devices
25. History graphs (CPU, RAM, network, temperatures), export to InfluxDB
26. API tokens for Home Assistant, Homepage etc.
27. Custom firewall rules
28. Power saving (CPU governor), list of PCI and USB devices
29. Health check with hints
30. Remote syslog, find a disk by its LED, history of parity runs, start/stop all apps
31. Bridge, VLAN, bond, Wake-on-LAN
32. SMB recycle bin
33. Multiple and read-only dashboard accounts
34. File manager
35. Encryption (LUKS)
36. Own account name in the installer instead of `dahlia-admin`; dashboard account and terminal
    user share the name, only the name is shown
37. Start screen with animated dahlia, customizable widgets and weather
38. Start screen on the monitor attached to the server
39. Desktop app for Windows, macOS and Linux; apps open inside the app
40. Phone through the browser via QR code, re-pairing of the desktop app
41. Native desktop app (Flutter): pairing, sign-in, home, server update; always the same
    version as the server, updates itself from the repository
42. Native app: Apps area (installed apps, catalog, install with location and folder picker)
43. Native app: Disks area (pools, SMART, self-tests, burn-in, import, format, encryption,
    settings)
44. Native app: Files area (folders, search, copy path, upload and download, copy, move,
    rename, delete)

Later: USB creator for Windows, installation guide.

Version 2: VMs and LXC via Incus.

### Contributing

Please fill in the pull request template. Check locally before pushing:

```sh
shellcheck $(git ls-files '*.sh')
ruff check .
pytest
```

### Code signing

The Windows packages of the desktop app (`.exe`, `.msi`) are signed from the next release on:
free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/). Only files built by GitHub Actions from this
repository are signed; the maintainer approves every signing request.

- Committers and approvers: [Fabiy99](https://github.com/Fabiy99)
- Privacy: the desktop app does not transfer any information to systems other than the Dahlia
  server it is paired with. The server itself fetches updates from the apt repository, the app
  catalog from GitHub and, if places are set, the weather from Open-Meteo.

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).
