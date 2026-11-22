# Dahlia Linux – Handbuch

Dieses Handbuch beschreibt Installation und Bedienung. Wie Dahlia innen aufgebaut ist, steht in
der [Architektur](architektur.md). English: [handbook](../en/handbook.md).

## Inhalt

1. [Was du brauchst](#was-du-brauchst)
2. [USB-Stick erstellen](#usb-stick-erstellen)
3. [Installieren](#installieren)
4. [Umzug von Unraid](#umzug-von-unraid)
5. [Erste Einrichtung](#erste-einrichtung)
6. [Desktop-App, Browser und Handy](#desktop-app-browser-und-handy)
7. [Startbildschirm](#startbildschirm)
8. [Platten und Array](#platten-und-array)
9. [Parity](#parity)
10. [Freigaben](#freigaben)
11. [Familienkonten](#familienkonten)
12. [Dateien](#dateien)
13. [Apps](#apps)
14. [VMs und Container](#vms-und-container)
15. [Backup](#backup)
16. [Fernzugriff](#fernzugriff)
17. [Updates und Snapshots](#updates-und-snapshots)
18. [Benachrichtigungen und Wochenbrief](#benachrichtigungen-und-wochenbrief)
19. [Terminal, Docker und Protokoll](#terminal-docker-und-protokoll)
20. [Wenn etwas nicht geht](#wenn-etwas-nicht-geht)

## Was du brauchst

- Einen PC mit 64-Bit-Prozessor (x86_64), mindestens 2 GB RAM, für VMs deutlich mehr.
- Eine SSD ab 20 GB für das System. Sie wird bei der Installation komplett gelöscht.
- Beliebig viele Datenplatten. Platten mit Daten (ext4, XFS, Btrfs) übernimmt Dahlia ohne
  Formatieren.
- Netzwerkkabel und Internet während der Installation (DHCP).
- Einen USB-Stick ab 2 GB für den Installer.

Für VMs muss im BIOS die Virtualisierung eingeschaltet sein (Intel VT-x oder AMD-V/SVM).

## USB-Stick erstellen

**Windows:** Die Desktop-App vom [neuesten Release](https://github.com/Fabiy99/dahlia-linux/releases/latest)
installieren (`Dahlia-<version>-setup.exe`) und starten. Auf der ersten Seite steht unten
„USB-Stick erstellen“:

1. „Herunterladen“ lädt die ISO des neuesten Release in den Download-Ordner und prüft sie mit
   der `.sha256`-Datei des Release. Eine vorhandene ISO lässt sich mit „ISO-Datei wählen“
   nehmen.
2. USB-Stick wählen. Angeboten werden nur Laufwerke am USB-Bus.
3. „Auf den Stick schreiben“: Windows fragt nach Administratorrechten. Dahlia löscht den Stick,
   schreibt die ISO und liest sie zur Kontrolle zurück.

**Linux und macOS:** ISO vom Release laden und mit einem Werkzeug wie balenaEtcher schreiben,
oder unter Linux:

```sh
sha256sum -c dahlia-linux-<version>-amd64.iso.sha256
sudo dd if=dahlia-linux-<version>-amd64.iso of=/dev/sdX bs=4M conv=fsync status=progress
```

`/dev/sdX` ist der Stick, nicht eine Platte mit Daten (`lsblk` zeigt Größe und Modell).

**Server ohne Bildschirm und Tastatur** (Windows-App): Im USB Creator „Für einen Server ohne
Bildschirm“ wählen und Servername, Konto, Passwort, Tastatur und Zeitzone eintragen. Auf dem
Stick landen nur Servername, Zeitzone, Tastatur, die Adresse des PCs und ein Zufallsschlüssel,
kein Passwort. Dann:

1. Stick am Server einstecken, Netzwerkkabel anschließen, vom Stick starten. Die App bleibt offen
   (Windows-Frage nach Verbindungen im privaten Netzwerk zulassen).
2. Der Server meldet seine Platten an die App. Dort die Systemplatte wählen. Eine Platte mit Daten
   braucht zur Bestätigung ihre Seriennummer.
3. Die Installation läuft durch, der Server startet neu. Die App koppelt sich über den
   Fingerabdruck der lokalen CA und legt das Dashboard-Konto selbst an.

## Installieren

Den PC vom Stick starten (Boot-Menü meist F11, F12 oder Esc). Im Menü „Install Dahlia Linux“
wählen. Der Installer ist englisch und fragt:

1. Tastaturlayout
2. Kontoname und Passwort. Mit diesem Konto meldest du dich an der Konsole, per SSH und im
   Terminal des Dashboards an. Der Name ist auch der Vorschlag für das Dashboard-Konto.
3. Systemplatte. Der Stick wird nicht angeboten.
4. Servername (Standard `dahlia`, erreichbar als `dahlia.local`)
5. Zeitzone
6. Netzwerk: DHCP oder feste Adresse
7. Optional ein SSH-Schlüssel
8. Zusammenfassung. Erst nach „Yes“ wird die Systemplatte gelöscht.

Nach dem Neustart zeigt ein Bildschirm am Server die Dahlie, die Adresse, einen QR-Code und den
Einrichtungscode. Server ohne Bildschirm: „serial console“ im Boot-Menü wählen.

„Automatic install“ ist für Tests gedacht: keine Fragen, Konto `dahlia-admin` mit Startpasswort
`dahlia`. Gibt es nur eine Platte, wird sie ohne Rückfrage gelöscht.

## Umzug von Unraid

Der Umzugshelfer läuft als Container in Unraid und installiert Dahlia auf eine freie SSD im selben
Server. Unraid läuft dabei weiter; seine Platten sind für den Helfer gesperrt.

1. In Unraid unter Docker „Add Container“ die Vorlage
   [`migrate/unraid-template.xml`](https://github.com/Fabiy99/dahlia-linux/blob/main/migrate/unraid-template.xml)
   eintragen (Abbild `ghcr.io/fabiy99/dahlia-linux/migrate`) und starten.
2. Die Weboberfläche des Containers öffnen (Port 8484). Der Zugangscode steht im Log des
   Containers.
3. Der Helfer zeigt, was übernommen wird: Netzwerk, Servername, Zeitzone, Platten (über die
   Seriennummer), Freigaben, SMB-Benutzer mit Passwort, Docker-Container mit Appdaten und
   WireGuard. Nicht übernommen werden Plugins, VM-Einstellungen, Bonding/VLANs, verschlüsselte
   und ZFS-Platten und weitere Pools.
4. Freie SSD wählen, Konto und Passwort eintragen, „Dahlia installieren“. Am Ende zeigt der Helfer
   den Einrichtungscode.
5. Array in Unraid stoppen, Server herunterfahren, Unraid-Stick abziehen und aufheben, Server
   starten. Er startet Dahlia von der SSD.
6. In der App oder im Browser den Einrichtungscode eingeben. Unter „Platten“ steht dann „Von
   Unraid übernehmen“ mit der Liste; „Jetzt übernehmen“ hängt die Datenplatten ohne Formatieren
   ein, legt Freigaben und Benutzer an und macht aus den Containern Apps.

Die Unraid-Parity passt nicht zu SnapRAID. Sie wird nur auf Wunsch formatiert und neu berechnet;
bis dahin geht der Weg zurück: Unraid-Stick einstecken und davon starten.

## Erste Einrichtung

1. Im Browser `https://dahlia.local/` öffnen (oder die IP-Adresse vom Bildschirm des Servers).
2. Die Zertifikatswarnung bestätigen. Der Server hat eine eigene, lokale Zertifizierungsstelle.
   Unter Einstellungen > Adressen lässt sie sich herunterladen und im Betriebssystem
   importieren; dann verschwindet die Warnung.
3. Den Einrichtungscode eingeben. Er steht am Bildschirm des Servers und beim SSH-Login.
4. Dashboard-Konto anlegen: Name (Vorschlag ist das Konto aus dem Installer) und Passwort.

Danach erscheint der Startbildschirm, und die App zeigt beim ersten Start einen kurzen
Rundgang. Den Rundgang startest du jederzeit wieder über das Kontomenü oben rechts.

## Desktop-App, Browser und Handy

**Desktop-App** (Windows, macOS, Linux, vom Release): Sie findet Server im Netz von selbst. Beim
ersten Verbinden zeigt sie den Fingerabdruck der lokalen CA; er muss mit dem unter Einstellungen
> Adressen übereinstimmen. Die App hat immer dieselbe Version wie der Server und aktualisiert
sich selbst, wenn der Server neuer ist.

**Browser:** Das Dashboard unter `https://dahlia.local/` bietet dasselbe. Es ist der Notzugang,
falls die App einmal nicht startet.

**Handy:** Oben rechts „Handy“ zeigt einen QR-Code. Das Handy öffnet damit das Dashboard im
Browser; „Zum Home-Bildschirm“ legt es wie eine App ab. Über „Handy“ gibt es auch einen
Kopplungscode, um die Desktop-App neu zu koppeln.

Zwei-Faktor-Anmeldung (TOTP): Einstellungen > Sicherheit.

## Startbildschirm

Kacheln: Begrüßung mit Zustand des Servers, Wetter für mehrere Orte, Apps, Speicher, System,
zuletzt erledigte Aufgaben, Wochenbrief und Inhalte aus Apps. „Anpassen“ schaltet Kacheln ein
und aus, ändert Größe und Reihenfolge, Farbschema, Akzentfarbe und Hintergrund. Die Einstellung
bleibt bei Updates erhalten.

**Inhalte aus Apps** (ausgeschaltet, bis du sie unter „Anpassen“ einschaltest):

| Kachel | App | Einrichtung |
| --- | --- | --- |
| Fotos | Immich | API-Schlüssel aus Immich (Kontoeinstellungen > API-Schlüssel) |
| Neu in Jellyfin | Jellyfin | API-Schlüssel aus Jellyfin (Dashboard > API-Schlüssel) |
| Downloads | qBittorrent oder Transmission | qBittorrent: Benutzer und Passwort der Web-Oberfläche; Transmission: nichts |

Die Schlüssel trägst du direkt in der Kachel ein. Der Server fragt die Apps auf seinem eigenen
Rechner ab; nichts geht nach außen.

**Bilderrahmen:** Unter „Anpassen“ > „Bilderrahmen am Server-Bildschirm“ zeigt der Monitor am
Server Fotos aus Immich statt der Übersicht: ein Album oder zufällig aus allen Fotos, Wechsel
nach einstellbaren Sekunden, auf Wunsch mit Uhr. Dafür braucht die Kachel „Fotos“ den
API-Schlüssel. Solange der Einrichtungscode angezeigt wird, bleibt die Übersicht.

## Platten und Array

Unter „Platten“ siehst du alle Laufwerke mit Größe, Temperatur und SMART-Werten.

- **Datenplatten** hängen einzeln unter `/mnt/disk1`, `/mnt/disk2` … Eine Platte mit Daten
  wird übernommen, ohne sie zu formatieren. Leere Platten formatiert Dahlia auf Wunsch (XFS,
  ext4 oder Btrfs).
- **Cache:** eine oder zwei SSDs unter `/mnt/cache` (Btrfs, bei zwei SSDs RAID1). Neue Dateien
  landen zuerst dort; der **Mover** schiebt sie nachts auf die Datenplatten. Die Pfade bleiben
  gleich.
- **Pool:** `/mnt/user` fasst Cache und Datenplatten zusammen. Freigaben und Apps nutzen diesen
  Pfad. `/mnt/storage` zeigt nur die Datenplatten.
- **Parity:** siehe unten.

Vor dem ersten Einsatz einer neuen Platte lohnt der Plattentest (Burn-in): er beschreibt und
liest die ganze Platte und dauert je nach Größe einen Tag oder länger. Platten mit Daten von
anderen Dateisystemen (auch NTFS und exFAT) kopiert der Import nach `/mnt/user` und prüft die
Kopie, bevor die Platte formatiert werden kann.

Verschlüsselung (LUKS) lässt sich beim Formatieren wählen. Der Schlüssel liegt auf dem Server
oder wird nach jedem Start im Dashboard eingegeben.

**Platte tauschen:** Ist eine Daten- oder Parity-Platte defekt oder fehlt sie, neue leere Platte
einbauen (mindestens so groß wie die alte) und unter „Platten“ > „Platte tauschen“ die alte
wählen. Dahlia formatiert die neue mit dem Dateisystem der alten (verschlüsselt, wenn die alte
es war), gibt ihr dieselbe Nummer (`/mnt/diskN` bleibt) und stellt sie aus der Parity wieder
her; bei einer Parity-Platte wird die Parity neu berechnet. Der Fortschritt steht unter
„Parity“. Eine Datenplatte geht nur mit Parity.

## Parity

Parity schützt vor dem Ausfall einzelner Datenplatten (SnapRAID). Die Parity-Platte muss
mindestens so groß sein wie die größte Datenplatte; zwei Parity-Platten verkraften zwei
Ausfälle.

- **Sync** berechnet die Parity neu, standardmäßig jede Nacht.
- **Scrub** prüft einen Teil der Daten gegen die Parity, standardmäßig wöchentlich.
- **Platte wiederherstellen:** Ersatzplatte einbauen, unter Parity die ausgefallene Platte und
  die neue wählen. Dahlia rekonstruiert den Stand des letzten Sync.

Parity ist kein Backup: gelöschte Dateien sind nach dem nächsten Sync auch dort weg.

## Freigaben

Freigaben zeigen Ordner aus `/mnt/user` im Netz.

- **SMB** (Windows, macOS, Linux): öffentlich (ohne Passwort), geschützt (alle lesen,
  SMB-Benutzer schreiben) oder privat (nur gewählte SMB-Benutzer). SMB-Benutzer legst du unter
  Freigaben an; sie sind unabhängig vom Dashboard-Konto.
- **Time Machine** für Macs: Option an einer Freigabe.
- **NFS** für Linux-Rechner und andere Server.
- **Freigaben anderer Geräte** (NAS, PC) bindet Dahlia unter `/mnt/remotes` ein; Apps können sie
  nutzen.

Windows: im Explorer `\\dahlia.local\<freigabe>`. macOS: Finder > Gehe zu > Mit Server
verbinden, `smb://dahlia.local/<freigabe>`.

## Familienkonten

Unter Einstellungen > Sicherheit > Dashboard-Konten ein Konto mit der Rolle „Familie“ anlegen und
die Apps wählen, die es sehen soll. Ein Familienkonto hat:

- einen eigenen Startbildschirm (Farben, Hintergrund, Wetter-Orte nur für dieses Konto) mit den
  freigegebenen Apps und den Ordnern, die es öffnen darf,
- einen SMB-Benutzer mit demselben Namen und Passwort; private Freigaben gibst du ihm wie jedem
  SMB-Benutzer frei, öffentliche und geschützte sieht es immer,
- keinen Zugriff auf Einstellungen, Platten, Apps installieren, Terminal oder Protokolle.

Ändert das Familienmitglied sein Passwort, ändert sich das SMB-Passwort mit.

## Dateien

Der Dateimanager zeigt Pool, System-SSD und eingebundene Freigaben: Ordner anlegen, suchen,
hoch- und herunterladen, kopieren, verschieben, umbenennen, löschen. Den Pfad eines Ordners
kopierst du mit einem Klick, etwa für eine App.

## Apps

Unter „Apps“ steht der Katalog mit über 100 Vorlagen. Installieren fragt:

- **Speicherort** der App-Daten: System-SSD (`/srv/appdata`) oder Pool (`/mnt/user/appdata`).
  Ohne Datenplatten bleibt nur die SSD.
- **Ordner** für Medien, Fotos oder Dokumente, gewählt im Ordnerbaum.

Danach ist die App über einen Port-Link und unter `<app>.local` erreichbar. Eine Grafikkarte
(`/dev/dri`) für Transcoding lässt sich Apps wie Jellyfin geben. Eigene Container ohne Katalog
gehen über „Eigener Container“.

**Updates:** Steht ein Update an, zeigt die Karte „Update“. Vor dem Update kopiert Dahlia die
Daten und die Konfiguration der App (`appdata/.rollback/<app>`), sofern genug Platz frei ist.
Läuft die neue Version nicht, setzt „Zurück zur alten Version“ App, Daten und
Abbilder zurück. Die Kopie gilt bis zum nächsten Update der App.

## VMs und Container

Unter Mehr > VMs. Beim ersten Mal installiert Dahlia Incus aus Debian; du wählst, ob die
Laufwerke auf der System-SSD (`/var/lib/incus`) oder auf dem Cache (`/mnt/cache/incus`) liegen.

**Neu:**

- **Container (LXC):** ein kleines Linux-System, das den Kernel des Servers mitnutzt. Schnell
  und sparsam. Abbild aus der Liste (Debian, Ubuntu, Alpine …).
- **VM:** ein eigener Rechner mit eigenem Betriebssystem. Von einem fertigen Abbild oder von
  einer ISO-Datei auf dem Server (z. B. Home Assistant OS, Windows, eine andere Distribution).
- **Netzwerk:** „Hinter dem Server (NAT)“ gibt ein eigenes kleines Netz; der Server erreicht die
  Maschine, das Heimnetz nicht. „Im Heimnetz“ holt eine eigene Adresse vom Router; dann
  erreicht der Server selbst die Maschine nicht (Grenze von macvlan), alle anderen Geräte schon.
- **Windows:** Häkchen „Windows“ setzen. Dann bekommt die VM ein TPM und ein NVMe-Laufwerk,
  damit das Setup die Platte ohne Zusatztreiber findet. Für das Netzwerk in Windows die
  virtio-win-Treiber installieren.

**Konsole:** Bei einer laufenden VM öffnet „Konsole“ den Bildschirm der VM, in der App und im
Browser. „Strg+Alt+Entf“ schickt die Tastenkombination an die VM. Nach der Installation von
einer ISO die VM herunterfahren und „ISO auswerfen“.

**Shell eines Containers:** „Shell“ kopiert den Befehl `incus exec <name> -- bash -l` und öffnet
das Terminal. Dort einfügen.

**Snapshots:** „Snapshots“ an einer Maschine legt einen Snapshot an, setzt auf einen zurück oder
löscht ihn. Automatisch geht täglich (7 Tage aufbewahrt) oder wöchentlich (4 Wochen). Snapshots
liegen auf demselben Datenträger; gegen einen Plattenausfall hilft nur das Backup.

Einstellungen (Kerne, Arbeitsspeicher, Start mit dem Server) gelten für VMs ab dem nächsten
Start. Löschen geht nur bei gestoppten Maschinen und entfernt ihr Laufwerk.

## Backup

Unter Mehr > Backup. Gesichert werden die Konfiguration, die Dashboard-Konten und die Daten aller
Apps, nicht Filme oder Fotos in Freigaben.

1. Ziel wählen: ein Ordner auf einer anderen Platte (am besten eine USB-Platte) oder ein
   SFTP-Server.
2. **Passwort aufbewahren.** Das Backup ist verschlüsselt; ohne das angezeigte Passwort lässt
   sich nichts wiederherstellen.
3. Das Backup läuft täglich (Zeit unter Einstellungen > Dienste > Zeitpläne). Laufende Apps
   halten dafür kurz an.

**VMs und Container:** In den Backup-Einstellungen lassen sich Maschinen dazunehmen. Dahlia
exportiert sie für das Backup (`incus export`, nur der aktuelle Stand) und entfernt den Export
danach. Wiederherstellen legt die Maschine unter ihrem Namen neu an, oder als
`<name>-restored`, wenn es den Namen schon gibt.

**Backup-Probe:** Einmal im Monat stellt Dahlia eine Stichprobe von Dateien aus dem letzten
Backup in einen Testordner wieder her und vergleicht sie. Das Ergebnis steht auf der
Backup-Seite; ein Fehler kommt als Benachrichtigung.

**Wiederherstellen:** je App oder die Konfiguration. Auf einem neuen Server: Dahlia installieren,
dasselbe Ziel und das alte Passwort eintragen, zuerst die Konfiguration, dann die Apps
wiederherstellen.

## Fernzugriff

Einstellungen > Sicherheit.

- **WireGuard:** Dahlia ist der Server. Pro Gerät einen Peer anlegen, den QR-Code mit der
  WireGuard-App scannen. Am Router muss der UDP-Port (Standard 51820) auf den Server
  weitergeleitet sein.
- **Tailscale:** installieren, anmelden, fertig; kein Port am Router nötig.

Die Firewall lässt sonst nur Verbindungen aus lokalen Netzen zu. Eigene Regeln gehen unter
Einstellungen > Sicherheit > Firewall.

## Updates und Snapshots

Einstellungen > Updates zeigt anstehende Paket-Updates; die App meldet sie auch mit einem
Hinweis oben. Sicherheitsupdates spielt Dahlia auf Wunsch automatisch ein.

Vor jedem Update legt snapper einen Snapshot des Systems an. Unter „System-Snapshots“ setzt
„Zurücksetzen“ das System auf einen Snapshot zurück und startet neu. Konfiguration, Konten,
Logs und Docker-Daten werden dabei aus dem laufenden System übernommen; Pool und `/srv` bleiben
unberührt. Snapshots lassen sich auch im Bootmenü starten.

## Benachrichtigungen und Wochenbrief

Die Glocke oben zeigt Meldungen des Servers (Backups, Platten, Updates). Die Desktop-App zeigt
neue Meldungen auch als Benachrichtigung des Betriebssystems; das lässt sich in der Glocke
abschalten. Weitere Wege (E-Mail, ntfy, Gotify) unter Einstellungen > Dienste.

**Wochenbrief und Jahresrückblick:** Jeden Montag fasst Dahlia die Woche zusammen (Datenzuwachs, neue
Fotos, Backups, Updates, neue Apps, Warnungen, Netzwerkverkehr), am 1. Januar das Jahr. Die Kachel „Wochenbrief“ zeigt den
neuesten; ältere stehen im Archiv der Kachel.

**Strom und Kosten:** Unter Einstellungen > Dienste > „Strom und Kosten“ die Messung wählen: die
USV (Leistung über NUT) oder eine smarte Steckdose im LAN (Shelly oder Tasmota, lokal abgefragt,
ohne Cloud), dazu den Preis pro kWh. Dahlia misst jede Minute; der Wochenbrief zeigt den
Durchschnitt in Watt, die kWh und die Kosten, der Jahresrückblick die Summe.

## Terminal, Docker und Protokoll

- **Terminal** (Mehr > Terminal): eine Shell auf dem Server als Linux-Konto mit dem Namen des
  Dashboard-Kontos. Kopieren und Einfügen mit Strg+Umschalt+C/V (macOS: Cmd+C/V).
- **Docker** (Mehr > Docker): alle Container mit CPU und RAM, Starten, Stoppen, Logs, Aufräumen.
- **System-Log** (Mehr > System-Log): das Journal mit Filtern; „Diagnosepaket“ speichert alles,
  was bei einer Fehlersuche hilft, ohne Passwörter.

## Wenn etwas nicht geht

| Problem | Lösung |
| --- | --- |
| `dahlia.local` geht nicht | IP-Adresse vom Bildschirm des Servers oder aus dem Router nehmen. Manche Router oder VPNs blockieren mDNS. |
| App findet den Server nicht | Adresse von Hand eintippen. Im Browser über „Handy“ einen Kopplungscode holen und die App neu koppeln. |
| Zertifikatswarnung | Lokale CA unter Einstellungen > Adressen herunterladen und importieren. |
| Dashboard-Passwort vergessen | Per SSH oder an der Konsole: `sudo rm /var/lib/dashboard/users.json && sudo systemctl restart dashboard`, dann neu einrichten. Apps und Daten bleiben. |
| Zwei-Faktor-Handy verloren | `sudo dashboard --disable-totp <konto>` |
| System startet nach einem Update nicht richtig | Im Bootmenü einen Snapshot von vor dem Update starten, danach im Dashboard dauerhaft zurücksetzen. |
| App läuft nach dem Update nicht | Apps > die App > „Zurück zur alten Version“. |
| Platte ausgefallen | Ersatzplatte einbauen, Platten > Platte tauschen. |
| VM startet nicht („no KVM“) | Virtualisierung im BIOS einschalten (VT-x/AMD-V). |

Fehler melden: [GitHub Issues](https://github.com/Fabiy99/dahlia-linux/issues), am besten mit
dem Diagnosepaket.
