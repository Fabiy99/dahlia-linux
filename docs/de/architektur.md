# Architektur

[English version](../en/architecture.md)

Stand: Meilenstein 8. Dieses Dokument beschreibt das Zielbild. Was davon schon gebaut ist,
steht in der README unter "Meilensteine".

## Installation (geführt ab Meilenstein 8)

Die ISO ist das aktuelle Debian-13-netinst-Image, umgebaut von `iso/build-iso.sh`.
`iso/preseed.cfg` steckt im Installer-initrd und enthält alles, was beide Modi gemeinsam
haben. Der Installer ist englisch. Boot-Menü (BIOS und UEFI):

| Eintrag | Modus |
| --- | --- |
| Install Dahlia Linux | geführt (Standard) |
| Install Dahlia Linux (serial console) | geführt, Ausgabe auf ttyS0 |
| Automatic install (no questions, default password) | unbeaufsichtigt |
| Automatic install (serial console) | unbeaufsichtigt auf ttyS0 (CI-Test) |

Geführt (`iso/installer/dahlia-guided.cfg`, `guided.sh`): Der Debian-Installer fragt nach
dem Tastaturlayout und dem Passwort für `dahlia-admin`. Danach fragt `guided.sh`:

1. Systemplatte, mit Gerät, Größe, Modell und Seriennummer. Das Installationsmedium wird
   nie angeboten.
2. Servername (ein DNS-Label, Standard `dahlia`)
3. Zeitzone
4. Netzwerk des installierten Systems: DHCP oder statisch (Adresse mit Präfix, Gateway,
   DNS). Der Installer selbst braucht DHCP und Internet.
5. SSH-Schlüssel für `dahlia-admin` (optional)
6. Zusammenfassung mit Warnung, dass alle Daten auf der gewählten Platte gelöscht werden.
   "No" führt zurück zur Plattenauswahl, die Antworten bleiben vorbelegt.

Jede Frage hat "Go Back". `late-command.sh` schreibt die Antworten mit `apply-answers.sh`
ins Zielsystem (`/etc/hostname`, `/etc/hosts`, `/etc/timezone`,
`/etc/network/interfaces`, `/etc/resolv.conf`, `~dahlia-admin/.ssh/authorized_keys`),
bevor die Dahlia-Pakete installiert werden. Das Passwort ist nicht abgelaufen.

Unbeaufsichtigt (`dahlia-auto.cfg`, Kernel-Parameter `dahlia.mode=auto`): keine Fragen.
Gibt es genau eine Kandidatin, nimmt `pick-disk.sh` sie ohne Rückfrage; bei mehreren fragt
der Installer. Servername `dahlia`, Tastatur Deutsch, Zeitzone Europe/Berlin, Benutzer
`dahlia-admin` mit dem Startpasswort `dahlia`, das beim ersten Login geändert werden muss.

In beiden Modi: Benutzer `dahlia-admin` (UID 1001, sudo), root ohne Passwort. Am Ende
installiert `late-command.sh` die Dahlia-Pakete, die auf der ISO unter `/dahlia/pool`
liegen.

Root-Dateisystem: Btrfs, der Installer legt das Subvolume `@rootfs` an. `snapper` legt vor
und nach jedem apt-Lauf einen Snapshot an, `grub-btrfs` macht die Snapshots im Bootmenü
startbar.

Beim ersten Start zeigt das Dashboard nur den Einrichtungsassistenten. Er ist mit einem
Einmal-Code geschützt, der auf tty1 und in `/etc/motd` steht (ab Meilenstein 5).

## Pakete

| Paket | Inhalt |
| --- | --- |
| `dahlia-base` | Konfiguration, nftables-Regeln, Gruppe `dahlia` (GID 1000), zieht alle Dahlia-Pakete nach |
| `dahlia-agent` | root-Dienst auf `/run/dahlia/agent.sock`, feste Aktionsliste |
| `dahlia-dashboard` | FastAPI-Backend als `dahlia-ui` hinter nginx, Frontend in Vanilla HTML/CSS/JS |
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

