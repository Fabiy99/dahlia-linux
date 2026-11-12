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

Rollback (ab Meilenstein 18): Das Dashboard listet die Snapshots unter Updates, legt neue an,
löscht sie und setzt das System auf einen zurück. `snapper rollback` wirkt bei Debians
Layout nicht, weil fstab und GRUB `@rootfs` beim Namen einhängen. Deshalb macht
`/usr/lib/dahlia/rollback.sh N` (als `dahlia-rollback.service`) es wie `snapper-rollback`:
beschreibbarer Snapshot von `.snapshots/N/snapshot` neben der Wurzel, die laufende Wurzel
wird zu `@rootfs.pre-rollback-<Zeit>`, der neue Snapshot heißt `@rootfs`, `.snapshots` zieht
mit um, dann Neustart. Aus dem laufenden System übernommen (Reflink-Kopie) werden `/home`,
`/root`, `/var/log`, `/etc/dahlia`, `/var/lib/dahlia`, `/var/lib/dahlia-dashboard` und
`/var/lib/docker`; `/srv` (`@srv`) und das Array gehören ohnehin nicht dazu. Der alte Stand
bleibt, bis man ihn im Dashboard löscht (`rollback.sh --delete-old NAME`).

Beim ersten Start zeigt das Dashboard nur den Einrichtungsassistenten. Er ist mit einem
Einmal-Code geschützt, der auf tty1 und in `/etc/motd` steht (ab Meilenstein 5).

## Pakete

