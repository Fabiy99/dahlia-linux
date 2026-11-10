# CLAUDE.md

Regeln für Agenten und Mitwirkende in diesem Repo. Gilt für jede Änderung.

## Projekt

Dahlia Linux: Homelab-Server-OS auf Debian 13 "Trixie" (x86_64), GPLv3.
Architektur: `docs/de/architektur.md` (Deutsch) bzw. `docs/en/architecture.md` (Englisch).
Beide Fassungen inhaltlich gleich halten.

## Feste Entscheidungen

- Basis Debian 13. Kein Backports-Kernel, kein Debian 12.
- Installation auf SSD über Debian-Installer mit Preseed. Kein Live-System.
- Auslieferung als .deb-Pakete (`dahlia-base`, `dahlia-agent`, `dahlia-dashboard`,
  `dahlia-storage`, `dahlia-archive-keyring`) über ein signiertes apt-Repo.
  Kein `git pull` auf dem Zielsystem.
- Root-Dateisystem Btrfs mit snapper und grub-btrfs.
- Storage: Datenplatten einzeln unter `/mnt/diskN`, Parity unter `/mnt/parityN`,
  Cache unter `/mnt/cache` (Btrfs, RAID1 bei zwei SSDs), `/mnt/storage` = mergerfs nur HDDs,
  `/mnt/user` = mergerfs Cache + HDDs. Freigaben nutzen nur `/mnt/user`. Apps liegen wahlweise
  auf `/mnt/user` oder auf der System-SSD unter `/srv/dahlia` (Btrfs-Subvolume `@dahlia`).
- Platten über generierte systemd-Mount-Units mit `nofail`, nicht über `/etc/fstab`.
- Web-UI (`dahlia-dashboard`) läuft unprivilegiert. Alles, was root braucht, geht über
  `dahlia-agent` (Unix-Socket, feste Aktionsliste, Parameter werden geprüft).
  Das UI bekommt keinen Docker-Socket und führt keine freien Shell-Befehle aus.
- Firewall: nftables, verwaltet von Dahlia. Docker veröffentlicht Ports nur auf 127.0.0.1.
- Domains: Server `dahlia.local`; Apps über Port-Links, einstufige mDNS-Aliase oder
  `*.dahlia.home.arpa` per AdGuard. Keine mehrstufigen `.local`-Namen.
- Keine fertigen Dashboards (Cockpit, Portainer, CasaOS usw.). Bibliotheken sind erlaubt.
- VMs und LXC erst ab Version 2, über Incus.

## Arbeitsweise

- Ein Meilenstein pro PR, Reihenfolge siehe README.
- Kein Code ohne Test, wo es testbar ist. Storage-Code wird gegen Loop-Devices getestet.
- Keine Platzhalter, keine TODOs im Code. Was nicht fertig ist, kommt nicht in den PR.
- Vor jedem Push: `shellcheck` auf alle Shell-Skripte, `ruff check .`, `pytest`.
- Shell-Skripte: `#!/bin/bash`, `set -euo pipefail`, Dateiendung `.sh` oder ein Eintrag in
  der shellcheck-Liste der CI.
- Python: 3.13, Formatierung und Linting über ruff (Konfiguration in `pyproject.toml`).

## Stil

- Kommentare und Doku knapp und sachlich, wie ein Sysadmin sie schreibt.
  Keine Werbesprache.
- Doku, README und PR-Vorlage zweisprachig (DE/EN). Code-Kommentare auf Englisch.
- UI-Texte nur über `locales/de.json` und `locales/en.json`, nie fest im Code.
