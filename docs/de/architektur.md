# Architektur

[English version](../en/architecture.md)

Stand: Meilenstein 1. Dieses Dokument beschreibt das Zielbild. Was davon schon gebaut ist,
steht in der README unter "Meilensteine".

## Installation

Die ISO ist das aktuelle Debian-13-netinst-Image, umgebaut von `iso/build-iso.sh`.
`iso/preseed.cfg` steckt im Installer-initrd, die Installation läuft ohne Fragen durch.
Einzige Ausnahme ist die Zielplatte: `iso/pick-disk.sh` nimmt sie automatisch, wenn es
genau eine Kandidatin gibt (das Installationsmedium zählt nicht). Bei mehreren Platten
fragt der Installer nach. Am Ende installiert `iso/late-command.sh` die Dahlia-Pakete,
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