| Paket | Inhalt |
| --- | --- |
| `dahlia-base` | Konfiguration, Gruppe `dahlia` (GID 1000), zieht alle Dahlia-Pakete nach. Maskiert `systemd-ssh-generator` (SSH über AF_VSOCK), der unter Hyper-V bei jedem `daemon-reload` Fehler auf die Konsole schreibt; SSH läuft normal über `sshd` |
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
| `/mnt/remotes/<Name>` | SMB- und NFS-Freigaben anderer Geräte (M24), nur für App- und Backup-Ordner |

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
dahlia-snapraid fix --disk N                   # Dateien von /mnt/diskN aus der Parity zurückholen, prüfen
dahlia-snapraid fix --bad                      # vom Scrub gefundene defekte Blöcke reparieren
dahlia-snapraid status [--json]                # Abdeckung, Scrub-Stand, Fehler, letzte Läufe
dahlia-disk set-option snapraid_delete_threshold 50
dahlia-disk set-option mover_min_age_minutes 1440
dahlia-disk set-minfreespace 100G               # freier Platz pro Platte für mergerfs
dahlia-disk settings                           # alle Einstellungen als JSON
```

Dieselben Werte setzt das Dashboard unter Platten > Einstellungen (ab Meilenstein 17).

Der Mover lässt offene, kürzlich geänderte und schon auf den Datenplatten vorhandene
Dateien sowie Hardlinks auf dem Cache. Er kopiert erst, prüft, benennt um und löscht dann.
Timer: Mover 03:00, SnapRAID-Sync 04:30, Scrub sonntags 06:00 (8 %, Blöcke älter als
10 Tage). Mover und SnapRAID teilen sich `/run/dahlia/array.lock`.

Parity im Dashboard (ab Meilenstein 14): Seite "Parity" mit Zustand, Platten, Sync und Scrub
von Hand, Einstellungen (Lösch-Schwelle, Scrub-Anteil und -Alter) und den Reparaturen.
Ergebnis und Zähler der letzten Läufe stehen in `/var/lib/dahlia/snapraid.json`;
`snapraid status` läuft nur, wenn sich die Content-Datei geändert hat. Hat ein Sync wegen
der Lösch-Schwelle angehalten, kann er nach Rückfrage mit `--force` laufen. Findet ein Scrub
defekte Blöcke, endet er mit Fehler (Benachrichtigung) und `fix --bad` repariert sie.

Ausgefallene Datenplatte ersetzen: die alte Platte unter Platten entfernen, die neue
formatieren und als Datenplatte hinzufügen. Sie bekommt die kleinste freie Nummer, also die
der alten. Danach `fix --disk N`: `snapraid -d dN fix` (Protokoll in
`/var/log/dahlia/snapraid-fix.log`) und `snapraid -d dN -a check`. Wiederhergestellt wird
der Stand des letzten Syncs. Bis dahin verweigert SnapRAID den Sync, weil dN fehlt bzw. leer
ist. Erzwungener Sync und Reparaturen laufen als Unit `dahlia-parity.service`.

## Rechte und Sicherheit

- `dahlia-dashboard` läuft als `dahlia-ui` mit systemd-Härtung und hört nur auf
  `127.0.0.1`. nginx macht TLS auf Port 443.
- `dahlia-agent` läuft als root und nimmt nur Aktionen aus seiner Liste an. Jede Aktion
  prüft ihre Parameter.
- Docker veröffentlicht Container-Ports nur auf `127.0.0.1` (`daemon.json`: `"ip"`).
  Apps sind über nginx erreichbar. Apps, die LAN-Ports brauchen, sind im App-Template
  markiert.
- Die Firewall (nftables) öffnet Ports nur für aktivierte Funktionen und nur fürs lokale
  Netz, siehe [Firewall](#firewall-ab-meilenstein-10).

### Firewall (ab Meilenstein 10)

`dahlia-agent` verwaltet genau eine Tabelle, `inet dahlia`. Sie wird aus dem erzeugt, was
in Benutzung ist, und als Ganzes in einer Transaktion ersetzt; andere Tabellen (Docker)
bleiben unberührt. `dahlia-firewall.service` lädt sie beim Booten vor dem Netzwerk,
`/etc/network/if-up.d/dahlia-firewall` nach jedem Interface-Start, Apps und Freigaben nach
jeder Änderung.

- Eingehend (`input`, Policy drop): bestehende Verbindungen, `lo`, ICMPv6, DHCP und Ping
  aus dem LAN. Neue Verbindungen nur von lokalen Quellen: private IPv4-Netze
  (`10/8`, `172.16/12`, `192.168/16`, `100.64/10`, `169.254/16`), `fc00::/7`, `fe80::/10`,
  die Subnetze der eigenen Interfaces (auch das IPv6-Präfix des Providers) und die
  zusätzlich eingetragenen Netze (z. B. VPN).
- Offene Ports: 22 (SSH), 80/443 (nginx), 5353/udp (mDNS); 445, 5357 und 3702/udp nur,
  solange Freigaben existieren; 2049 (NFS), solange eine Freigabe über NFS geht; der Port-Link und die LAN-Ports jeder installierten App.
  Geschlossene Ports antworten dem LAN sofort mit `admin-prohibited`.
- Weitergeleitet (`forward`): Ports, die Docker per DNAT an Container gibt, nur für lokale
  Quellen.

Einstellungen in `/etc/dahlia/firewall.json` (`enabled`, `networks`), im Dashboard unter
Einstellungen. Das geladene Regelwerk liegt in `/run/dahlia/firewall.nft`.

```
dahlia-firewall status     # Einstellungen, lokale Netze, offene Ports (JSON)
dahlia-firewall show       # Regelwerk, das apply laden würde
dahlia-firewall apply      # neu laden
sudo dahlia-firewall disable   # Notausgang: Tabelle entfernen
```

### Fernzugriff: WireGuard und Tailscale (ab Meilenstein 20)

WireGuard: Dahlia betreibt das Interface `dahlia-wg` mit `wg-quick@dahlia-wg.service`.
Einstellungen und Geräte liegen in `/etc/dahlia/wireguard.json` (0600), der Schlüssel des
Servers in `/etc/wireguard/dahlia-wg.key`, daraus entsteht `/etc/wireguard/dahlia-wg.conf`.
Für ein Gerät erzeugt der Server Schlüsselpaar und Preshared Key; Konfiguration und QR-Code
(`qrencode`) zeigt das Dashboard einmal an, gespeichert wird nur der öffentliche Schlüssel.
Neue oder entfernte Geräte lädt `wg syncconf`, ohne die anderen Tunnel zu trennen.

| Punkt | Umsetzung |
| --- | --- |
| Netz | Tunnel `10.253.0.0/24` (änderbar, solange keine Geräte da sind, darf kein lokales Netz überlappen), Server `.1`, Geräte ab `.2` |
| Routing | "nur das Heimnetz": Tunnel und die IPv4-Subnetze des Servers; "ganzer Verkehr": `0.0.0.0/0, ::/0`. DNS für Clients optional, z. B. AdGuard Home |
| Firewall | UDP-Port (Standard 51820) offen aus dem Internet, Masquerading des Tunnelnetzes Richtung LAN in `inet dahlia`, auch bei abgeschalteter Firewall. Der Tunnel zählt als lokal (`10/8`) |
| Weiterleitung | `/etc/sysctl.d/60-dahlia-forward.conf` (`net.ipv4.ip_forward = 1`). Docker setzt die iptables-Kette `FORWARD` auf DROP; `/usr/lib/dahlia/wireguard-forward.sh` (PostUp/PostDown) erlaubt den Tunnel in `DOCKER-USER` |
| Router | UDP-Port an den Server weiterleiten; die Adresse im Internet (DynDNS-Name oder IP) trägt man im Dashboard ein |

Tailscale ist nicht in Debian. "Installieren" startet `dahlia-tailscale-install.service`
(`/usr/lib/dahlia/tailscale-install.sh`): Schlüssel nach
`/usr/share/keyrings/tailscale-archive-keyring.gpg`, Quelle
`/etc/apt/sources.list.d/tailscale.list`, Paket `tailscale`, wie in der Anleitung von
Tailscale. "Verbinden" startet `tailscale up` als `dahlia-tailscale-up.service` (10 Minuten
Zeit zum Anmelden); den Anmelde-Link liest das Dashboard aus `tailscale status --json`.
Optionen: die IPv4-Subnetze des Servers als Route anbieten (in der Tailscale-Verwaltung
freigeben) und Exit-Node. Tailscale-Adressen (`100.64/10`) zählen für die Firewall als lokal.

Debians `nftables.service` bleibt aus; sein `/etc/nftables.conf` beginnt mit
`flush ruleset` und würde auch Dockers Regeln löschen.

### dahlia-agent (ab Meilenstein 4)

Der Agent hört auf `/run/dahlia/agent.sock` (root:dahlia-agent, 0660) und wird von
`dahlia-agent.socket` beim ersten Aufruf gestartet. Zusätzlich zu den Dateirechten prüft er
die Gegenstelle per `SO_PEERCRED`: erlaubt sind root und Mitglieder der Gruppe
`dahlia-agent`, dazu gehört der Dashboard-Benutzer `dahlia-ui`.

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
| `history.get` | `range` (`hour`/`day`/`week`/`month`/`year`) | Verlauf aus `/var/lib/dahlia/history.db`, gemittelt pro Schritt (1 min bis 1 Tag) |
| `history.influx` | – | InfluxDB-Ziel ohne Token/Passwort, Ergebnis des letzten Exports |
| `history.set_influx` | `enabled`, `url`, `version` (1/2), `org`, `bucket`, `token` bzw. `database`, `username`, `password` (weggelassen = bisheriges) | Testschreiben, dann `/etc/dahlia/history.json` (0600) |
| `system.hardware` | - | Mainboard (DMI), CPU-Temperatur (hwmon), SMART je Platte mit `smartctl -n standby` (weckt keine schlafende Platte), laufender und letzter Selbsttest |
| `disks.smart_test` | `device`, `kind` (`short`/`long`/`abort`) | `smartctl -t short\|long` bzw. `smartctl -X` |
| `disks.burnin_start` | `device`, `confirm` (Plattenname) | Plattentest als `dahlia-burnin@<name>.service`, nur auf leeren Platten |
| `disks.burnin_status`, `disks.burnin_stop`, `disks.burnin_clear` | bei `stop`/`clear`: `device` | Fortschritt und Ergebnis, abbrechen, Ergebnis löschen |
| `system.log` | `lines`, `priority`, `unit` (optional) | `journalctl -n … -p …` |
| `system.diagnostics` | – | Diagnosepaket nach `/run/dahlia-dashboard/diagnostics.tar.gz` (0600, Dashboard-Benutzer) |
| `settings.time` / `settings.set_time` | `timezone`, `ntp` | `timedatectl set-timezone`, `set-ntp` |
| `settings.network` / `settings.set_network` | `mode` (`dhcp`/`static`), `address` (CIDR), `gateway`, `dns` | `/etc/network/interfaces`, Anwendung über `dahlia-network` mit Rückfall |
| `settings.confirm_network` | – | bestätigt die neue Netzwerkeinstellung |
| `settings.spindown` / `settings.set_spindown` | `default`, `disks` (Seriennummer → Minuten) | `/etc/dahlia/spindown.json`, `hdparm -S` |
| `vpn.status` | - | WireGuard (Einstellungen, Geräte mit letztem Handshake und Daten) und Tailscale (installiert, Zustand, Anmelde-Link, Adressen) |
| `vpn.wireguard_set` | `enabled`, `port`, `endpoint`, `route` (`lan`/`all`), `dns`, `subnet` | schreibt die Konfiguration, lädt Firewall und Interface |
| `vpn.wireguard_add_peer`, `vpn.wireguard_remove_peer` | `name` | neues Gerät (Konfiguration und QR-Code einmal) bzw. entfernen |
| `vpn.tailscale_install`, `vpn.tailscale_up`, `vpn.tailscale_logout` | bei `up`: `routes`, `exit_node` | Tailscale installieren, verbinden, abmelden |
| `settings.firewall` / `settings.set_firewall` | `enabled`, `networks` (CIDR-Liste, höchstens 20) | `/etc/dahlia/firewall.json`, `nft -c -f` und `nft -f` mit `/run/dahlia/firewall.nft` |
| `settings.schedules` / `settings.set_schedule` | `job`, `frequency`, `time`, `weekday`, `monthday` | Drop-in `OnCalendar=` für die Timer |
| `shares.list` | – | Freigaben, SMB-Benutzer, ob `/mnt/user` eingehängt ist, ob `smbd` und `nfs-server` laufen |
| `shares.set` / `shares.remove` | `name`, `comment`, `security` (`public`/`secure`/`private`), `users` (Name → `rw`/`ro`), `time_machine`, `time_machine_size` (GB, 0 = ohne Grenze), `nfs` | erzeugt `/etc/samba/smb.conf`, prüft mit `testparm`, startet `smbd`; bei `nfs` `/etc/exports.d/dahlia.exports`, `exportfs -ra`, `nfs-server` |
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
| `storage.settings` | – | `dahlia-disk settings` |
| `storage.set_minfreespace` | `size` (z. B. `100G`, M/G/T) | `dahlia-disk set-minfreespace` |
| `storage.format` | `device` (ganze Platte), `filesystem` (`xfs`, `ext4`, `btrfs`), `confirm` (Plattenname wiederholt) | `dahlia-disk format`, nur leere oder übernommene Platten |
| `storage.import` / `storage.import_status` | `uuid`, `target` (Name unter `/mnt/user`) | `dahlia-disk import` als Unit `dahlia-import`, Fortschritt in `/run/dahlia/import.json` |
| `job.start` | `job` (mover, snapraid-sync, snapraid-scrub, backup) | startet die Unit, kehrt sofort zurück |
| `job.status` | `job` (dazu `update`) | Zustand und letztes Ergebnis |
| `parity.get` | – | `dahlia-snapraid status --json` und Zustand von Sync, Scrub, Mover und Reparatur |
| `parity.sync` | `force` (optional) | Sync starten; mit `force` ohne Lösch-Schwelle als `dahlia-parity.service` |
| `parity.fix` | `what` (`bad` oder `diskN`) | `dahlia-snapraid fix --bad` bzw. `--disk N` als `dahlia-parity.service` |
| `updates.check` | - | `apt-get update`, Liste der Upgrades |
| `updates.apply` | - | `apt-get full-upgrade` über die Unit `dahlia-update.service`; die geplanten Pakete (Simulation) in `/run/dahlia/update.json` |
| `updates.auto` | - | Schalter und Neustart-Einstellungen, letzter Lauf von unattended-upgrades, ausstehender Neustart |
| `snapshots.list` | – | `snapper --jsonout list`, `btrfs subvolume list /`, `/var/lib/dahlia/rollback.json` |
| `snapshots.create` | `description` | `snapper create --cleanup-algorithm number` |
| `snapshots.delete` | `number` | `snapper delete` |
| `snapshots.rollback` | `number` | `dahlia-rollback.service` → `rollback.sh N --reboot` |
| `snapshots.delete_old_root` | `name` (`<subvol>.pre-rollback-<Zeit>`) | `rollback.sh --delete-old` |
| `updates.set_auto` | `enabled`, `reboot`, `reboot_time` (`HH:MM`) | `20auto-upgrades`, `52dahlia-auto-reboot`, `/etc/dahlia/updates.json` |
| `updates.progress` | - | Zustand, Prozent, je Paket wartet/geladen/eingerichtet/fertig aus der apt-Ausgabe im Journal dieses Laufs; das Dashboard zeigt es auf der Update-Seite |
| `apps.catalog` | `refresh` (optional) | App-Katalog und sein Stand, mit `refresh` vorher herunterladen |
| `apps.list` | - | installierte Apps mit Container-Zustand, Version, verfügbarem Update |
| `apps.install`, `apps.update` | `id`, bei `install` `location` (optional, `/mnt/user` oder `/srv`) und `folders` (optional, Freigabe -> Ordner unter `/mnt/user` oder `/srv`) | im Hintergrund als Unit `dahlia-app-<id>`; die Ordner bleiben bei Updates erhalten |
| `apps.custom_install`, `apps.custom_update` | `id`, `spec` (Formular, siehe Apps), bei `install` `location` | eigener Container; der Agent legt `spec` unter `/run/dahlia/apps-custom/<id>.json` ab, `dahlia-apps` prüft jedes Feld |
| `apps.job` | `id` | Zustand der letzten Installation bzw. Aktualisierung |
| `apps.start`, `apps.stop` | `id` | starten bzw. stoppen, gilt auch für den nächsten Boot |
| `apps.remove` | `id`, `delete_data` (optional) | Container entfernen, Daten nur auf Wunsch |
| `remotes.list` | – | eingebundene Freigaben anderer Geräte mit Zustand (`mounted`, `idle`, `error`, `off`), ohne Passwort |
| `remotes.set` | `name`, `type` (`smb`/`nfs`), `host`, `share`, `username`, `password` (weggelassen = bisheriges), `readonly` | Probe-Einhängen, dann `/etc/dahlia/remotes.json`, Zugangsdaten `/etc/dahlia/remotes/<Name>.cred` (0600), `.mount`- und `.automount`-Unit |
| `remotes.remove` | `name`, `force` | Units und Zugangsdaten löschen; ohne `force` nicht, solange eine App einen Ordner darauf nutzt |
| `files.browse` | `root` (`/mnt/user`, `/srv` oder `/mnt/remotes`), `path` (relativ dazu) | Unterordner für die Ordnerauswahl, ohne versteckte Ordner und Symlinks, höchstens 500; dazu, welche Speicherorte nutzbar sind |
| `files.mkdir` | `root`, `path` | neuen Ordner anlegen (Gruppe `dahlia`, 2775), nicht unter `appdata` |
| `domains.get` | - | mDNS-Aliase, Namen in den Zertifikaten, Zertifikate |
| `domains.set` | `mdns_aliases` | `dahlia-domains set`, App-Zertifikat und Proxy neu |
| `backup.get` | – | Ziel, letztes Ergebnis, öffentlicher SSH-Schlüssel, mögliche Zielplatten (`/mnt/diskN`, `/mnt/cache`, `/mnt/user`, `/media/…`) |
| `backup.set` | `target` (`{"type": "folder", "path": …}` oder `{"type": "sftp", "host", "port", "user", "path"}`), `stop_apps`, `folders` (optional, weitere Ordner unter `/mnt/user` oder `/srv`) | `/etc/dahlia/backup.json`, legt Passwort und SSH-Schlüssel an, schaltet `dahlia-backup.timer` ein |
| `backup.key` | – | Passwort des Repositorys |
| `backup.snapshots` | – | `restic snapshots` |
| `backup.restore` | `snapshot`, `what` (`config` oder App-ID) | im Hintergrund als Unit `dahlia-backup-restore` |
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
| Zweiter Faktor | optional TOTP (RFC 6238, SHA-1, 6 Stellen, 30 s, ±1 Schritt), jeder Code nur einmal; 10 Wiederherstellungscodes (SHA-256, je einmal); QR-Code über `qrencode` |
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
| Platten | Belegung von `/mnt/user`, `/mnt/storage` und `/mnt/cache`; je physische Platte eine Karte mit Modell, Seriennummer, Temperatur, SMART-Zustand, letztem Selbsttest (Kurz-/Langtest starten), Plattentest bei leeren Platten und ihren Dateisystemen (Status, Belegung, hinzufügen in einer Rolle, entfernen) |
| Apps | installierte Apps als Karten (Zustand, Adressen, Updates, Start/Stopp), Katalog mit Suche und Kategorien, eigene Container |
| Docker | alle Container (auch fremde) mit Zustand, Ports, CPU/RAM, Start/Stopp/Neustart, Logs; Platzbedarf und Aufräumen |
| Adressen | mDNS-Aliase an/aus, CA-Download und Zertifikate |
| Aufgaben | Mover, SnapRAID-Sync und -Scrub starten, Zustand |
| System-Log | Journal mit Filter nach Stufe und Dienst |
| Updates | suchen, installieren |
| Freigaben | SMB- und NFS-Freigaben auf `/mnt/user`, Time Machine, SMB-Benutzer |
| Einstellungen | Zeit und Zeitzone, Netzwerk (DHCP/fest), Platten-Spindown, Zeitpläne für Mover, SnapRAID, Backup und SMART-Selbsttests, Benachrichtigungen, USV, Fernzugriff (WireGuard, Tailscale), Dashboard-Passwort, Servername |

SMART-Selbsttests und Plattentest (M23):

- Selbsttests laufen in der Platte selbst, sie bleibt dabei nutzbar. `dahlia-smart-short.timer`
  (wöchentlich, Samstag 05:00) und `dahlia-smart-long.timer` (Standard aus, ein Langtest einer
  großen HDD dauert einen Tag) starten `dahlia-smart-test@short|long.service`, das den Test auf
  jeder Platte anstößt. Zeiten unter Einstellungen → Zeitpläne. Ein Kurztest weckt schlafende
  Platten.
- Ergebnis aus dem Selbsttest-Protokoll (ATA und NVMe): laufend mit Restprozent, letzter Test
  mit Art, Ergebnis und Betriebsstunden. Ein fehlgeschlagener Test geht als kritische Meldung
  raus.
- Plattentest vor dem Einsatz: nur für Platten ohne Partition und Dateisystem (Status `empty`),
  `confirm` wiederholt den Namen. `/usr/lib/dahlia/disk-burnin.sh` liest SMART, schreibt mit
  `badblocks -w` vier Muster über die ganze Platte und liest sie zurück, liest SMART erneut.
  Bestanden heißt: keine defekten Blöcke, kein SMART-Zähler gewachsen. Dateien unter
  `/var/lib/dahlia/burnin`. Solange er läuft, lässt sich die Platte nicht formatieren.

Benachrichtigungen und USV (M9d):

- Kanäle: E-Mail (SMTP mit STARTTLS oder TLS), ntfy, Gotify. Zugangsdaten stehen in
  `/etc/dahlia/notify.json` (root, 0600); das Dashboard sieht nur, ob eines gesetzt ist.
- `dahlia-notify.timer` prüft alle 10 Minuten SMART (auch einen fehlgeschlagenen Selbsttest), Plattentemperatur (ab 55 °C HDD,
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
- Time Machine: `fruit:time machine` pro Freigabe, optional mit Größengrenze
  (`fruit:time machine max size`). Mehrere Macs auf einer Freigabe teilen sich die Grenze.

NFS-Freigaben (M21):

- Eine öffentliche oder geschützte Freigabe kann zusätzlich über NFS gehen: öffentlich
  lesen und schreiben, geschützt nur lesen. Private Freigaben nie, denn NFS hat ohne Kerberos
  keine Anmeldung.
- Nur NFSv4 (`/etc/nfs.conf.d/dahlia.conf`: `vers3=n`, `udp=n`), damit reicht 2049/tcp;
  rpcbind und mountd bleiben hinter der Firewall.
- `/etc/exports.d/dahlia.exports` wird aus `shares.json` erzeugt. Erlaubt sind dieselben Netze
  wie in der Firewall: die privaten Bereiche und die zusätzlich eingetragenen Netze (eine
  Änderung dort schreibt die Exports neu). Optionen: `all_squash`, `anonuid=65534`
  (nobody), `anongid` = Gruppe `dahlia`, `sync`, `no_subtree_check` und eine feste `fsid`
  aus dem Namen, weil nfsd bei mergerfs (FUSE) keine Gerätenummer hat. mergerfs läuft dafür
  schon mit `inodecalc=path-hash` und `noforget`.
- `nfs-server` läuft nur, solange eine Freigabe über NFS geht.
- Einhängen unter Linux: `mount -t nfs4 dahlia.local:/mnt/user/<Name> /mnt/ziel`.

Freigaben anderer Geräte (M24):

- Unter Freigaben → *Freigaben anderer Geräte* lassen sich SMB-Freigaben (NAS, Windows) und
  NFS-Exporte einbinden. Sie liegen unter `/mnt/remotes/<Name>` und erscheinen in der
  Ordnerauswahl als eigener Speicherort, etwa für die Medienordner einer App. App-Daten
  (`appdata`) liegen nie dort.
- Vor dem Speichern hängt der Agent die Freigabe einmal probeweise unter
  `/run/dahlia/remote-test` ein; ein falsches Passwort oder ein falscher Pfad wird sofort
  gemeldet und nichts gespeichert.
- Wie bei den Platten keine `/etc/fstab`: je Freigabe eine `.mount`- und eine
  `.automount`-Unit in `/etc/systemd/system` mit `_netdev,nofail` und 30 s Zeitlimit. Ein
  ausgeschaltetes Gerät hält den Start nicht auf; eingehängt wird beim ersten Zugriff.
- SMB: Benutzer und Passwort in `/etc/dahlia/remotes/<Name>.cred` (root, 0600), ohne Benutzer
  als Gast. Dateien gehören `dahlia-apps:dahlia` (0664/0775, `noperm`), damit Apps schreiben
  können. NFS: `soft,timeo=300,retrans=4`, die Besitzer kommen vom anderen Gerät.
- Optional nur lesen (`ro`). Entfernen lässt sich eine Freigabe erst, wenn keine App mehr
  einen Ordner darauf nutzt, oder ausdrücklich trotzdem. Die Dateien auf dem anderen Gerät
  bleiben immer unberührt.

Verlauf und InfluxDB (M25):

- `dahlia-history.service` (`/usr/sbin/dahlia-history run`) nimmt jede volle Minute CPU-Auslastung,
  belegten Arbeitsspeicher, Last, CPU-Temperatur (hwmon, in VMs meist keine) und die Datenrate
  jeder physischen Netzwerkschnittstelle auf. Plattentemperaturen nicht, damit schlafende
  Platten schlafen.
- Speicher: SQLite in `/var/lib/dahlia/history.db`. Minutenwerte zwei Tage, 15-Minuten-Mittel
  35 Tage, Stundenmittel 400 Tage; die Mittel werden bei jedem Messwert fortgeschrieben. Die
  Datei bleibt bei wenigen MB.
- Die Übersicht zeigt den Verlauf für Stunde, Tag, Woche, Monat und Jahr als einfache
  SVG-Linien, ohne Diagramm-Bibliothek.
- Optional schreibt der Dienst jeden Messwert per Line-Protokoll in eine InfluxDB (1.x mit
  Datenbank, 2.x mit Organisation, Bucket und Token), Messungen `dahlia` und `dahlia_net`
  (Tag `interface`), jeweils mit Tag `host`. Vor dem Speichern geht ein Testwert raus; das
  Ergebnis des letzten Exports steht in `/var/lib/dahlia/history-influx.json` und in den
  Einstellungen.
- Der Dienst läuft mit `ProtectSystem=strict` und darf nur nach `/var/lib/dahlia` schreiben.

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

Zwei-Faktor-Anmeldung (M22): unter Einstellungen mit dem Passwort einrichten, QR-Code
scannen, mit einem Code bestätigen. Erst dann ist sie an; andere angemeldete Browser werden
abgemeldet. Bei der Anmeldung fragt das Formular nach dem Code erst, wenn das Passwort
stimmt. Falsche Codes zählen zu den Fehlversuchen. Ausschalten braucht Passwort und Code.
Handy und Wiederherstellungscodes weg: `sudo dahlia-dashboard --disable-totp <Konto>`.

API-Tokens (M26): für Home Assistant, Homepage oder Skripte, unter Einstellungen erstellt und
widerrufen (höchstens 20). Ein Token (`dahlia_` + 43 Zeichen) wird nur beim Erstellen
angezeigt; `tokens.json` im Zustandsverzeichnis (0600) hält SHA-256-Hashes, Name, Erstellzeit
und letzte Nutzung. Gesendet als `Authorization: Bearer <Token>`, gilt es nur für lesende
Endpunkte mit Werten: `/api/system`, `/api/stats`, `/api/storage`, `/api/hardware`,
`/api/parity`, `/api/jobs`, `/api/apps` (ohne App-Passwörter und eigene Container-Einstellungen),
`/api/docker`, `/api/docker/stats`, `/api/history`. Keine Einstellungen, keine Änderungen, kein Terminal.
Beispiel: `curl -H "Authorization: Bearer dahlia_…" https://dahlia.local/api/stats`.

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
Host-Netzwerk, freie Geräte und Capabilities. `schema: 1` (ein Container) wird weiter gelesen.

