# Architektur

[English version](../en/architecture.md)

Stand: Meilenstein 4. Dieses Dokument beschreibt das Zielbild. Was davon schon gebaut ist,
steht in der README unter "Meilensteine".

## Installation

Die ISO ist das aktuelle Debian-13-netinst-Image, umgebaut von `iso/build-iso.sh`.
`iso/preseed.cfg` steckt im Installer-initrd, die Installation läuft ohne Fragen durch.
Einzige Ausnahme ist die Zielplatte: `iso/pick-disk.sh` nimmt sie automatisch, wenn es
genau eine Kandidatin gibt (das Installationsmedium zählt nicht). Bei mehreren Platten
fragt der Installer nach. Ab Meilenstein 8 fragt der Installer immer nach der Zielplatte
(Modell, Größe, Seriennummer) und zeigt vor dem Löschen eine Zusammenfassung; die
automatische Auswahl bleibt nur im Menüeintrag für die unbeaufsichtigte Installation. Am Ende installiert `iso/late-command.sh` die Dahlia-Pakete,
die auf der ISO unter `/dahlia/pool` liegen.

Hostname (`dahlia`), Sprache (Englisch, Tastatur Deutsch) und Zeitzone (Europe/Berlin)
sind vorbelegt und werden später im Einrichtungsassistenten geändert. Angelegt wird der
Benutzer `dahlia-admin` (UID 1001, sudo) mit dem Startpasswort `dahlia`, das beim ersten
Login geändert werden muss. root hat kein Passwort.

Root-Dateisystem: Btrfs, der Installer legt das Subvolume `@rootfs` an. `snapper` legt vor
und nach jedem apt-Lauf einen Snapshot an, `grub-btrfs` macht die Snapshots im Bootmenü
startbar.

Beim ersten Start zeigt das Dashboard nur den Einrichtungsassistenten. Er ist mit einem
Einmal-Code geschützt, der auf tty1 und in `/etc/motd` steht (ab Meilenstein 5).

## Pakete

| Paket | Inhalt |
| --- | --- |
| `dahlia-base` | Konfiguration, nftables-Regeln, Benutzer `dahlia-ui`, Gruppe `dahlia` (GID 1000) |
| `dahlia-agent` | root-Dienst auf `/run/dahlia/agent.sock`, feste Aktionsliste |
| `dahlia-dashboard` | FastAPI-Backend hinter nginx, Frontend in Vanilla HTML/CSS/JS |
| `dahlia-storage` | disk-manager, Mover, SnapRAID-Wrapper, Health-Checks |
| `dahlia-archive-keyring` | öffentlicher Schlüssel des apt-Repos |

## Storage

| Pfad | Inhalt |
| --- | --- |
| `/mnt/diskN` | einzelne Datenplatten, je ein eigenes Dateisystem |
| `/mnt/parityN` | SnapRAID-Parity, nie im Pool |
| `/mnt/cache` | SSD(s), Btrfs, bei zwei SSDs RAID1 |
| `/mnt/storage` | mergerfs über alle Datenplatten (Ziel des Movers) |
| `/mnt/user` | mergerfs über `/mnt/cache/shares` und alle Datenplatten, Cache zuerst |
| `/mnt/external` | USB- und NTFS-Platten, außerhalb von Pool und SnapRAID |

Apps und Freigaben nutzen nur `/mnt/user`. Neue Dateien landen auf dem Cache
(`category.create=ff`), der Mover verschiebt sie nachts nach `/mnt/storage`. Der Pfad unter
`/mnt/user` ändert sich dabei nicht.

Platten werden als generierte systemd-Mount-Units mit `nofail` und
`x-systemd.device-timeout=15s` eingebunden. Fehlt eine Platte, startet das System trotzdem
und meldet den Ausfall.

SnapRAID läuft über einen Wrapper mit Timer: `diff`, Abbruch über einer Lösch-Schwelle,
`sync`, wöchentlich `scrub`. Mover und SnapRAID teilen sich ein Lock und laufen nie
gleichzeitig.

### dahlia-disk (ab Meilenstein 3)

`dahlia-disk` aus dem Paket `dahlia-storage` setzt das Layout um. Es formatiert nie etwas.
Zustand liegt in `/etc/dahlia/storage.json`, alles andere wird daraus erzeugt.

```sh
dahlia-disk list                          # Platten und was Dahlia damit tun darf
dahlia-disk add <UUID> --role data        # vorhandenes ext4/XFS/Btrfs als /mnt/diskN
dahlia-disk add <UUID> --role parity      # als /mnt/parityN, nie im Pool
dahlia-disk add <UUID> --role cache       # SSD-Dateisystem als /mnt/cache (ab M3b)
dahlia-disk add <UUID> --role external    # z. B. NTFS unter /mnt/external/<name>
dahlia-disk remove <UUID>                 # aushängen, Daten bleiben auf der Platte
dahlia-disk apply                         # Units und Pools aus storage.json neu erzeugen
dahlia-disk snapraid-config [--write]     # /etc/snapraid.conf erzeugen
```

Neue Datenplatten kommen per `user.mergerfs.branches` ohne Remount in die laufenden Pools.
Leere Mount-Points sind mit `chattr +i` gesperrt, damit bei fehlender Platte nichts auf
der System-SSD landet. Die Units hängen mit `Wants=` an `dahlia-storage.target`, eine
fehlende Platte blockiert also nicht den Boot.

### Cache, Mover, SnapRAID (ab Meilenstein 3b)