SMB-Freigaben nutzen nur `/mnt/user`. Apps liegen auf `/mnt/user` oder, wenn man es bei der
Installation wählt oder kein Array da ist, auf der System-SSD unter `/srv` (siehe Apps). Neue Dateien landen auf dem Cache
(`category.create=ff`), der Mover verschiebt sie nachts nach `/mnt/storage`. Der Pfad unter
`/mnt/user` ändert sich dabei nicht.

Platten werden als generierte systemd-Mount-Units mit `nofail` und
`x-systemd.device-timeout=15s` eingebunden. Fehlt eine Platte, startet das System trotzdem
und meldet den Ausfall.

SnapRAID läuft über einen Wrapper mit Timer: `diff`, Abbruch über einer Lösch-Schwelle,
`sync`, wöchentlich `scrub`. Mover und SnapRAID teilen sich ein Lock und laufen nie
gleichzeitig.

### dahlia-disk (ab Meilenstein 3)

`dahlia-disk` aus dem Paket `dahlia-storage` setzt das Layout um. `add` formatiert nie;
nur `format` legt ein Dateisystem an: auf einer leeren Platte oder auf einer, deren Daten
`import` nach `/mnt/user` kopiert und geprüft hat. `import` lässt `lost+found`,
`System Volume Information` und `$RECYCLE.BIN` auf oberster Ebene aus. Eine Platte ohne
lesbare Signatur, die trotzdem eingehängt ist, oder mit mehreren Signaturen gilt nie als leer.
Zustand liegt in `/etc/dahlia/storage.json`, alles andere wird daraus erzeugt.

```sh
dahlia-disk list                          # Platten und was Dahlia damit tun darf
dahlia-disk add <UUID> --role data        # vorhandenes ext4/XFS/Btrfs als /mnt/diskN
dahlia-disk add <UUID> --role parity      # als /mnt/parityN, nie im Pool
dahlia-disk add <UUID> --role cache       # SSD-Dateisystem als /mnt/cache (ab M3b)
dahlia-disk add <UUID> --role external    # z. B. NTFS unter /mnt/external/<name>
dahlia-disk remove <UUID>                 # aushängen, Daten bleiben auf der Platte
dahlia-disk format /dev/sdX --fs xfs      # leere Platte: GPT, eine Partition, XFS/ext4/Btrfs
dahlia-disk import <UUID> --to <Name>     # nur lesend einhängen, rsync nach /mnt/user/<Name>, Vergleich
dahlia-disk apply                         # Units und Pools aus storage.json neu erzeugen
dahlia-disk snapraid-config [--write]     # /etc/snapraid.conf erzeugen
```