Grafikkarte (ab Meilenstein 11): `schema: 3` erlaubt zusätzlich `gpu: true` an einem Dienst.
Hat der Server einen Render-Knoten (`/dev/dri/renderD*`, Intel oder AMD), bekommt der
Container `/dev/dri` und die Gruppen `render` und `video`, z. B. für VA-API/Quick Sync in
Jellyfin oder Immich. Ohne GPU läuft die App auf der CPU. NVIDIA braucht das Container-Toolkit
von NVIDIA und wird nicht unterstützt. Ältere Dahlia-Versionen überspringen Vorlagen mit
`schema: 3`. `system.hardware` meldet die Grafikgeräte (`gpus`).

Platzhalter in Umgebung, `command` und Zugangsdaten: `{TZ}`, `{PUID}`, `{PGID}`,
`{HOSTNAME}`, `{DOMAIN}` und `{SECRET_<NAME>}`. Secrets sind Zufallswerte (32 Hex-Zeichen),
die bei der Installation entstehen und in `/var/lib/dahlia/apps/<id>/secrets.json` (0600)
liegen; bei Updates bleiben sie gleich. Das Dashboard zeigt Zugangsdaten an, die die
Vorlage unter `login` nennt.

| Punkt | Umsetzung |
| --- | --- |
| Web-Port | Docker veröffentlicht ihn auf `127.0.0.1:41000-41999`; nginx lauscht im LAN auf dem öffentlichen Port der Vorlage (Port-Link `http://<server>:<port>/`) und mit mDNS-Alias auf 443 für `<id>.local` |
| LAN-Ports | nur wenn die Vorlage sie nennt; das Dashboard zeigt sie vor der Installation |
| Speicherort | Array `/mnt/user` oder System-SSD `/srv` (FHS: Daten, die das System bereitstellt). Wahl bei der Installation, ohne Array automatisch `/srv`. Apps von vor 0.7 liegen auf dem Array |
| System-SSD | `/srv` ist auf Btrfs ein eigenes Subvolume `@srv` auf oberster Ebene wie bei openSUSE, eingehängt über `srv.mount` (angelegt von `/usr/lib/dahlia/system-data.sh` im postinst). Es gehört nicht zu den snapper-Snapshots von `/`: ein Rollback setzt App-Daten nicht zurück und verliert sie nicht. Auf anderen Dateisystemen ein normaler Ordner |
| Daten | `<Ort>/appdata/<id>/...` (Besitzer `dahlia-apps`), Freigaben `<Ort>/<name>`; Apps auf dem Array starten nur mit eingehängtem Array |
| Einrichtung | Das Dashboard zeigt vor der Installation die Voraussetzungen und die Freigaben der App. Man wählt den Speicherort; "Ändern" öffnet einen Ordnerbaum auf System-SSD oder Array (mit "Neuer Ordner"), auch gemischt; der Pfad im Container bleibt gleich |
| Start | Docker startet nichts selbst (`restart: no`); `dahlia-apps.service` startet die aktivierten Apps nach den Platten |
| Container | `no-new-privileges`, Log-Treiber `local` mit 3 x 10 MB; Hauptdienst `dahlia-<id>`, Hilfsdienste `dahlia-<id>-<dienst>` |
| Stack | eigenes Docker-Netz je App, Dienste erreichen sich über den Dienstnamen; Hilfsdienste veröffentlichen keine Ports |
| Katalog | `catalog.json` (alle Vorlagen in einer Datei), sonst `index.json` und `apps/<id>.yaml`; Vorlagen mit neuerem Schema werden übersprungen |
| Logos | `catalog.json` nennt unter `icons` je App `icons/<id>.svg` oder `.png` mit SHA-256. Der Agent lädt nur geänderte Logos, prüft Hash, Größe (64 KB) und Dateityp und legt sie in `/var/lib/dahlia/apps-catalog/icons/`; nginx liefert sie als `/app-icons/` mit `sandbox`-CSP aus. Ohne Logo zeigt das Dashboard den Anfangsbuchstaben |
| Updates | `dahlia-apps-catalog.timer` lädt den Katalog alle 6 Stunden; weicht die Vorlage einer installierten App ab, zeigt das Dashboard "Update auf <Version>" und "Alle aktualisieren". Alte Images werden danach gelöscht |

