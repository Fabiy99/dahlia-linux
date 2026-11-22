"""Read an Unraid configuration (/boot/config on the flash drive and
/var/local/emhttp of the running system) into one summary for Dahlia.

Everything here only reads files; nothing on the Unraid side is changed.
The summary goes to /var/lib/dahlia/unraid/unraid.json on the new system,
next to copies of the raw files, and Dahlia's import reads it there.
"""

import ipaddress
import re
import xml.etree.ElementTree as ET
from pathlib import Path

SECTION_RE = re.compile(r'^\["?([^"\]]+)"?\]\s*$')
VALUE_RE = re.compile(r'^([A-Za-z0-9_.\[\]]+)="(.*)"\s*$')
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
NT_RE = re.compile(r"^[0-9A-F]{32}$")


def read_kv(text: str) -> dict[str, str]:
    """KEY="value" lines (Unraid's .cfg files); [0] suffixes are dropped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = VALUE_RE.match(line.strip())
        if m:
            out[m.group(1).replace("[0]", "")] = m.group(2)
    return out


def read_ini(text: str) -> dict[str, dict[str, str]]:
    """disks.ini: ["name"] sections with KEY="value" lines."""
    out: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        m = SECTION_RE.match(line)
        if m:
            current = out.setdefault(m.group(1), {})
            continue
        m = VALUE_RE.match(line)
        if m and current is not None:
            current[m.group(1)] = m.group(2)
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return ""


ROLES = {"Parity": "parity", "Data": "data", "Cache": "cache", "Flash": "flash"}


def disks(ini: dict[str, dict[str, str]]) -> list[dict]:
    """Assigned disks: slot, role, device (sdb), serial id, file system."""
    out = []
    for slot, d in ini.items():
        role = ROLES.get(d.get("type", ""))
        device = d.get("device", "")
        if not role or not device or not re.fullmatch(r"[a-z0-9]+", device):
            continue
        out.append({
            "slot": slot,
            "role": role,
            "device": device,
            "id": d.get("id", ""),
            "fs": d.get("fsType", ""),
            # Pools other than "cache" are named after their slot (e.g. "nvme").
            "pool": re.sub(r"\d+$", "", slot) if role == "cache" else "",
        })
    order = {"flash": 0, "parity": 1, "data": 2, "cache": 3}
    return sorted(out, key=lambda d: (order[d["role"]], d["slot"]))


def network(cfg: dict[str, str]) -> dict:
    """Main interface only: DHCP or a static IPv4 address with prefix."""
    if not cfg or cfg.get("USE_DHCP", "yes").lower() != "no":
        return {"mode": "dhcp"}
    try:
        iface = ipaddress.IPv4Interface(f"{cfg.get('IPADDR', '')}/{cfg.get('NETMASK', '')}")
        gateway = str(ipaddress.IPv4Address(cfg.get("GATEWAY", "")))
    except ValueError:
        return {"mode": "dhcp"}
    dns = []
    for key in ("DNS_SERVER1", "DNS_SERVER2", "DNS_SERVER3"):
        try:
            dns.append(str(ipaddress.IPv4Address(cfg.get(key, ""))))
        except ValueError:
            continue
    return {"mode": "static", "address": iface.with_prefixlen, "gateway": gateway,
            "dns": dns or [gateway]}


def identity(cfg: dict[str, str]) -> dict:
    name = cfg.get("NAME", "").strip().lower()
    tz = cfg.get("timeZone", "").strip()
    return {
        "hostname": name if HOST_RE.match(name) else "",
        "timezone": tz if re.fullmatch(r"[A-Za-z]+(/[A-Za-z0-9_+-]+){1,2}", tz) else "",
    }


def _names(cfg: dict[str, str], key: str) -> list[str]:
    return [u for u in cfg.get(key, "").split(",") if NAME_RE.match(u)]


SECURITY = {"public": "public", "secure": "secure", "private": "private"}
CACHE = {"no": "no", "yes": "yes", "only": "only", "prefer": "prefer"}


def shares(folder: Path) -> list[dict]:
    out = []
    for f in sorted(folder.glob("*.cfg")) if folder.is_dir() else []:
        name = f.stem
        if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,64}", name):
            continue
        cfg = read_kv(_read(f))
        out.append({
            "name": name,
            "comment": cfg.get("shareComment", ""),
            "smb": cfg.get("shareExport", "e") == "e",
            "security": SECURITY.get(cfg.get("shareSecurity", "public"), "public"),
            "cache": CACHE.get(cfg.get("shareUseCache", "no"), "no"),
            "read": _names(cfg, "shareReadList"),
            "write": _names(cfg, "shareWriteList"),
        })
    return out


def users(config: Path) -> list[dict]:
    """Unraid users (UID 1000 and up) and whether an SMB password hash exists."""
    smb = {}
    for line in _read(config / "smbpasswd").splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and NT_RE.match(parts[3]):
            smb[parts[0]] = True
    out = []
    for line in _read(config / "passwd").splitlines():
        parts = line.split(":")
        if len(parts) < 7 or not parts[2].isdigit():
            continue
        name, uid = parts[0], int(parts[2])
        if uid < 1000 or uid >= 60000 or not NAME_RE.match(name):
            continue
        out.append({"name": name, "smb_password": smb.get(name, False)})
    return out


def _configs(root: ET.Element, kind: str) -> list[ET.Element]:
    return [c for c in root.findall("Config") if c.get("Type") == kind]


def template(text: str) -> dict | None:
    """One dockerMan template (templates-user/my-*.xml) as a container spec."""
    # No DTDs: entity expansion is the risk of parsing XML with the stdlib.
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return None
    try:
        root = ET.fromstring(text)  # noqa: S314 - DTDs rejected above
    except ET.ParseError:
        return None
    if root.tag != "Container":
        return None
    name = (root.findtext("Name") or "").strip()
    image = (root.findtext("Repository") or "").strip()
    if not name or not image:
        return None

    def value(c: ET.Element) -> str:
        return (c.text or c.get("Default") or "").strip()

    ports = []
    for c in _configs(root, "Port"):
        host, target = value(c), (c.get("Target") or "").strip()
        if host.isdigit() and target.isdigit():
            ports.append({"host": int(host), "container": int(target),
                          "protocol": "udp" if c.get("Mode") == "udp" else "tcp"})
    paths = []
    for c in _configs(root, "Path"):
        host, target = value(c), (c.get("Target") or "").strip()
        if host.startswith("/") and target.startswith("/"):
            paths.append({"host": host, "container": target,
                          "readonly": (c.get("Mode") or "rw").startswith("ro")})
    env = {}
    for c in _configs(root, "Variable"):
        key = (c.get("Target") or "").strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            env[key] = value(c)
    devices = [value(c) for c in _configs(root, "Device") if value(c).startswith("/dev/")]
    return {
        "name": name,
        "image": image,
        "network": (root.findtext("Network") or "bridge").strip(),
        "webui": (root.findtext("WebUI") or "").strip(),
        "ports": ports,
        "paths": paths,
        "env": env,
        "devices": devices,
        "privileged": (root.findtext("Privileged") or "").strip().lower() == "true",
    }


def templates(folder: Path) -> list[dict]:
    out = []
    for f in sorted(folder.glob("*.xml")) if folder.is_dir() else []:
        t = template(_read(f))
        if t:
            t["file"] = f.name
            out.append(t)
    return out


def read(config: Path, emhttp: Path) -> dict:
    """Summary of an Unraid system; found=False if config holds no Unraid setup."""
    ident = read_kv(_read(config / "ident.cfg"))
    ini = read_ini(_read(emhttp / "disks.ini"))
    found = bool(ident) or bool(ini)
    docker = read_kv(_read(config / "docker.cfg"))
    wg = config / "wireguard"
    return {
        "found": found,
        "identity": identity(ident),
        "network": network(read_kv(_read(config / "network.cfg"))),
        "disks": disks(ini),
        "shares": shares(config / "shares"),
        "users": users(config),
        "containers": templates(config / "plugins/dockerMan/templates-user"),
        "appdata": docker.get("DOCKER_APP_CONFIG_PATH", "/mnt/user/appdata/"),
        "wireguard": sorted(f.name for f in wg.glob("wg*.conf")) if wg.is_dir() else [],
        "vms": (config / "plugins/dynamix.vm.manager").is_dir(),
    }


# Raw files copied to the new system for the import (paths below config).
COPY = ["ident.cfg", "network.cfg", "docker.cfg", "passwd", "smbpasswd", "shares",
        "plugins/dockerMan/templates-user", "wireguard"]