Neue Datenplatten kommen per `user.mergerfs.branches` ohne Remount in die laufenden Pools.
Die Pools laufen mit `cache.files=partial`: SQLite im WAL-Modus und MariaDB brauchen
shared mmap, das mergerfs mit `cache.files=off` (direct_io) nicht bietet.
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
| `system.stats` | - | CPU-Zähler, Speicher, Swap, Netzwerk, Belegung von `/` und `/mnt/*` |
| `system.hardware` | - | Mainboard (DMI), CPU-Temperatur (hwmon), SMART je Platte mit `smartctl -n standby` (weckt keine schlafende Platte) |
| `system.log` | `lines`, `priority`, `unit` (optional) | `journalctl -n … -p …` |
| `settings.time` / `settings.set_time` | `timezone`, `ntp` | `timedatectl set-timezone`, `set-ntp` |
| `settings.network` / `settings.set_network` | `mode` (`dhcp`/`static`), `address` (CIDR), `gateway`, `dns` | `/etc/network/interfaces`, Anwendung über `dahlia-network` mit Rückfall |
| `settings.confirm_network` | – | bestätigt die neue Netzwerkeinstellung |
| `settings.spindown` / `settings.set_spindown` | `default`, `disks` (Seriennummer → Minuten) | `/etc/dahlia/spindown.json`, `hdparm -S` |
| `settings.schedules` / `settings.set_schedule` | `job`, `frequency`, `time`, `weekday`, `monthday` | Drop-in `OnCalendar=` für die Timer |
| `shares.list` | – | Freigaben, SMB-Benutzer, ob `/mnt/user` eingehängt ist |
| `shares.set` / `shares.remove` | `name`, `comment`, `security` (`public`/`secure`/`private`), `users` (Name → `rw`/`ro`), `time_machine` | erzeugt `/etc/samba/smb.conf`, prüft mit `testparm`, startet `smbd` |
| `shares.user_add` / `shares.user_password` / `shares.user_remove` | `name`, `password` | `useradd`/`userdel` (Gruppen `dahlia`, `dahlia-smb`), `smbpasswd` über stdin |
| `notify.get` | – | Kanäle ohne Geheimnisse, Schalter, letzte Meldungen |
| `notify.set` | `channel` (`email`/`ntfy`/`gotify`), `settings` | `/etc/dahlia/notify.json` (0600); leeres Passwort/Token behält das alte |
| `notify.set_events` / `notify.test` | `events` (Name → bool) | Meldungsarten an/aus, Testnachricht an alle aktiven Kanäle |
| `ups.get` / `ups.set` | `mode` (`off`/`usb`/`remote`), `host`, `port`, `name`, `user`, `password`, `shutdown_minutes` (0 bis 120) | erzeugt `/etc/nut/*`, `upsc` für den Status |
| `docker.list`, `docker.stats`, `docker.disk` | - | alle Container, CPU/RAM je Container, Platzbedarf von Images, Volumes, Build-Cache |
| `docker.container` | `name`, `op` (`start`, `stop`, `restart`) | `docker <op> -- <name>` |
| `docker.logs` | `name`, `lines` (10 bis 5000) | `docker logs --tail` |
| `docker.prune` | - | `docker image prune -a`, `docker builder prune` |
| `system.set_hostname` | `hostname` (ein DNS-Label, klein) | `hostnamectl`, `/etc/hosts`, Avahi, neues Zertifikat |
| `system.reboot`, `system.poweroff` | - | `systemctl --no-block ...` |
| `storage.list` | - | `dahlia-disk list --json` |
| `storage.add` | `uuid`, `role` (data, parity, cache, external) | `dahlia-disk add` |
| `storage.remove` | `uuid`, `force` (optional) | `dahlia-disk remove` |
| `storage.set_option` | `name`, `value` (Zahl) | `dahlia-disk set-option` |
| `storage.format` | `device` (ganze Platte), `filesystem` (`xfs`, `ext4`, `btrfs`), `confirm` (Plattenname wiederholt) | `dahlia-disk format`, nur leere oder übernommene Platten |
| `storage.import` / `storage.import_status` | `uuid`, `target` (Name unter `/mnt/user`) | `dahlia-disk import` als Unit `dahlia-import`, Fortschritt in `/run/dahlia/import.json` |
| `job.start` | `job` (mover, snapraid-sync, snapraid-scrub) | startet die Unit, kehrt sofort zurück |
| `job.status` | `job` (dazu `update`) | Zustand und letztes Ergebnis |
| `updates.check` | - | `apt-get update`, Liste der Upgrades |
| `updates.apply` | - | `apt-get full-upgrade` über die Unit `dahlia-update.service` |
| `apps.catalog` | `refresh` (optional) | App-Katalog und sein Stand, mit `refresh` vorher herunterladen |
| `apps.list` | - | installierte Apps mit Container-Zustand, Version, verfügbarem Update |
| `apps.install`, `apps.update` | `id`, bei `install` `location` (optional, `/mnt/user` oder `/srv`) und `folders` (optional, Freigabe -> Ordner unter `/mnt/user` oder `/srv`) | im Hintergrund als Unit `dahlia-app-<id>`; die Ordner bleiben bei Updates erhalten |
| `apps.job` | `id` | Zustand der letzten Installation bzw. Aktualisierung |
| `apps.start`, `apps.stop` | `id` | starten bzw. stoppen, gilt auch für den nächsten Boot |
| `apps.remove` | `id`, `delete_data` (optional) | Container entfernen, Daten nur auf Wunsch |
| `files.browse` | `root` (`array` oder `system`), `path` (relativ dazu) | Unterordner für die Ordnerauswahl, ohne versteckte Ordner und Symlinks, höchstens 500; dazu, welche Speicherorte nutzbar sind |
| `files.mkdir` | `root`, `path` | neuen Ordner anlegen (Gruppe `dahlia`, 2775), nicht unter `appdata` |
| `domains.get` | - | Domain, mDNS-Aliase, nötige DNS-Einträge, Zertifikate |
| `domains.set` | `domain`, `mdns_aliases` | `dahlia-domains set`, Zertifikat und Proxy neu |
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

