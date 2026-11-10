"""Block device discovery and classification.

Reads `lsblk --json` and decides for every filesystem whether Dahlia may use
it. The rules are conservative: anything that belongs to the running system,
is already mounted, or is part of RAID/LVM/LUKS/ZFS is never offered.
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
    # candidate, external-only, empty, system, mounted, foreign, unsupported, registered
    status: str
    reason: str

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


def blkid_probe(path: str) -> dict[str, str]:
    """Read the filesystem signature straight from the device.

    lsblk takes FSTYPE/UUID from the udev database, which lags behind after
    mkfs or on loop devices. `blkid -p` probes the device itself.
    """
    res = subprocess.run(
        ["blkid", "-p", "-o", "export", path], check=False, capture_output=True, text=True
    )
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


def _tree_has_foreign(node: dict) -> bool:
    if node.get("fstype") in FOREIGN_SIGNATURES:
        return True
    return any(_tree_has_foreign(c) for c in node.get("children") or [])


def classify(
    lsblk: dict,
    registered_uuids: set[str] | None = None,
    allow_loop: bool = False,
) -> list[Device]:
    """Return one Device per usable-or-rejected filesystem/disk, with a status."""
    registered_uuids = registered_uuids or set()
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

        # Whole disk without partition table and without filesystem: blank.
        if not children and not top.get("fstype"):
            status, reason = ("system", "system disk") if is_system else ("empty", "no filesystem")
            result.append(_device(top, top, status, reason))
            continue

        # Filesystem directly on the disk, or one entry per partition.
        nodes = [top] if not children else [c for c in children if c.get("type") == "part"]
        for node in nodes:
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


def _device(node: dict, disk: dict, status: str, reason: str) -> Device:
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
    )


def formattable(found: list[Device], device: str) -> Device:
    """The blank whole disk `device`, or ValueError why it may not be formatted."""
    dev = next((d for d in found if d.path == device), None)
    if dev is None:
        raise ValueError(f"{device} is not a blank disk")
    if dev.path != dev.disk:
        raise ValueError(f"{device} is a partition; only whole blank disks are formatted")
    if dev.status != "empty":
        raise ValueError(f"{device} cannot be formatted: {dev.reason}")
    return dev


def partition_path(disk: str) -> str:
    """First partition of a disk: /dev/sdb1, /dev/nvme0n1p1, /dev/loop3p1."""
    return f"{disk}p1" if disk[-1].isdigit() else f"{disk}1"


def allow_loop_from_env() -> bool:
    # Loop devices are only accepted for tests; real systems never pool them.
    return os.environ.get("DAHLIA_ALLOW_LOOP") == "1"
