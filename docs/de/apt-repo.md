# apt-Repo und Updates

[English version](../en/apt-repo.md)

Installierte Systeme holen neue Dahlia-Versionen aus einem signierten apt-Repo auf
GitHub Pages: `https://fabiy99.github.io/dahlia-linux/`. Die Quelle steht in
`/etc/apt/sources.list.d/dahlia.sources`, der öffentliche Schlüssel in
`/usr/share/keyrings/dahlia-archive-keyring.asc` (beides aus `dahlia-archive-keyring`).

Update auf dem Server:

```sh
sudo apt update && sudo apt upgrade
```

snapper legt vorher und nachher einen Snapshot an.

## Ablauf bei einem Release

1. `VERSION` erhöhen, PR mergen.
2. Auf GitHub ein Release mit dem Tag `v<VERSION>` anlegen.
3. Der Workflow `iso` hängt die ISO an das Release, der Workflow `repo` baut die Pakete,
   signiert das Repo mit dem Secret `DAHLIA_SIGNING_KEY` und veröffentlicht es auf
   GitHub Pages.

`tools/sign-repo.sh` bricht ab, wenn die Signatur nicht zum Schlüssel in
`dahlia-archive-keyring` passt. So kann kein Repo veröffentlicht werden, dem die Server
nicht vertrauen.

## Signierschlüssel einrichten (einmalig)

Der private Schlüssel liegt nur beim Maintainer und als GitHub-Secret, nie im Repo.
Unter Windows in Git Bash, unter Linux in einem Terminal:

```sh
export GNUPGHOME="$(mktemp -d)"
gpg --batch --passphrase '' --quick-gen-key "Dahlia Linux Archive <dahlia@invalid>" ed25519 sign 5y
gpg --armor --export > dahlia-archive-keyring.asc
gpg --armor --export-secret-keys > dahlia-signing-key-PRIVAT.asc
```

- `dahlia-archive-keyring.asc` (öffentlich) kommt ins Repo nach
  `packages/dahlia-archive-keyring/usr/share/keyrings/`.
- Den Inhalt von `dahlia-signing-key-PRIVAT.asc` unter Settings → Secrets and variables →
  Actions als Secret `DAHLIA_SIGNING_KEY` speichern. Danach die Datei offline sichern
  (z. B. USB-Stick) und vom Rechner löschen.
- Settings → Pages → Source: "GitHub Actions".

Der Schlüssel läuft nach fünf Jahren ab. Vorher wird die Gültigkeit verlängert
(`gpg --quick-set-expire`) und der öffentliche Schlüssel mit einem Release neu ausgeliefert.