### Dashboard (ab Meilenstein 5)

Aufruf: `https://<server>.local/` (Port 80 leitet auf 443 um). Das Zertifikat stellt die
lokale Dahlia-CA aus (siehe "Netzwerk und Namen"). Bis die CA im Browser importiert ist,
warnt der Browser.

Beim ersten Aufruf gibt es nur den Einrichtungsassistenten. Er verlangt den Einmal-Code,
den der Server auf der Konsole (tty1 und serielle Konsole) und beim SSH-Login zeigt. Der
Assistent legt das Dashboard-Konto an und setzt optional den Servernamen. Das Konto gilt
nur für das Web-UI, nicht für SSH oder die Konsole.

| Teil | Umsetzung |
| --- | --- |
| Backend | FastAPI/uvicorn als `dahlia-ui` auf `127.0.0.1:8080`, systemd-Härtung (`ProtectSystem=strict`, keine Capabilities) |
| Frontend | statische Dateien unter `/usr/share/dahlia-dashboard/www`, von nginx ausgeliefert, Texte aus `locales/de.json` und `locales/en.json` |
| Konten | `/var/lib/dahlia-dashboard/users.json` (0600), Passwörter mit scrypt |
| Sitzungen | zufälliges Token im Speicher, Cookie `HttpOnly`, `Secure`, `SameSite=Strict`; 2 h Leerlauf, höchstens 12 h |
| Schutz | ändernde Anfragen brauchen den Header `X-Dahlia-Request`; 5 Fehlversuche pro IP in 5 Minuten, dann Sperre |
| root-Aktionen | nur über `dahlia-agent`; `dahlia-ui` ist Mitglied der Gruppe `dahlia-agent` |

Aufbau: Seitenleiste mit Servername und Version, Inhalt in Kacheln. Bestätigungen laufen
über einen eigenen Dialog, Meldungen erscheinen unten rechts. Unter 48rem Breite (Handy)
wird die Seitenleiste zum Menü, Tabellen werden zu Karten. Ein Web-App-Manifest erlaubt
„Zum Home-Bildschirm“ auf Handy und Tablet.