```sh
dahlia-apps catalog --refresh
dahlia-apps install jellyfin [--location /srv] [--folder media=/mnt/user/Filme/Serien]
dahlia-apps list
dahlia-apps updates                # Apps mit neuerer Vorlage im Katalog
dahlia-apps update jellyfin
dahlia-apps remove jellyfin [--delete-data]
dahlia-apps check app.yaml        # Vorlage prüfen
dahlia-apps install kuma --spec kuma.json [--location /srv]   # eigener Container
dahlia-apps update kuma [--spec kuma.json]
```

Eigene Container (ab Meilenstein 19): Für ein Image, das nicht im Katalog ist, gibt es unter
Apps "Eigener Container". Das Formular fragt Kennung, Name, Image mit fester Version,
optional Web-Port und Port im Netzwerk, weitere LAN-Ports, Ordner (App-Daten unter
`<Ort>/appdata/<id>/` oder ein Ordner aus dem Ordner-Dialog), Umgebungsvariablen mit den
Platzhaltern oben und GPU ab. `dahlia-apps` baut daraus eine Vorlage mit nur dem Dienst
`app` und prüft sie wie jede Katalog-Vorlage, es gibt also auch hier kein `privileged`,
kein Host-Netz und keine freien Geräte. Ordner werden zu den Freigaben `folder1`,
`folder2` usw. Das Formular liegt in `/var/lib/dahlia/apps/<id>/custom.json` (0600),
`state.json` trägt `"custom": true`. Eigene Container nehmen nie eine Vorlage aus dem
Katalog, auch wenn dort später eine App mit derselben Kennung auftaucht; eine Kennung aus dem
Katalog ist bei der Installation nicht erlaubt. "Bearbeiten" ändert das Formular und
installiert neu (Daten und Secrets bleiben), "Update" lädt das Image neu.

