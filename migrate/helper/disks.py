"""Disks the helper may install Dahlia on.

A disk is locked when Unraid uses it (array, parity, cache and pools, the
flash drive), when it is smaller than MIN_SIZE, or when it is not a plain
local disk. A free disk that still holds partitions or file systems can be
chosen, but only after typing its serial number to confirm the erase.
"""

import json
import re
import subprocess
from collections.abc import Callable, Sequence

MIN_SIZE = 20 * 1000**3
LSBLK = ["lsblk", "-J", "-b", "-o",
         "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,ROTA,RM,RO,FSTYPE,LABEL,PTTYPE"]

Run = Callable[[Sequence[str]], str]


def run(cmd: Sequence[str]) -> str:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=True,
                          timeout=60).stdout


def _flat(dev: dict) -> list[dict]:
    out = []
    for child in dev.get("children") or []:
        out.append(child)
        out.extend(_flat(child))
    return out


def scan(unraid_disks: list[dict], runner: Run = run, allow_loop: bool = False) -> list[dict]:
    """Whole disks with size, model, serial and lock state."""
    used = {d["device"]: d for d in unraid_disks}
    data = json.loads(runner(LSBLK) or "{}")
    out = []
    for dev in data.get("blockdevices", []):
        kind = dev.get("type")
        name = dev.get("name") or ""
        if kind not in ("disk", "loop") or (kind == "loop" and not allow_loop):
            continue
        if re.match(r"^(zram|ram|sr|md|nbd)", name):
            continue
        size = int(dev.get("size") or 0)
        if kind == "loop" and size == 0:
            continue
        parts = _flat(dev)
        fs = sorted({p["fstype"] for p in parts + [dev] if p.get("fstype")})
        lock = ""
        role = used.get(name)
        if role:
            lock = "unraid_" + role["role"]
        elif dev.get("ro") in (True, "1", 1):
            lock = "readonly"
        elif size < MIN_SIZE:
            lock = "too_small"
        elif dev.get("rm") in (True, "1", 1) and dev.get("tran") == "usb":
            lock = "removable"
        out.append({
            "name": name,
            "path": dev.get("path") or f"/dev/{name}",
            "size": size,
            "model": (dev.get("model") or "").strip(),
            "serial": (dev.get("serial") or "").strip(),
            "transport": dev.get("tran") or "",
            "ssd": dev.get("rota") in (False, "0", 0),
            "has_data": bool(parts or fs or dev.get("pttype")),
            "filesystems": fs,
            "lock": lock,
            "unraid_slot": role["slot"] if role else "",
        })
    return out


def confirm_ok(disk: dict, typed: str) -> bool:
    """A disk with data needs its serial (or, without one, its name) typed."""
    if not disk["has_data"]:
        return True
    expected = disk["serial"] or disk["name"]
    return typed.strip().upper() == expected.upper()
