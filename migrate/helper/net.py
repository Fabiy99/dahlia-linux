"""The server's main network interface and the name it gets under Dahlia.

Unraid renames interfaces to eth0, eth1 ... and puts them into bonds and
bridges; Debian uses the predictable names (eno1, enp3s0). The name is taken
from udev's net_id builtin (onboard, then slot, then path, as systemd's
default policy) and pinned to the MAC address in a .link file.
"""

import re
import subprocess
from pathlib import Path

SYS = Path("/sys/class/net")


def physical(sys_net: Path = SYS) -> list[str]:
    """Interfaces backed by a device (no bridges, bonds, veth, docker)."""
    out = []
    for p in sorted(sys_net.iterdir()) if sys_net.is_dir() else []:
        if (p / "device").exists() and re.fullmatch(r"[a-z0-9]{2,15}", p.name):
            out.append(p.name)
    return out


def main_interface(sys_net: Path = SYS) -> str | None:
    """eth0 (Unraid's first port) if present, else the first one with a link."""
    names = physical(sys_net)
    if "eth0" in names:
        return "eth0"
    for n in names:
        try:
            if (sys_net / n / "carrier").read_text().strip() == "1":
                return n
        except OSError:
            continue
    return names[0] if names else None


def predicted_name(net_id_output: str, fallback: str = "lan0") -> str:
    """Pick the name from `udevadm test-builtin net_id` output."""
    values = dict(re.findall(r"^(ID_NET_NAME_\w+)=(\S+)$", net_id_output, re.M))
    for key in ("ID_NET_NAME_ONBOARD", "ID_NET_NAME_SLOT", "ID_NET_NAME_PATH"):
        name = values.get(key, "")
        if re.fullmatch(r"[a-z0-9]{2,15}", name):
            return name
    return fallback


def mac(name: str, sys_net: Path = SYS) -> str:
    """Permanent MAC (a bond may have changed the current one)."""
    try:
        out = subprocess.run(["ethtool", "-P", name], capture_output=True, text=True,
                             timeout=10, check=False).stdout
        m = re.search(r"(([0-9a-f]{2}:){5}[0-9a-f]{2})", out.lower())
        if m and m.group(1) != "00:00:00:00:00:00":
            return m.group(1)
    except OSError:
        pass
    return (sys_net / name / "address").read_text().strip().lower()


def detect(sys_net: Path = SYS) -> dict | None:
    name = main_interface(sys_net)
    if not name:
        return None
    try:
        out = subprocess.run(["udevadm", "test-builtin", "net_id", str(sys_net / name)],
                             capture_output=True, text=True, timeout=10, check=False).stdout
    except OSError:
        out = ""
    return {"current": name, "name": predicted_name(out), "mac": mac(name, sys_net)}
