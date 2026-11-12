"""LUKS2 encryption of Dahlia disks.

All encrypted disks share one passphrase, the array key. Keys never go on a
command line: cryptsetup reads them from stdin (--key-file=-) or from a file.

Optionally the system SSD keeps a key file (/etc/dahlia/luks/array.key, 0400,
an extra key slot on every encrypted disk); /etc/crypttab then lists the disks
with it, and systemd-cryptsetup opens them at boot. Without it the disks
stay locked after a reboot until the passphrase is entered in the dashboard.
The key file protects disks that leave the house alone (RMA, sold, stolen),
not a stolen server.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import tempfile

KEY_DIR = "/etc/dahlia/luks"
KEY_FILE = f"{KEY_DIR}/array.key"
KEY_BYTES = 64
PASS_MIN = 8
PASS_MAX = 512
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAPPER_DIR = "/dev/mapper"
# The new key for luksChangeKey goes through a file; /run is tmpfs.
TMP_DIR = "/run/dahlia"


CRYPTTAB = "/etc/crypttab"
BEGIN = "# BEGIN disk: generated from /etc/dahlia/storage.json, do not edit"
END = "# END disk"


class CryptError(RuntimeError):
    pass


def check_passphrase(key: str) -> str:
    if not PASS_MIN <= len(key) <= PASS_MAX or CONTROL_RE.search(key):
        raise CryptError(f"passphrase: {PASS_MIN} to {PASS_MAX} characters, no control characters")
    return key


def read_keys(stream, count: int) -> list[str]:
    """count passphrases, one per line, from stdin."""
    lines = stream.read().decode("utf-8", "strict").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != count:
        raise CryptError(f"expected {count} passphrase line(s) on stdin")
    return [check_passphrase(k) for k in lines]


def cryptsetup(args: list[str], key: str | None = None, check: bool = True):
    return subprocess.run(["cryptsetup", *args], input=key.encode() if key is not None else None,
                          capture_output=True, check=check)


def mapper_name(luks: str) -> str:
    return f"luks-{luks}"


def mapper_path(luks: str) -> str:
    return f"{MAPPER_DIR}/{mapper_name(luks)}"


def device_path(luks: str) -> str:
    return f"/dev/disk/by-uuid/{luks}"


def is_open(luks: str) -> bool:
    return os.path.exists(mapper_path(luks))


def is_present(luks: str) -> bool:
    return os.path.exists(device_path(luks))


def luks_format(part: str, key: str) -> str:
    """New LUKS2 container on part; returns its UUID."""
    cryptsetup(["luksFormat", "--type", "luks2", "--batch-mode", "--key-file=-", part], key)
    return cryptsetup(["luksUUID", part]).stdout.decode().strip()


def open_with_key(device: str, luks: str, key: str) -> bool:
    """False when the key is wrong."""
    res = cryptsetup(["open", "--type", "luks", "--key-file=-", device, mapper_name(luks)], key,
                     check=False)
    return res.returncode == 0


def open_with_file(device: str, luks: str, key_file: str) -> bool:
    res = cryptsetup(["open", "--type", "luks", "--key-file", key_file, device,
                      mapper_name(luks)], check=False)
    return res.returncode == 0


def key_matches(device: str, key: str) -> bool:
    res = cryptsetup(["open", "--test-passphrase", "--key-file=-", device], key, check=False)
    return res.returncode == 0


def close(luks: str) -> None:
    if is_open(luks):
        cryptsetup(["close", mapper_name(luks)], check=False)


def ensure_key_file(path: str = KEY_FILE) -> None:
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as f:
        f.write(secrets.token_bytes(KEY_BYTES))


def drop_key_file(path: str = KEY_FILE) -> None:
    if not os.path.exists(path):
        return
    size = os.path.getsize(path)
    os.chmod(path, 0o600)
    with open(path, "r+b") as f:
        f.write(b"\0" * size)
        f.flush()
        os.fsync(f.fileno())
    os.unlink(path)


def add_key_file(device: str, key: str, path: str = KEY_FILE) -> None:
    """Extra key slot on device holding the key file; key authorises it."""
    res = cryptsetup(["luksAddKey", "--key-file=-", device, path], key, check=False)
    if res.returncode != 0:
        raise CryptError(f"{device}: passphrase not accepted")


def remove_key_file(device: str, path: str = KEY_FILE) -> None:
    # Removing a slot needs that slot's key: the key file itself.
    cryptsetup(["luksRemoveKey", "--key-file", path, device], check=False)


def change_key(device: str, old: str, new: str) -> None:
    os.makedirs(TMP_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=TMP_DIR, prefix=".newkey.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new.encode())
        res = cryptsetup(["luksChangeKey", "--key-file=-", device, tmp], old, check=False)
    finally:
        os.unlink(tmp)
    if res.returncode != 0:
        raise CryptError(f"{device}: old passphrase not accepted")


def crypttab_block(luks_ids: list[str], key_file: str = KEY_FILE) -> str:
    if not luks_ids:
        return ""
    lines = [f"{mapper_name(u)} UUID={u} {key_file} luks,nofail" for u in luks_ids]
    return "\n".join([BEGIN, *lines, END]) + "\n"


def render_crypttab(current: str, luks_ids: list[str], key_file: str = KEY_FILE) -> str:
    """current with Dahlia's block replaced; other entries stay as they are."""
    out, skip = [], False
    for line in current.splitlines(keepends=True):
        if line.rstrip("\n") == BEGIN:
            skip = True
            continue
        if skip:
            if line.rstrip("\n") == END:
                skip = False
            continue
        out.append(line)
    text = "".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + crypttab_block(luks_ids, key_file)


def sync_crypttab(luks_ids: list[str], path: str = CRYPTTAB) -> bool:
    """Write the entries (only while the key file exists). True if changed."""
    ids = luks_ids if os.path.exists(KEY_FILE) else []
    try:
        with open(path, encoding="utf-8") as f:
            current = f.read()
    except FileNotFoundError:
        current = ""
    wanted = render_crypttab(current, ids)
    if wanted == current or (not current and not wanted):
        return False
    tmp = f"{path}.dahlia-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(wanted)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return True
