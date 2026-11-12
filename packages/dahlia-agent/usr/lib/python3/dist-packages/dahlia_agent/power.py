"""CPU frequency governor and the list of PCI and USB devices.

The governor is written to every cpufreq policy in sysfs and kept over
reboots with systemd-tmpfiles (/etc/tmpfiles.d/dahlia-cpu-governor.conf,
a "w" line). Most VMs have no cpufreq; then nothing can be set.

Devices come from lspci and lsusb; the IOMMU group matters for passing a
device to a VM later (version 2).
"""

import re
from pathlib import Path

from .actions import Context, Param, action
from .protocol import FAILED, INVALID_PARAMS, AgentError
from .settings import write_file

GOVERNOR_RE = re.compile(r"^[a-z0-9_]{1,32}$")
# Shown first in the UI, in this order; others follow as the kernel lists them.
KNOWN = ("powersave", "schedutil", "ondemand", "conservative", "performance", "userspace")


def policies(ctx: Context) -> list[Path]:
    return sorted((ctx.sys / "devices/system/cpu/cpufreq").glob("policy*"))


def read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def tmpfiles_conf(ctx: Context) -> Path:
    return ctx.etc / "tmpfiles.d/dahlia-cpu-governor.conf"


def governor_status(ctx: Context) -> dict:
    pols = policies(ctx)
    if not pols:
        return {"available": [], "current": None, "driver": None, "saved": None,
                "frequencies": []}
    avail = read(pols[0] / "scaling_available_governors").split()
    current = sorted({read(p / "scaling_governor") for p in pols} - {""})
    saved = None
    m = re.search(r"\s(\S+)\s*$", read(tmpfiles_conf(ctx)))
    if m and GOVERNOR_RE.match(m.group(1)):
        saved = m.group(1)
    freqs = []
    for p in pols:
        cur = read(p / "scaling_cur_freq")
        if cur.isdigit():
            freqs.append(int(cur) // 1000)  # MHz
    order = [g for g in KNOWN if g in avail] + [g for g in avail if g not in KNOWN]
    return {"available": order, "current": current[0] if len(current) == 1 else
            (",".join(current) or None), "driver": read(pols[0] / "scaling_driver") or None,
            "saved": saved, "frequencies": freqs}


@action("power.get", mutating=False)
def power_get(ctx: Context):
    """CPU governor: available, current, saved for boot, driver, MHz per policy."""
    return governor_status(ctx)


@action("power.set_governor", mutating=True,
        params={"governor": Param(str, pattern=GOVERNOR_RE)})
def power_set_governor(ctx: Context, governor: str):
    """Set the governor on all CPUs now and at every boot."""
    status = governor_status(ctx)
    if not status["available"]:
        raise AgentError(FAILED, "this system has no CPU frequency scaling (cpufreq)")
    if governor not in status["available"]:
        raise AgentError(INVALID_PARAMS, f"governor must be one of: "
                         f"{', '.join(status['available'])}")
    for p in policies(ctx):
        try:
            (p / "scaling_governor").write_text(governor)
        except OSError as e:
            raise AgentError(FAILED, f"{p.name}: {e.strerror or e}") from None
    write_file(tmpfiles_conf(ctx),
               "# Written by dahlia-agent (Settings > Power saving).\n"
               f"w /sys/devices/system/cpu/cpufreq/policy*/scaling_governor - - - - {governor}\n")
    return governor_status(ctx)


# --- devices --------------------------------------------------------------------

BRACKET_RE = re.compile(r"^(.*?)\s*\[([0-9a-fA-F]{4})\]$")
USB_RE = re.compile(r"^Bus (\d{3}) Device (\d{3}): ID ([0-9a-f]{4}):([0-9a-f]{4})\s*(.*)$")


def split_name(value: str) -> tuple[str, str | None]:
    """"Intel Corporation [8086]" -> ("Intel Corporation", "8086")."""
    m = BRACKET_RE.match(value.strip())
    return (m.group(1), m.group(2).lower()) if m else (value.strip(), None)


def parse_lspci(text: str) -> list[dict]:
    """Output of lspci -vmm -nn -k: one block per device."""
    out = []
    for block in text.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields[key.strip()] = value.strip()
        if "Slot" not in fields:
            continue
        cls, cls_id = split_name(fields.get("Class", ""))
        vendor, vendor_id = split_name(fields.get("Vendor", ""))
        device, device_id = split_name(fields.get("Device", ""))
        group = fields.get("IOMMUGroup")
        out.append({"slot": fields["Slot"], "class": cls, "class_id": cls_id,
                    "vendor": vendor, "device": device,
                    "id": f"{vendor_id}:{device_id}" if vendor_id and device_id else None,
                    "driver": fields.get("Driver"),
                    "iommu_group": int(group) if group and group.isdigit() else None})
    return out


def parse_lsusb(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = USB_RE.match(line.strip())
        if m:
            out.append({"bus": int(m.group(1)), "device": int(m.group(2)),
                        "id": f"{m.group(3)}:{m.group(4)}", "name": m.group(5).strip(),
                        "hub": m.group(3) == "1d6b"})  # Linux Foundation root hubs
    return out


@action("system.devices", mutating=False)
def system_devices(ctx: Context):
    """PCI devices (class, IDs, driver, IOMMU group) and USB devices."""
    pci = ctx.runner.run(["lspci", "-vmm", "-nn", "-k"], check=False)
    usb = ctx.runner.run(["lsusb"], check=False)
    return {"pci": parse_lspci(pci.stdout or "") if pci.returncode == 0 else [],
            "usb": parse_lsusb(usb.stdout or "") if usb.returncode == 0 else [],
            "iommu": any((ctx.sys / "kernel/iommu_groups").glob("*"))}