## Backup (ab Meilenstein 13)

`dahlia-backup` sichert mit restic, was zum Wiederherstellen eines Servers nötig ist:

- `/etc/dahlia`
- `/var/lib/dahlia/apps` (Vorlagen, Zustand und Geheimnisse der installierten Apps)
- `/var/lib/dahlia-dashboard` (Dashboard-Konten)
- `<Speicherort>/appdata/<id>` jeder installierten App
- weitere Ordner aus `folders` in `/etc/dahlia/backup.json`

Freigaben mit Filmen oder Fotos gehören nicht dazu. Dafür sind Parity und eine zweite
Kopie zuständig.

Ziel ist ein Ordner `dahlia-backup` auf einer anderen Platte (am sichersten eine USB-Platte
unter `/media`) oder ein SFTP-Server. Für SFTP legt Dahlia `/etc/dahlia/backup_ed25519` an;
der öffentliche Schlüssel steht im Dashboard und gehört auf dem Server in
`~/.ssh/authorized_keys`. Das Repository ist mit einem Zufallspasswort aus
`/etc/dahlia/backup.pass` (0600) verschlüsselt. Ohne dieses Passwort lässt sich nichts
wiederherstellen, daher zeigt das Dashboard es zum Aufbewahren an.

Laufende Apps werden für die Sicherung gestoppt und danach wieder gestartet, damit die
Datenbanken konsistent sind (abschaltbar). Danach behält restic 7 tägliche, 4 wöchentliche
und 6 monatliche Snapshots. `dahlia-backup.timer` läuft täglich um 02:00 und wird erst
eingeschaltet, wenn ein Ziel gespeichert ist; die Zeit steht unter Einstellungen > Zeitpläne.
Fehler gehen wie bei den anderen Jobs an die Benachrichtigungen.