| Seite | Inhalt |
| --- | --- |
| Übersicht | System (Version, Kernel, CPU, Adresse, Neustart, Ausschalten), CPU- und RAM-Auslastung mit Verlauf, Netzwerk (Durchsatz je Schnittstelle), Belegung von System-SSD, Pools und jeder Platte unter `/mnt`, Platten mit Temperatur und SMART-Zustand, Apps mit Zustand, Aufgaben mit letztem Ergebnis |
| Platten | Belegung von `/mnt/user`, `/mnt/storage` und `/mnt/cache`; je physische Platte eine Karte mit Modell, Seriennummer, Temperatur, SMART-Zustand und ihren Dateisystemen (Status, Belegung, hinzufügen in einer Rolle, entfernen) |
| Apps | installierte Apps als Karten (Zustand, Adressen, Updates, Start/Stopp), Katalog mit Suche und Kategorien |
| Docker | alle Container (auch fremde) mit Zustand, Ports, CPU/RAM, Start/Stopp/Neustart, Logs; Platzbedarf und Aufräumen |
| Domains | Domain, mDNS-Aliase, DNS-Einträge, CA-Download |
| Aufgaben | Mover, SnapRAID-Sync und -Scrub starten, Zustand |
| System-Log | Journal mit Filter nach Stufe und Dienst |
| Updates | suchen, installieren |
| Freigaben | SMB-Freigaben auf `/mnt/user`, SMB-Benutzer |
| Einstellungen | Zeit und Zeitzone, Netzwerk (DHCP/fest), Platten-Spindown, Zeitpläne für Mover und SnapRAID, Benachrichtigungen, USV, Dashboard-Passwort, Servername |

Benachrichtigungen und USV (M9d):

- Kanäle: E-Mail (SMTP mit STARTTLS oder TLS), ntfy, Gotify. Zugangsdaten stehen in
  `/etc/dahlia/notify.json` (root, 0600); das Dashboard sieht nur, ob eines gesetzt ist.
- `dahlia-notify.timer` prüft alle 10 Minuten SMART, Plattentemperatur (ab 55 °C HDD,
  70 °C SSD) und Belegung (ab 90 %, kritisch ab 97 %). Gemeldet wird nur eine Änderung,
  ein behobenes Problem als "Resolved". Zustand in `/var/lib/dahlia/notify-state.json`.
- Mover, SnapRAID-Sync/-Scrub und Update haben `OnFailure=dahlia-notify-failure@%n`;
  die Meldung enthält die letzten 15 Journalzeilen.
- USV über NUT: USB (`usbhid-ups`, `upsd` nur auf localhost) oder ein NUT-Server im Netz
  (`netclient`). `upsmon` ruft bei jedem Ereignis `dahlia-notify ups-event` auf. Bei
  Akkubetrieb startet ein Timer `dahlia-ups-shutdown`, der nach der eingestellten Zeit
  ausschaltet; zurück am Netz wird er gestoppt. Ohne Zeit fährt `upsmon` bei schwachem
  Akku herunter.

SMB-Freigaben (M9c):

- Eine Freigabe ist ein Ordner direkt unter `/mnt/user` (Gruppe `dahlia`, Modus 2775,
  wie die Freigaben der Apps). Ohne eingehängtes `/mnt/user` legt Dahlia keine Freigabe an.
- `/etc/samba/smb.conf` wird aus `/etc/dahlia/shares.json` erzeugt, das Debian-Original
  bleibt als `smb.conf.dahlia-orig`. Mindestens SMB2, keine Drucker, kein NetBIOS (`nmbd` aus).
- Zugriff: öffentlich (Gast liest und schreibt), geschützt (Gast liest, ausgewählte Benutzer
  schreiben), privat (nur ausgewählte Benutzer). Dateien bekommen die Gruppe `dahlia`.
- SMB-Benutzer sind Systemkonten ohne Shell und Home in den Gruppen `dahlia-smb` und
  `dahlia`. Das Passwort geht nur über stdin an `smbpasswd`.
- Finden im Netz: `wsdd` für Windows, eine Avahi-Datei (`_smb._tcp`, bei Time Machine
  `_adisk._tcp`) für macOS. Ohne Freigabe sind `smbd` und `wsdd` aus.

Einstellungen (M9b):

