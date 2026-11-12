"""Block device discovery and classification.

Reads `lsblk --json` and decides for every filesystem whether Dahlia may use
it. The rules are conservative: anything that belongs to the running system,
is already mounted, or is part of RAID/LVM/LUKS/ZFS is never offered.

The one exception are LUKS containers Dahlia formatted itself: once unlocked
they are open as /dev/mapper/luks-<LUKS UUID>, and the filesystem inside is
classified like any other (with `luks` set). Locked, they show as "locked".
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass

LSBLK_COLUMNS = "NAME,PATH,TYPE,SIZE,FSTYPE,UUID,LABEL,MOUNTPOINTS,MODEL,SERIAL,RM,PKNAME"

# Filesystems that can join the pool (data/parity) as they are.
POOL_FILESYSTEMS = {"ext4", "xfs", "btrfs"}
# Filesystems that can only be mounted as external disks outside the pool.
EXTERNAL_FILESYSTEMS = POOL_FILESYSTEMS | {"ntfs", "exfat"}
# Filesystems dahlia-disk format creates on a blank disk.
FORMAT_FILESYSTEMS = ("xfs", "ext4", "btrfs")
# Signatures that mean "this device belongs to something else".
FOREIGN_SIGNATURES = {
    "linux_raid_member",
    "LVM2_member",
    "crypto_LUKS",
    "zfs_member",
    "swap",
    "iso9660",
    "udf",
}
LUKS = "crypto_LUKS"
# Dahlia opens its containers under this name (the systemd-cryptsetup convention).
MAPPER_PREFIX = "luks-"

# Mount points that mark a device tree as the system disk.
SYSTEM_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/usr", "/var", "[SWAP]"}


@dataclass
class Device:
    path: str
    name: str
    kind: str  # lsblk TYPE: disk, part, loop
    size: int
    fstype: str | None
    uuid: str | None
    label: str | None
    model: str | None
    serial: str | None
    removable: bool
    disk: str  # whole-disk path this filesystem lives on
    # candidate, external-only, empty, system, mounted, foreign, unsupported, registered,
    # locked
    status: str
    reason: str
    imported: bool = False  # data copied to /mnt/user by dahlia-disk import
    luks: str | None = None  # UUID of the LUKS container around the filesystem

    def to_dict(self) -> dict:
        return asdict(self)


def run_lsblk() -> dict:
    out = subprocess.run(
        ["lsblk", "--json", "--bytes", "--output", LSBLK_COLUMNS],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return enrich(json.loads(out), blkid_probe)


BLKID_AMBIVALENT = 8
AMBIVALENT = "ambivalent"


def blkid_probe(path: str) -> dict[str, str]:
    """Read the filesystem signature straight from the device.

    lsblk takes FSTYPE/UUID from the udev database, which lags behind after
    mkfs or on loop devices. `blkid -p` probes the device itself.
    """
    res = subprocess.run(
        ["blkid", "-p", "-o", "export", path], check=False, capture_output=True, text=True
    )
    # Exit code 8: several signatures, blkid will not say which one is real.
    if res.returncode == BLKID_AMBIVALENT:
        return {"TYPE": AMBIVALENT}
    values = {}
    for line in res.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value
    return values


def enrich(lsblk: dict, probe) -> dict:
    """Fill in missing fstype/uuid/label of leaf devices using `probe`."""

    def walk(node: dict) -> None:
        children = node.get("children") or []
        for child in children:
            walk(child)
        if children or node.get("fstype"):
            return
        found = probe(node.get("path") or f"/dev/{node.get('name')}")
        if found.get("TYPE"):
            node["fstype"] = found["TYPE"]
            node["uuid"] = node.get("uuid") or found.get("UUID")
            node["label"] = node.get("label") or found.get("LABEL")

    for top in lsblk.get("blockdevices") or []:
        walk(top)
    return lsblk


def _mountpoints(node: dict) -> list[str]:
    return [m for m in (node.get("mountpoints") or []) if m]


def _tree_mountpoints(node: dict) -> set[str]:
    found = set(_mountpoints(node))
    for child in node.get("children") or []:
        found |= _tree_mountpoints(child)
    return found


def dahlia_crypt(node: dict) -> dict | None:
    """The open Dahlia mapping of a LUKS node, or None."""
    name = MAPPER_PREFIX + (node.get("uuid") or "")
    for child in node.get("children") or []:
        if child.get("type") == "crypt" and child.get("name") == name:
            return child
    return None


def _tree_has_foreign(node: dict) -> bool:
    if node.get("fstype") == LUKS:
        # A locked container or one Dahlia opened is judged by what is
        # inside; one opened under another name stays foreign.
        inner = dahlia_crypt(node)
        if inner is not None:
            return _tree_has_foreign(inner)
        return bool(node.get("children"))
    if node.get("fstype") in FOREIGN_SIGNATURES:
        return True
    return any(_tree_has_foreign(c) for c in node.get("children") or [])


def classify(
    lsblk: dict,
    registered_uuids: set[str] | None = None,
    allow_loop: bool = False,
    registered_luks: set[str] | None = None,
) -> list[Device]:
    """Return one Device per usable-or-rejected filesystem/disk, with a status."""
    registered_uuids = registered_uuids or set()
    registered_luks = registered_luks or set()
    result: list[Device] = []

    for top in lsblk.get("blockdevices") or []:
        kind = top.get("type")
        if kind == "loop" and not allow_loop:
            continue
        if kind not in ("disk", "loop"):
            continue
        # Compressed RAM swap and RAM disks are not storage.
        if (top.get("name") or "").startswith(("zram", "ram")):
            continue

        tree_mounts = _tree_mountpoints(top)
        is_system = bool(tree_mounts & SYSTEM_MOUNTPOINTS)
        has_foreign = _tree_has_foreign(top)
        children = top.get("children") or []

        # Whole disk without partition table and without filesystem: blank,
        # unless something has it mounted (a signature blkid cannot read).
        if not children and not top.get("fstype"):
            if is_system:
                status, reason = "system", "system disk"
            elif tree_mounts:
                status, reason = "mounted", "mounted outside Dahlia"
            else:
                status, reason = "empty", "no filesystem"
            result.append(_device(top, top, status, reason))
            continue

        # Filesystem directly on the disk, or one entry per partition.
        nodes = [top] if not children else [c for c in children if c.get("type") == "part"]
        for node in nodes:
            if node.get("fstype") == LUKS and not is_system:
                result.append(_luks_device(node, top, has_foreign, registered_uuids,
                                           registered_luks))
                continue
            status, reason = _decide(node, is_system, has_foreign, registered_uuids)
            result.append(_device(node, top, status, reason))

    return result


def _decide(node: dict, is_system: bool, has_foreign: bool, registered: set[str]):
    fstype = node.get("fstype")
    if is_system:
        return "system", "disk holds the running system"
    if node.get("uuid") and node["uuid"] in registered:
        return "registered", "already managed by Dahlia"
    if _tree_mountpoints(node):
        return "mounted", "mounted outside Dahlia"
    if fstype == AMBIVALENT:
        return "foreign", "more than one signature on the device"
    if fstype in FOREIGN_SIGNATURES or has_foreign:
        return "foreign", f"member of {fstype or 'RAID/LVM/LUKS/ZFS'}"
    if not fstype:
        return "empty", "no filesystem"
    if not node.get("uuid"):
        return "unsupported", "filesystem has no UUID"
    if fstype in POOL_FILESYSTEMS:
        return "candidate", "can join the pool without formatting"
    if fstype in EXTERNAL_FILESYSTEMS:
        return "external-only", f"{fstype} can only be mounted as external disk"
    return "unsupported", f"filesystem {fstype} not supported"


def _luks_device(node: dict, top: dict, has_foreign: bool, registered: set[str],
                 registered_luks: set[str]) -> Device:
    luks = node.get("uuid") or ""
    inner = dahlia_crypt(node)
    if inner is None:
        if node.get("children"):
            return _device(node, top, "foreign", "LUKS container opened outside Dahlia")
        if luks in registered_luks:
            return _device(node, top, "registered", "encrypted, locked", luks=luks)
        return _device(node, top, "locked", "encrypted, locked", luks=luks)
    status, reason = _decide(inner, False, has_foreign, registered)
    dev = _device(inner, top, status, reason, luks=luks)
    # The partition, not the mapping, is what lives on the disk.
    dev.path = node.get("path") or f"/dev/{node.get('name')}"
    dev.name = node.get("name") or ""
    dev.kind = node.get("type") or ""
    return dev


def _device(node: dict, disk: dict, status: str, reason: str, luks: str | None = None) -> Device:
    return Device(
        path=node.get("path") or f"/dev/{node.get('name')}",
        name=node.get("name") or "",
        kind=node.get("type") or "",
        size=int(node.get("size") or 0),
        fstype=node.get("fstype"),
        uuid=node.get("uuid"),
        label=node.get("label"),
        model=(disk.get("model") or "").strip() or None,
        serial=(disk.get("serial") or "").strip() or None,
        removable=bool(disk.get("rm")),
        disk=disk.get("path") or f"/dev/{disk.get('name')}",
        status=status,
        reason=reason,
        luks=luks,
    )


def formattable(found: list[Device], device: str,
                imported: set[str] | frozenset[str] = frozenset()) -> list[Device]:
    """Entries of the whole disk `device`, or ValueError why it may not be formatted.

    A disk qualifies when nothing on it is in use and every filesystem on it
    is either absent or was copied away by a finished import.
    """
    entries = [d for d in found if d.disk == device]
    if not entries:
        if any(d.path == device for d in found):
            raise ValueError(f"{device} is a partition; only whole disks are formatted")
        raise ValueError(f"{device} is not a disk Dahlia can use")
    for d in entries:
        if d.status in ("system", "registered", "mounted", "foreign", "locked"):
            raise ValueError(f"{device} cannot be formatted: {d.reason}")
        if d.status != "empty" and d.uuid not in imported:
            raise ValueError(f"{d.path} holds data ({d.fstype}); import it before formatting")
    return entries


def partition_path(disk: str) -> str:
    """First partition of a disk: /dev/sdb1, /dev/nvme0n1p1, /dev/loop3p1."""
    return f"{disk}p1" if disk[-1].isdigit() else f"{disk}1"


def allow_loop_from_env() -> bool:
    # Loop devices are only accepted for tests; real systems never pool them.
    return os.environ.get("DAHLIA_ALLOW_LOOP") == "1"
