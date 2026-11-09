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
    return json.loads(out)


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


def allow_loop_from_env() -> bool:
    # Loop devices are only accepted for tests; real systems never pool them.
    return os.environ.get("DAHLIA_ALLOW_LOOP") == "1"