Wiederherstellen geht je App (Container gestoppt, `appdata` ersetzt, wieder gestartet) oder
für die Konfiguration (`/etc/dahlia` und die App-Liste).

```sh
sudo dahlia-backup run
sudo dahlia-backup snapshots
sudo dahlia-backup restore 4f2a9c1d paperless
sudo dahlia-backup restore 4f2a9c1d config
```

Auf einem neuen Server: Dahlia installieren, das alte Passwort nach `/etc/dahlia/backup.pass`
(0600) schreiben, im Dashboard dasselbe Ziel eintragen und `config` wiederherstellen, danach
die Apps einzeln wiederherstellen und unter Apps starten.

## Netzwerk und Namen (ab Meilenstein 7)

| Name | Weg | Voraussetzung |
| --- | --- | --- |
| `https://<server>.local/` | Dashboard, Avahi-Hostname | keine |
| `https://<app>.local/` | App über nginx, mDNS-Alias | mDNS-Aliase eingeschaltet |
| `http://<server>.local:<port>/` | Port-Link | keine |

Einen eigenen DNS-Server oder eine eigene Domain braucht Dahlia nicht (bis 1.0 gab es
`*.dahlia.home.arpa`; seit 1.1 nicht mehr, ein alter Eintrag `domain` in
`/etc/dahlia/domains.json` wird ignoriert). Mehrstufige `.local`-Namen
(`jellyfin.dahlia.local`) gibt es nicht, weil mDNS-Clients sie nicht zuverlässig auflösen.
Der Platzhalter `{DOMAIN}` in App-Vorlagen steht für `local`, `https://<app>.{DOMAIN}` ist
also der mDNS-Alias der App.