Der Cache ist ein Dateisystem (Btrfs, bei zwei SSDs vorher als RAID1 angelegt:
`mkfs.btrfs -d raid1 -m raid1 <ssd1> <ssd2>`). `dahlia-disk add --role cache` hängt es unter
`/mnt/cache` ein und setzt `/mnt/cache/shares` als ersten Zweig vor die Datenplatten in
`/mnt/user`, ebenfalls ohne Remount. `/mnt/user` nutzt dann `category.create=ff`,
`/mnt/storage` bleibt bei `mfs`.

```sh
dahlia-mover [--dry-run] [--min-age MIN]       # Cache -> /mnt/storage, Pfad bleibt gleich
dahlia-snapraid sync [--force]                 # diff, Lösch-Schwelle prüfen, sync
dahlia-snapraid scrub                          # Teil des Arrays gegen die Parity prüfen
dahlia-disk set-option snapraid_delete_threshold 50
```

Der Mover lässt offene, kürzlich geänderte und schon auf den Datenplatten vorhandene
Dateien sowie Hardlinks auf dem Cache. Er kopiert erst, prüft, benennt um und löscht dann.
Timer: Mover 03:00, SnapRAID-Sync 04:30, Scrub sonntags 06:00 (8 %, Blöcke älter als
10 Tage). Mover und SnapRAID teilen sich `/run/dahlia/array.lock`.

## Rechte und Sicherheit

- `dahlia-dashboard` läuft als `dahlia-ui` mit systemd-Härtung und hört nur auf
  `127.0.0.1`. nginx macht TLS auf Port 443.
- `dahlia-agent` läuft als root und nimmt nur Aktionen aus seiner Liste an. Jede Aktion
  prüft ihre Parameter.
- Docker veröffentlicht Container-Ports nur auf `127.0.0.1` (`daemon.json`: `"ip"`).
  Apps sind über nginx erreichbar. Apps, die LAN-Ports brauchen, sind im App-Template
  markiert.
- Die Firewall (nftables) öffnet Ports nur für aktivierte Funktionen und nur fürs lokale
  Subnetz.

### dahlia-agent (ab Meilenstein 4)

Der Agent hört auf `/run/dahlia/agent.sock` (root:dahlia-agent, 0660) und wird von
`dahlia-agent.socket` beim ersten Aufruf gestartet. Zusätzlich zu den Dateirechten prüft er
die Gegenstelle per `SO_PEERCRED`: erlaubt sind root und Mitglieder der Gruppe
`dahlia-agent`. Später ist das der Dashboard-Benutzer `dahlia-ui`.

Protokoll: eine Anfrage pro Verbindung, je eine JSON-Zeile hin und zurück.

```text
-> {"action": "storage.add", "params": {"uuid": "...", "role": "data"}}
<- {"ok": true, "result": {...}}
<- {"ok": false, "error": {"code": "invalid_params", "message": "..."}}
```

| Aktion | Parameter | Wirkung |
| --- | --- | --- |
| `system.info` | - | Hostname, Version, Kernel, Uptime, Last, Speicher |
| `system.set_hostname` | `hostname` (ein DNS-Label, klein) | `hostnamectl`, `/etc/hosts`, Avahi |
| `system.reboot`, `system.poweroff` | - | `systemctl --no-block ...` |
| `storage.list` | - | `dahlia-disk list --json` |
| `storage.add` | `uuid`, `role` (data, parity, cache, external) | `dahlia-disk add` |
| `storage.remove` | `uuid`, `force` (optional) | `dahlia-disk remove` |
| `storage.set_option` | `name`, `value` (Zahl) | `dahlia-disk set-option` |
| `job.start` | `job` (mover, snapraid-sync, snapraid-scrub) | startet die Unit, kehrt sofort zurück |
| `job.status` | `job` (dazu `update`) | Zustand und letztes Ergebnis |
| `updates.check` | - | `apt-get update`, Liste der Upgrades |
| `updates.apply` | - | `apt-get full-upgrade` als Unit `dahlia-update` |
| `agent.actions` | - | diese Liste |

Unbekannte Aktionen, unbekannte oder fehlende Parameter und falsche Typen werden abgelehnt,
bevor ein Befehl läuft. Befehle laufen als Argumentlisten, nie über eine Shell. Ändernde
Aktionen laufen nacheinander. Jeder Aufruf steht mit UID und Ergebnis im Journal
(`journalctl -u dahlia-agent`).

Zum Testen auf der Konsole:

```sh
sudo dahlia-agent-call system.info
sudo dahlia-agent-call storage.add '{"uuid": "<UUID>", "role": "data"}'
```

## Netzwerk und Namen

- Server: `dahlia.local` über Avahi.
- Apps: Port-Links (Standard), einstufige mDNS-Aliase (`jellyfin.local`) oder
  `*.dahlia.home.arpa` über AdGuard-Rewrites.
- Zertifikate von einer lokalen Dahlia-CA. Das CA-Zertifikat steht im Dashboard zum
  Download bereit.

## Updates

- Debian-Sicherheitsupdates über `unattended-upgrades`.
- Dahlia-Pakete und übrige Updates per Klick im UI, immer mit Snapshot vorher.
- Das apt-Repo wird in CI gebaut, signiert und auf GitHub Pages veröffentlicht
  (`https://fabiy99.github.io/dahlia-linux/`). Der private Schlüssel existiert nur als
  GitHub-Secret. Details: [apt-repo.md](apt-repo.md).
