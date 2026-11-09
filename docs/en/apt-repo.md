# apt repository and updates

[Deutsche Fassung](../de/apt-repo.md)

Installed systems get new Dahlia versions from a signed apt repository on GitHub Pages:
`https://fabiy99.github.io/dahlia-linux/`. The source is
`/etc/apt/sources.list.d/dahlia.sources`, the public key
`/usr/share/keyrings/dahlia-archive-keyring.asc` (both from `dahlia-archive-keyring`).

Updating a server:

```sh
sudo apt update && sudo apt upgrade
```

snapper takes a snapshot before and after.

## Release procedure

1. Bump `VERSION`, merge the PR.
2. Create a GitHub release with the tag `v<VERSION>`.
3. The `iso` workflow attaches the ISO to the release; the `repo` workflow builds the
   packages, signs the repository with the secret `DAHLIA_SIGNING_KEY` and publishes it on
   GitHub Pages.

`tools/sign-repo.sh` aborts if the signature does not verify against the key in
`dahlia-archive-keyring`, so a repository the servers do not trust is never published.

## Setting up the signing key (once)

The private key exists only with the maintainer and as a GitHub secret, never in the
repository. On Windows in Git Bash, on Linux in a terminal:

```sh
export GNUPGHOME="$(mktemp -d)"
gpg --batch --passphrase '' --quick-gen-key "Dahlia Linux Archive <dahlia@invalid>" ed25519 sign 5y
gpg --armor --export > dahlia-archive-keyring.asc
gpg --armor --export-secret-keys > dahlia-signing-key-PRIVATE.asc
```

- `dahlia-archive-keyring.asc` (public) goes into the repository under
  `packages/dahlia-archive-keyring/usr/share/keyrings/`.
- Store the content of `dahlia-signing-key-PRIVATE.asc` as the secret `DAHLIA_SIGNING_KEY`
  under Settings → Secrets and variables → Actions. Then back the file up offline
  (e.g. a USB stick) and delete it from the computer.
- Settings → Pages → Source: "GitHub Actions".

The key expires after five years. Before that, extend it (`gpg --quick-set-expire`) and ship
the public key again with a release.