mDNS-Aliase: `dahlia-mdns.service` (ohne Rechte, Benutzer `dahlia-mdns`) veröffentlicht für jede App
mit Web-Oberfläche `<app>.local` per `avahi-publish` auf der primären IPv4-Adresse. Ändert
sich die Adresse, folgen die Aliase nach höchstens 10 Sekunden. Eine App, die so heißt wie
der Server, bekommt keinen Alias.

Zertifikate (`dahlia-domains`, Teil von `dahlia-agent`):

| Datei | Inhalt |
| --- | --- |
| `/etc/dahlia/tls/ca.key`, `ca.crt` | lokale CA, EC P-256, 20 Jahre; Schlüssel nur für root lesbar |
| `/etc/dahlia/tls/server.key`, `server.crt` | Zertifikat des Servers (Dashboard), 825 Tage |
| `/etc/dahlia/tls/apps.key`, `apps.crt` | Zertifikat der App-Proxys, 825 Tage |

Die CA hat Namensbeschränkungen (`nameConstraints`): sie kann nur Namen unter `.local`,
und private IPv4-Adressen (10/8, 172.16/12, 192.168/16) signieren (CAs von vor 1.1 auch
`home.arpa`; sie bleiben, damit niemand neu importieren muss). Wer sie importiert, vertraut
ihr also für keine anderen Websites. Das Server-Zertifikat enthält `<server>.local` und die
privaten IPv4-Adressen des Servers, das App-Zertifikat die mDNS-Aliase. Ohne Aliase lauscht
der App-Proxy nur auf dem Port-Link. Apps
installieren oder entfernen ändert so nur das App-Zertifikat; ein Browser, der das
Server-Zertifikat einmal akzeptiert hat, verliert das Dashboard nicht. `dahlia-domains.timer`
prüft täglich und nach dem Boot: neue Adresse, neue App, weniger als 30 Tage Restlaufzeit
führen zu einem neuen Zertifikat.