- Netzwerk: `settings.set_network` schreibt die neue Konfiguration nach
  `/etc/network/interfaces.dahlia-new` und startet `dahlia-network apply` als
  transiente Unit. Diese nimmt das Interface mit der alten Konfiguration herunter,
  tauscht die Dateien und startet es neu. Wird die Änderung nicht innerhalb von
  180 Sekunden über `settings.confirm_network` bestätigt, stellt sie die alte
  Konfiguration wieder her.
- Spindown: Standard für alle drehenden Platten plus Werte pro Seriennummer.
  `dahlia-spindown.service` wendet sie beim Booten an, eine udev-Regel bei neu
  angeschlossenen Platten. SSDs und NVMe werden übersprungen.
- Zeitpläne: Drop-ins unter `/etc/systemd/system/<timer>.d/dahlia-schedule.conf`,
  gespeicherte Werte in `/etc/dahlia/schedules.json`. „Aus“ deaktiviert den Timer.

Die Live-Werte liefert die Agent-Aktion `system.stats` (`/proc`, `statvfs`, `ip -j addr`);
die Übersicht fragt sie alle 3 Sekunden ab, solange sie offen ist. Die CPU-Auslastung
rechnet das Frontend aus zwei Messungen.

Passwort vergessen: `sudo rm /var/lib/dahlia-dashboard/users.json` und
`sudo systemctl restart dahlia-dashboard` öffnen den Einrichtungsassistenten mit neuem Code.

### Terminal (ab Meilenstein 12)

Die Seite Terminal im Dashboard ist eine Shell im Browser (xterm.js, MIT, unter
`www/vendor/xterm`, Canvas-Renderer wegen der CSP). Ablauf:

1. Browser → `wss://<server>/api/terminal` (nginx mit Upgrade-Headern). Das Backend prüft
   die Dashboard-Sitzung und dass `Origin` der eigene Server ist.
2. Backend → Unix-Socket `/run/dahlia-terminal/terminal.sock` (Gruppe `dahlia-ui`, 0660).
3. `dahlia-terminal.service` läuft als `dahlia-term` ohne Rechte und startet je Sitzung
   `su -l <Benutzer>` in einem Pseudo-Terminal. `su` fragt das Linux-Passwort; Root-Rechte
   gibt es danach mit `sudo` wie bei SSH. Erlaubt sind nur Konten ab UID 1000 mit
   Login-Shell, voreingestellt `dahlia-admin`.

Höchstens 4 Sitzungen, Trennung nach 15 Minuten ohne Eingabe. Beginn und Ende jeder
Sitzung (Linux-Konto, Dashboard-Konto, IP) stehen im Journal von `dahlia-terminal`.
Ein Update von `dahlia-dashboard` startet das Backend neu und trennt offene Sitzungen;
Paket-Updates daher über die Update-Seite oder in `tmux`/`screen`.

### Apps (ab Meilenstein 6)

