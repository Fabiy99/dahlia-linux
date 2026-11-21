"""Replace a data or parity disk (the disk replacement wizard).

The wizard in the dashboard and the app picks a managed disk (also one
that is missing) and a new blank disk at least as large. storage.replace
then, in this order:

  1. drops the old disk from Dahlia (disk remove; data on it stays),
  2. formats the new disk with the old disk's filesystem (encrypted if the
     old one was, with the array passphrase),
  3. adds it under the same number (disk add --index), so /mnt/diskN and
     the SnapRAID name stay the same,
  4. starts the rebuild: a data disk is restored from the parity
     (dahlia-snapraid fix --disk N), a parity disk is computed anew
     (forced sync).

If step 2 or 3 fails and the old disk is still connected, it is added back.
Nothing runs while a sync, scrub, mover or repair is busy.
"""

import json

from .actions import (
    DEVICE_RE,
    FORMAT_FILESYSTEMS,
    PASSPHRASE_RE,
    UUID_RE,
    Context,
    Param,
    action,
    dahlia_disk,
    parity_busy,
    start_parity_unit,
    storage_format,
    storage_list,
)
from .protocol import BUSY, FAILED, INVALID_PARAMS, AgentError

REPLACEABLE = ("data", "parity")


def managed(ctx: Context) -> list[dict]:
    return json.loads(dahlia_disk(ctx, "managed", "--json"))


@action("storage.managed", mutating=False)
def storage_managed(ctx: Context):
    """Disks Dahlia manages, with mount point and whether they are mounted (also missing ones)."""
    return {"disks": managed(ctx)}


def new_disk_size(ctx: Context, device: str) -> int:
    """Size of a whole blank disk from disk list; error if it is not one."""
    entries = [d for d in storage_list(ctx)["disks"] if d.get("disk") == device]
    if not entries:
        raise AgentError(INVALID_PARAMS, f"{device} is not a disk Dahlia can use")
    if any(d.get("status") != "empty" for d in entries):
        raise AgentError(FAILED, f"{device} is not blank")
    return max(int(d.get("size") or 0) for d in entries)


@action(
    "storage.replace",
    mutating=True,
    params={
        "uuid": Param(str, pattern=UUID_RE),
        "device": Param(str, pattern=DEVICE_RE),
        "confirm": Param(str),
        "passphrase": Param(str, required=False, default=None, pattern=PASSPHRASE_RE),
    },
)
def storage_replace(ctx: Context, uuid: str, device: str, confirm: str, passphrase: str | None):
    """Replace a data or parity disk with a new blank one and rebuild it from the parity."""
    old = next((d for d in managed(ctx) if d["uuid"] == uuid), None)
    if old is None or old["role"] not in REPLACEABLE:
        raise AgentError(INVALID_PARAMS, "only a managed data or parity disk can be replaced")
    if confirm != device.removeprefix("/dev/"):
        raise AgentError(INVALID_PARAMS, f"confirm must be '{device.removeprefix('/dev/')}'")
    if old["luks"] and passphrase is None:
        raise AgentError(INVALID_PARAMS, "the old disk was encrypted: 'passphrase' is needed")
    size = new_disk_size(ctx, device)
    if size < int(old.get("size") or 0):
        raise AgentError(FAILED, f"{device} is smaller than the disk it replaces")
    if old["role"] == "parity":
        largest = max((int(d.get("size") or 0) for d in managed(ctx) if d["role"] == "data"),
                      default=0)
        if size < largest:
            raise AgentError(FAILED, f"{device} is smaller than the largest data disk")
    if not any(d["role"] == "parity" for d in managed(ctx) if d["uuid"] != uuid) \
            and old["role"] == "data":
        raise AgentError(FAILED, "without a parity disk the data cannot be rebuilt")
    busy = parity_busy(ctx)
    if busy:
        raise AgentError(BUSY, f"{busy} is running")

    role, index = old["role"], str(old["index"])
    fs = old["fstype"] if old["fstype"] in FORMAT_FILESYSTEMS else "xfs"
    dahlia_disk(ctx, "remove", uuid)
    try:
        new = storage_format(ctx, device, fs, confirm, bool(old["luks"]), passphrase)
        dahlia_disk(ctx, "add", new["uuid"], "--role", role, "--index", index)
    except AgentError:
        # Back to how it was, as far as the old disk is still there.
        ctx.runner.run(["disk", "add", uuid, "--role", role, "--index", index], check=False)
        raise
    if role == "data":
        start_parity_unit(ctx, f"Dahlia parity: restore /mnt/disk{index}", "fix", "--disk", index)
    else:
        start_parity_unit(ctx, f"Dahlia parity: rebuild /mnt/parity{index}", "sync", "--force")
    return {"uuid": new["uuid"], "role": role, "index": int(index), "mountpoint": old["mountpoint"],
            "rebuild": True}