CA importieren: im Dashboard unter Adressen `dahlia-ca.crt` herunterladen und den
SHA-256-Fingerabdruck mit der Anzeige nach dem Login (Konsole oder SSH) vergleichen.
Danach unter Windows mit `certutil -addstore -f Root dahlia-ca.crt` (als Administrator),
unter macOS in der Schlüsselbundverwaltung (System, "Immer vertrauen"), unter Debian/Ubuntu
nach `/usr/local/share/ca-certificates/dahlia-ca.crt` kopieren und `update-ca-certificates`;
Firefox nutzt seinen eigenen Speicher (Einstellungen > Zertifikate > Importieren).

```sh
dahlia-domains show
dahlia-domains set --mdns off     # nur noch Port-Links
dahlia-domains apply              # Zertifikat und Proxy prüfen, nur bei Bedarf neu
```

## Diagnosepaket (ab Meilenstein 16)

Für Fehlermeldungen: Systemprotokoll > "Diagnosepaket herunterladen". Der Agent sammelt
Versionen (`dpkg-query`, `uname`, `/etc/os-release`), Dienste und Timer, Journal des
aktuellen Boots (Warnungen, `dahlia*`, Kernel) und Fehler des vorigen, Platten (`lsblk`,
`findmnt`, `df`, `dahlia-disk list`, Parity-Status, `/etc/snapraid.conf`), Netzwerk
(`ip`, `nft list ruleset`, `ss -tulpn`), Docker (`ps`, `info`, `system df`), die Enden von
apt- und unattended-upgrades-Log sowie `state.json` und `app.yaml` jeder App. Draußen
bleiben `compose.json` und `secrets.json` der Apps, `/etc/dahlia/backup.pass`, SSH- und
TLS-Schlüssel; in JSON-Einstellungen werden Schlüssel wie `password`, `token` oder `key`
durch `***` ersetzt. Das Archiv liegt im Laufzeitverzeichnis des Dashboards und wird nach
dem ersten Herunterladen gelöscht.

## Updates

- Debian-Sicherheitsupdates über `unattended-upgrades` (ab Meilenstein 15), standardmäßig an.
  `dahlia-base` liefert `/etc/apt/apt.conf.d/51dahlia-unattended-upgrades`: nur
  `${distro_codename}-security`, alte Kernel werden entfernt. Der Schalter ist Debians
  `20auto-upgrades`, der automatische Neustart (aus, Uhrzeit wählbar) steht in
  `52dahlia-auto-reboot`; beides setzt das Dashboard unter Updates. Ausgeführt wird es von
  `apt-daily-upgrade.timer` (täglich gegen 06:00), snapper legt wie bei jedem apt-Lauf
  Snapshots an. Das Dashboard zeigt den letzten Lauf aus
  `/var/log/unattended-upgrades/unattended-upgrades.log` und einen nötigen Neustart
  (`/run/reboot-required`). Ohne automatischen Neustart meldet `dahlia-notify` ihn einmal
  (Meldungsart `updates`).
- Dahlia-Pakete und übrige Updates per Klick im UI, immer mit Snapshot vorher.
- Das apt-Repo wird in CI gebaut, signiert und auf GitHub Pages veröffentlicht
  (`https://fabiy99.github.io/dahlia-linux/`). Der private Schlüssel existiert nur als
  GitHub-Secret. Details: [apt-repo.md](apt-repo.md).