Apps sind Docker-Container aus App-Vorlagen. Der Katalog liegt im Repo
[dahlia-apps](https://github.com/Fabiy99/dahlia-apps) und wird von
`https://raw.githubusercontent.com/Fabiy99/dahlia-apps/main/` geladen
(`/etc/dahlia/apps.json`, auch `file:///` für eigene Kataloge).

Eine Vorlage (YAML) beschreibt eine App. Ab `schema: 2` ist das ein kleiner Stack: der
Hauptdienst `app` und Hilfsdienste wie eine Datenbank oder Redis. Sie werden zusammen
installiert, aktualisiert und entfernt. Je Dienst gibt es nur: Image mit fester Version,
LAN-Ports, Volumes unter `appdata` oder einer Freigabe, Umgebungsvariablen, `command`,
`user` und `depends_on`. Dazu kommen Kategorie, Beschreibung, Hinweise, Webseite und
Zugangsdaten für das Dashboard. Alles andere wird abgelehnt, also auch `privileged`,
Host-Netzwerk, Geräte und Capabilities. `schema: 1` (ein Container) wird weiter gelesen.

Platzhalter in Umgebung, `command` und Zugangsdaten: `{TZ}`, `{PUID}`, `{PGID}`,
`{HOSTNAME}`, `{DOMAIN}` und `{SECRET_<NAME>}`. Secrets sind Zufallswerte (32 Hex-Zeichen),
die bei der Installation entstehen und in `/var/lib/dahlia/apps/<id>/secrets.json` (0600)
liegen; bei Updates bleiben sie gleich. Das Dashboard zeigt Zugangsdaten an, die die
Vorlage unter `login` nennt.

| Punkt | Umsetzung |
| --- | --- |
| Web-Port | Docker veröffentlicht ihn auf `127.0.0.1:41000-41999`; nginx lauscht im LAN auf dem öffentlichen Port der Vorlage (Port-Link `http://<server>:<port>/`) und auf 443 für `<id>.<domain>` und `<id>.local` |
| LAN-Ports | nur wenn die Vorlage sie nennt; das Dashboard zeigt sie vor der Installation |
| Speicherort | Array `/mnt/user` oder System-SSD `/srv` (FHS: Daten, die das System bereitstellt). Wahl bei der Installation, ohne Array automatisch `/srv`. Apps von vor 0.7 liegen auf dem Array |
| System-SSD | `/srv` ist auf Btrfs ein eigenes Subvolume `@srv` auf oberster Ebene wie bei openSUSE, eingehängt über `srv.mount` (angelegt von `/usr/lib/dahlia/system-data.sh` im postinst). Es gehört nicht zu den snapper-Snapshots von `/`: ein Rollback setzt App-Daten nicht zurück und verliert sie nicht. Auf anderen Dateisystemen ein normaler Ordner |
| Daten | `<Ort>/appdata/<id>/...` (Besitzer `dahlia-apps`), Freigaben `<Ort>/<name>`; Apps auf dem Array starten nur mit eingehängtem Array |
| Einrichtung | Das Dashboard zeigt vor der Installation die Voraussetzungen und die Freigaben der App. Man wählt den Speicherort; "Ändern" öffnet einen Ordnerbaum auf System-SSD oder Array (mit "Neuer Ordner"), auch gemischt; der Pfad im Container bleibt gleich |
| Start | Docker startet nichts selbst (`restart: no`); `dahlia-apps.service` startet die aktivierten Apps nach den Platten |
| Container | `no-new-privileges`, Log-Treiber `local` mit 3 x 10 MB; Hauptdienst `dahlia-<id>`, Hilfsdienste `dahlia-<id>-<dienst>` |
| Stack | eigenes Docker-Netz je App, Dienste erreichen sich über den Dienstnamen; Hilfsdienste veröffentlichen keine Ports |
| Katalog | `catalog.json` (alle Vorlagen in einer Datei), sonst `index.json` und `apps/<id>.yaml`; Vorlagen mit neuerem Schema werden übersprungen |
| Updates | `dahlia-apps-catalog.timer` lädt den Katalog alle 6 Stunden; weicht die Vorlage einer installierten App ab, zeigt das Dashboard "Update auf <Version>" und "Alle aktualisieren". Alte Images werden danach gelöscht |

```sh
dahlia-apps catalog --refresh
dahlia-apps install jellyfin [--location /srv] [--folder media=/mnt/user/Filme/Serien]
dahlia-apps list
dahlia-apps updates                # Apps mit neuerer Vorlage im Katalog
dahlia-apps update jellyfin
dahlia-apps remove jellyfin [--delete-data]
dahlia-apps check app.yaml        # Vorlage prüfen
```

## Netzwerk und Namen (ab Meilenstein 7)

| Name | Weg | Voraussetzung |
| --- | --- | --- |
| `https://<server>.local/` | Dashboard, Avahi-Hostname | keine |
| `https://<domain>/` | Dashboard | DNS-Eintrag `<domain>` auf den Server |
| `https://<app>.<domain>/` | App über nginx | DNS-Eintrag `*.<domain>` auf den Server |
| `https://<app>.local/` | App über nginx, mDNS-Alias | mDNS-Aliase eingeschaltet |
| `http://<server>.local:<port>/` | Port-Link | keine |

Die Domain ist standardmäßig `dahlia.home.arpa` (RFC 8375) und steht in
`/etc/dahlia/domains.json`. Mehrstufige `.local`-Namen (`jellyfin.dahlia.local`) werden
abgelehnt, weil mDNS-Clients sie nicht zuverlässig auflösen. Die DNS-Einträge legt man im
lokalen DNS an, z. B. in AdGuard Home unter Filter > DNS-Umschreibungen
(`*.dahlia.home.arpa` -> IP des Servers). Das Dashboard zeigt die Einträge mit der
aktuellen Adresse.

mDNS-Aliase: `dahlia-mdns.service` (ohne Rechte, Benutzer `dahlia-mdns`) veröffentlicht für jede App
mit Web-Oberfläche `<app>.local` per `avahi-publish` auf der primären IPv4-Adresse. Ändert
sich die Adresse, folgen die Aliase nach höchstens 10 Sekunden. Eine App, die so heißt wie
der Server, bekommt keinen Alias.

Zertifikate (`dahlia-domains`, Teil von `dahlia-agent`):

| Datei | Inhalt |
| --- | --- |
| `/etc/dahlia/tls/ca.key`, `ca.crt` | lokale CA, EC P-256, 20 Jahre; Schlüssel nur für root lesbar |
| `/etc/dahlia/tls/server.key`, `server.crt` | Server-Zertifikat für nginx, 825 Tage |

Die CA hat Namensbeschränkungen (`nameConstraints`): sie kann nur Namen unter `.local`,
`home.arpa`, der eigenen Domain und private IPv4-Adressen (10/8, 172.16/12, 192.168/16)
signieren. Wer sie importiert, vertraut ihr also für keine anderen Websites. Das
Server-Zertifikat enthält `<server>.local`, `<domain>`, `*.<domain>`, die mDNS-Aliase und
die privaten IPv4-Adressen des Servers. `dahlia-domains.timer` prüft täglich und nach dem
Boot: neue Adresse, neue App, weniger als 30 Tage Restlaufzeit führen zu einem neuen
Zertifikat. Eine Domain außerhalb von `home.arpa` braucht eine neue CA; das Dashboard sagt
dann, dass die CA neu importiert werden muss.

CA importieren: im Dashboard unter Domains `dahlia-ca.crt` herunterladen und den
SHA-256-Fingerabdruck mit der Anzeige nach dem Login (Konsole oder SSH) vergleichen.
Danach unter Windows mit `certutil -addstore -f Root dahlia-ca.crt` (als Administrator),
unter macOS in der Schlüsselbundverwaltung (System, "Immer vertrauen"), unter Debian/Ubuntu
nach `/usr/local/share/ca-certificates/dahlia-ca.crt` kopieren und `update-ca-certificates`;
Firefox nutzt seinen eigenen Speicher (Einstellungen > Zertifikate > Importieren).

```sh
dahlia-domains show
dahlia-domains set --domain lab.home.arpa --mdns off
dahlia-domains apply              # Zertifikat und Proxy prüfen, nur bei Bedarf neu
```

## Updates

- Debian-Sicherheitsupdates über `unattended-upgrades`.
- Dahlia-Pakete und übrige Updates per Klick im UI, immer mit Snapshot vorher.
- Das apt-Repo wird in CI gebaut, signiert und auf GitHub Pages veröffentlicht
  (`https://fabiy99.github.io/dahlia-linux/`). Der private Schlüssel existiert nur als
  GitHub-Secret. Details: [apt-repo.md](apt-repo.md).
