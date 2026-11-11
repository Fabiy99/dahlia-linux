"""Hardware state and system log: SMART, temperatures, board, journal.

SMART is read with "-n standby", so a sleeping disk stays asleep and is
reported as such instead of being woken up every time the page refreshes.
"""

import json
import re
from pathlib import Path

from .actions import Context, Param, action

# Exit code smartctl uses when the disk is in standby ("-n standby,N").
STANDBY_EXIT = 99
# smartctl exit bits: 1 command line, 2 open failed / no SMART.
SMARTCTL_FATAL = 0b11
# ATA attributes that point at a dying disk when their raw value is not 0.
ATA_WARN = {5: "reallocated", 187: "uncorrectable", 197: "pending", 198: "offline_uncorrectable"}
CPU_SENSORS = ("k10temp", "coretemp", "zenpower", "cpu_thermal", "acpitz")
DMI_FIELDS = ("sys_vendor", "product_name", "board_vendor", "board_name", "bios_version")
UNIT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9@._:-]{0,127}$")
PRIORITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


def list_disks(ctx: Context) -> list[dict]:
    out = ctx.runner.run(["lsblk", "-dJb", "-o", "NAME,TYPE,TRAN,ROTA,SIZE"]).stdout
    disks = []
    for d in json.loads(out or "{}").get("blockdevices", []):
        if d.get("type") != "disk" or str(d.get("name", "")).startswith(("zram", "loop")):
            continue
        disks.append({
            "path": f"/dev/{d['name']}",
            "transport": d.get("tran"),
            "rotational": bool(d.get("rota")) and d.get("rota") not in ("0", 0, False),
            "size": int(d.get("size") or 0),
        })
    return disks


def _test_kind(name: str) -> str:
    name = name.lower()
    if "short" in name:
        return "short"
    if "extended" in name or "long" in name:
        return "long"
    return "other"


def _ata_result(value: int) -> str:
    # Upper nibble of the ATA self-test status byte.
    code = value >> 4
    if code == 0:
        return "passed"
    if code in (1, 2):
        return "aborted"
    if code == 15:
        return "running"
    return "failed"


def _nvme_result(value: int) -> str:
    if value == 0:
        return "passed"
    if value in (1, 2, 3):
        return "aborted"
    return "failed"


def self_test(data: dict) -> dict:
    """Running self-test and the newest finished one, for ATA and NVMe."""
    running, remaining, last = False, None, None
    ata = data.get("ata_smart_data", {}).get("self_test", {}).get("status", {})
    if "value" in ata and ata["value"] >> 4 == 15:
        running, remaining = True, ata.get("remaining_percent")
    table = data.get("ata_smart_self_test_log", {}).get("standard", {}).get("table") or []
    for row in table:
        result = _ata_result(row.get("status", {}).get("value", 0))
        if result != "running":
            last = {"kind": _test_kind(row.get("type", {}).get("string", "")), "result": result,
                    "hours": row.get("lifetime_hours")}
            break
    nvme = data.get("nvme_self_test_log", {})
    if nvme.get("current_self_test_operation", {}).get("value"):
        running = True
        done = nvme.get("current_self_test_completion_percent")
        remaining = 100 - done if isinstance(done, int) else None
    for row in nvme.get("table") or []:
        last = {"kind": _test_kind(row.get("self_test_code", {}).get("string", "")),
                "result": _nvme_result(row.get("self_test_result", {}).get("value", 0)),
                "hours": row.get("power_on_hours")}
        break
    return {"running": running, "remaining": remaining, "last": last}


def smart_summary(data: dict) -> dict:
    """The few values a NAS shows per disk, from smartctl -j output."""
    status = data.get("smart_status", {})
    warnings = {}
    for attr in data.get("ata_smart_attributes", {}).get("table", []):
        name = ATA_WARN.get(attr.get("id"))
        raw = attr.get("raw", {}).get("value", 0)
        if name and raw:
            warnings[name] = raw
    nvme = data.get("nvme_smart_health_information_log", {})
    if nvme.get("media_errors"):
        warnings["media_errors"] = nvme["media_errors"]
    if nvme.get("critical_warning"):
        warnings["critical_warning"] = nvme["critical_warning"]
    if "passed" not in status:
        health = "unknown"
    elif not status["passed"]:
        health = "failed"
    else:
        health = "warning" if warnings else "passed"
    return {
        "health": health,
        "model": data.get("model_name"),
        "serial": data.get("serial_number"),
        "temperature": data.get("temperature", {}).get("current"),
        "power_on_hours": data.get("power_on_time", {}).get("hours"),
        "wear_percent": nvme.get("percentage_used"),
        "warnings": warnings,
        "self_test": self_test(data),
    }


def read_smart(ctx: Context, path: str) -> dict:
    proc = ctx.runner.run(
        ["smartctl", "-j", "-a", "-n", f"standby,{STANDBY_EXIT}", path], timeout=60, check=False
    )
    if proc.returncode == STANDBY_EXIT:
        return {"health": "standby"}
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        data = {}
    if proc.returncode & SMARTCTL_FATAL or "smart_status" not in data and "model_name" not in data:
        return {"health": "unavailable"}
    return smart_summary(data)


def read_temp(path: Path) -> float | None:
    try:
        return round(int(path.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def cpu_temperature(sys_root: Path) -> float | None:
    """Package temperature of the CPU from hwmon, None in most VMs."""
    found = {}
    for hw in sorted((sys_root / "class/hwmon").glob("hwmon*")):
        try:
            name = (hw / "name").read_text().strip()
        except OSError:
            continue
        if name in CPU_SENSORS and name not in found:
            temp = read_temp(hw / "temp1_input")
            if temp is not None:
                found[name] = temp
    for name in CPU_SENSORS:
        if name in found:
            return found[name]
    return None


def board_info(sys_root: Path) -> dict:
    out = {}
    for f in DMI_FIELDS:
        try:
            value = (sys_root / "class/dmi/id" / f).read_text().strip()
        except OSError:
            value = ""
        out[f] = value or None
    return out


GPU_VENDORS = {"0x8086": "Intel", "0x1002": "AMD", "0x10de": "NVIDIA"}


def gpus(sys_root: Path) -> list[dict]:
    """Graphics devices with a DRM driver; render = usable by apps (/dev/dri/renderD*)."""
    out = []
    for card in sorted((sys_root / "class/drm").glob("card[0-9]*")):
        if "-" in card.name:  # connectors such as card0-HDMI-A-1
            continue
        dev = card / "device"
        try:
            vendor = (dev / "vendor").read_text().strip()
        except OSError:
            vendor = ""
        driver = (dev / "driver").resolve().name if (dev / "driver").exists() else None
        render = sorted(p.name for p in (dev / "drm").glob("renderD*"))
        out.append({"card": card.name, "vendor": GPU_VENDORS.get(vendor, vendor or None),
                    "driver": driver, "render": render[0] if render else None})
    return out


@action("system.hardware", mutating=False)
def system_hardware(ctx: Context):
    """Board, CPU temperature, graphics devices and SMART state of every disk."""
    disks = []
    for d in list_disks(ctx):
        disks.append({**d, **read_smart(ctx, d["path"])})
    return {
        "board": board_info(ctx.sys),
        "cpu_temperature": cpu_temperature(ctx.sys),
        "gpus": gpus(ctx.sys),
        "disks": disks,
    }


@action(
    "system.log",
    mutating=False,
    params={
        "lines": Param(int, required=False, default=300, minimum=10, maximum=5000),
        "priority": Param(str, required=False, default="info", choices=PRIORITIES),
        "unit": Param(str, required=False, default="",
                      pattern=re.compile(r"^$|" + UNIT_RE.pattern)),
    },
)
def system_log(ctx: Context, lines: int, priority: str, unit: str):
    """The last lines of the system journal, optionally of one unit."""
    cmd = ["journalctl", "--no-pager", "-q", "-o", "short-iso", "-n", str(lines), "-p", priority]
    if unit:
        cmd += ["-u", unit]
    proc = ctx.runner.run(cmd, timeout=30)
    return {"lines": proc.stdout.splitlines()}
