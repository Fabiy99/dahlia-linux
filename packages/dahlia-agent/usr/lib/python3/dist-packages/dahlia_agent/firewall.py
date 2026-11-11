"""Firewall: one nftables table, inet dahlia, generated from what is in use.

Incoming connections are accepted only from local networks (private IPv4
ranges, IPv6 ULA and link-local, the subnets of the server's own
interfaces, plus networks the admin adds) and only on ports of enabled
functions: SSH, the dashboard and app links on nginx, mDNS, SMB and
WS-Discovery while shares exist, NFS while a share is exported, and the
ports of installed apps.
Container ports Docker forwards (lan_ports) are dropped for other sources.

Docker keeps its own tables; the ruleset never flushes anything but
inet dahlia, so it can be loaded at any time. Settings live in
/etc/dahlia/firewall.json; dahlia-firewall.service loads the table at boot,
and every change of apps, shares or settings loads it again.
"""

import argparse
import ipaddress
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CONFIG = Path("/etc/dahlia/firewall.json")
RULES = Path("/run/dahlia/firewall.nft")
TABLE = "dahlia"
MAX_NETWORKS = 20

PRIVATE4 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
            "169.254.0.0/16")
PRIVATE6 = ("fc00::/7", "fe80::/10")


@dataclass(frozen=True)
class Port:
    port: int
    protocol: str  # tcp or udp
    service: str  # ssh, https, smb, app:<id>, ...

    def to_dict(self) -> dict:
        return {"port": self.port, "protocol": self.protocol, "service": self.service}


BASE_PORTS = (Port(22, "tcp", "ssh"), Port(80, "tcp", "http"), Port(443, "tcp", "https"),
              Port(5353, "udp", "mdns"))
# smbd, plus wsdd so Windows lists the server under Network.
SMB_PORTS = (Port(445, "tcp", "smb"), Port(5357, "tcp", "wsdd"), Port(3702, "udp", "wsdd"))
# NFSv4 only: no rpcbind or mountd from the network.
NFS_PORTS = (Port(2049, "tcp", "nfs"),)


class FirewallError(ValueError):
    pass


# --- settings -----------------------------------------------------------------


def check_networks(networks) -> list[str]:
    """Extra allowed networks: CIDR strings, normalized; FirewallError if invalid."""
    if not isinstance(networks, list) or len(networks) > MAX_NETWORKS:
        raise FirewallError(f"networks: a list of at most {MAX_NETWORKS} networks")
    out = []
    for n in networks:
        try:
            net = ipaddress.ip_network(str(n).strip(), strict=False)
        except ValueError:
            raise FirewallError(f"{n!r} is not a network like 192.168.10.0/24") from None
        if net.prefixlen == 0:
            raise FirewallError("allowing every address turns the firewall off; disable it instead")
        if str(net) not in out:
            out.append(str(net))
    return out


def load_settings(path: Path = CONFIG) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        networks = check_networks(data.get("networks", []))
    except FirewallError:
        networks = []
    return {"enabled": data.get("enabled", True) is not False, "networks": networks}


def save_settings(settings: dict, path: Path = CONFIG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".dahlia-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.chmod(0o644)
    tmp.replace(path)


# --- what is open ---------------------------------------------------------------


def _shares(etc: Path) -> list:
    try:
        data = json.loads((etc / "dahlia/shares.json").read_text())
    except (OSError, ValueError):
        return []
    shares = data.get("shares") if isinstance(data, dict) else None
    return shares if isinstance(shares, list) else []


def shares_active(etc: Path) -> bool:
    return bool(_shares(etc))


def nfs_active(etc: Path) -> bool:
    return any(isinstance(s, dict) and s.get("nfs") is True and s.get("security") != "private"
               for s in _shares(etc))


def app_ports(apps_dir: Path) -> list[Port]:
    """Ports of installed apps: the nginx port link and LAN ports."""
    from . import apps

    paths = apps.Paths(apps=apps_dir)
    out = []
    for app_id in apps.installed(paths):
        try:
            t, _ = apps.load_installed(paths, app_id)
        except (apps.AppError, OSError):
            continue
        if t.public_port:
            out.append(Port(t.public_port, "tcp", f"app:{app_id}"))
        out += [Port(p.host, p.protocol, f"app:{app_id}") for p in t.lan_ports]
    return out


def open_ports(etc: Path, apps_dir: Path) -> list[Port]:
    ports = list(BASE_PORTS)
    if shares_active(etc):
        ports += SMB_PORTS
    if nfs_active(etc):
        ports += NFS_PORTS
    seen = {(p.port, p.protocol) for p in ports}
    for p in app_ports(apps_dir):
        if (p.port, p.protocol) not in seen:
            seen.add((p.port, p.protocol))
            ports.append(p)
    return ports


def local_subnets(run: Callable[[list[str]], str]) -> list[str]:
    """Subnets of the server's global addresses (also the ISP's IPv6 prefix)."""
    try:
        data = json.loads(run(["ip", "-j", "addr", "show"]) or "[]")
    except ValueError:  # FirewallError too: the fixed private ranges still apply
        return []
    out = []
    for iface in data:
        if iface.get("ifname") == "lo":
            continue
        for a in iface.get("addr_info") or []:
            if a.get("scope") != "global" or a.get("family") not in ("inet", "inet6"):
                continue
            try:
                net = ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}").network
            except (KeyError, ValueError):
                continue
            # A /32 or /128 (e.g. a VPN tunnel address) says nothing about the LAN.
            if net.prefixlen < net.max_prefixlen and str(net) not in out:
                out.append(str(net))
    return out


# --- ruleset --------------------------------------------------------------------


def _set(name: str, type_: str, values) -> str:
    return f"    set {name} {{ type {type_}; elements = {{ {', '.join(values)} }} }}"


def ruleset(ports: list[Port], networks: list[str]) -> str:
    """The table inet dahlia, replacing an existing one in one transaction."""
    v4 = list(PRIVATE4) + [n for n in networks if ":" not in n]
    v6 = list(PRIVATE6) + [n for n in networks if ":" in n]
    tcp = sorted({p.port for p in ports if p.protocol == "tcp"})
    udp = sorted({p.port for p in ports if p.protocol == "udp"})
    lines = [
        "# Generated by dahlia-firewall. Changes here are overwritten.",
        f"table inet {TABLE} {{}}",
        f"delete table inet {TABLE}",
        f"table inet {TABLE} {{",
        _set("local4", "ipv4_addr; flags interval; auto-merge", v4),
        _set("local6", "ipv6_addr; flags interval; auto-merge", v6),
    ]
    if tcp:
        lines.append(_set("tcp_ports", "inet_service", map(str, tcp)))
    if udp:
        lines.append(_set("udp_ports", "inet_service", map(str, udp)))
    lines += [
        "    chain input {",
        "        type filter hook input priority filter; policy drop;",
        "        ct state established,related accept",
        "        ct state invalid drop",
        '        iif "lo" accept',
        # Neighbour discovery, router advertisements, path MTU: IPv6 needs them.
        "        meta l4proto ipv6-icmp accept",
        "        icmp type { destination-unreachable, time-exceeded, parameter-problem } accept",
        "        ip saddr @local4 icmp type echo-request accept",
        "        udp sport 67 udp dport 68 accept",
        "        ip6 saddr fe80::/10 udp sport 547 udp dport 546 accept",
    ]
    for proto, setname in (("tcp", "tcp_ports"), ("udp", "udp_ports")):
        if (tcp if proto == "tcp" else udp):
            lines += [f"        ip saddr @local4 {proto} dport @{setname} accept",
                      f"        ip6 saddr @local6 {proto} dport @{setname} accept"]
    lines += [
        # Closed port from the LAN: answer at once instead of a timeout.
        "        ip saddr @local4 reject with icmpx admin-prohibited",
        "        ip6 saddr @local6 reject with icmpx admin-prohibited",
        "    }",
        "    chain forward {",
        "        type filter hook forward priority filter; policy accept;",
        # Container ports Docker publishes (DNAT) only for local sources.
        "        ct status dnat ip saddr != @local4 drop",
        "        ct status dnat ip6 saddr != @local6 drop",
        "    }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def disabled_ruleset() -> str:
    return f"table inet {TABLE} {{}}\ndelete table inet {TABLE}\n"


# --- apply ----------------------------------------------------------------------


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        lines = [ln for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
        raise FirewallError(f"{cmd[0]}: {lines[-1] if lines else proc.returncode}")
    return proc.stdout


@dataclass
class Firewall:
    etc: Path = Path("/etc")
    run_dir: Path = Path("/run/dahlia")
    apps_dir: Path = Path("/var/lib/dahlia/apps")
    run: Callable[[list[str]], str] = _run

    @property
    def config(self) -> Path:
        return self.etc / "dahlia/firewall.json"

    def status(self) -> dict:
        settings = load_settings(self.config)
        return {
            **settings,
            "local": list(PRIVATE4) + list(PRIVATE6),
            "subnets": local_subnets(self.run),
            "ports": [p.to_dict() for p in open_ports(self.etc, self.apps_dir)],
        }

    def apply(self) -> dict:
        """Write the ruleset and load it with nft; returns the status."""
        status = self.status()
        if status["enabled"]:
            text = ruleset([Port(**p) for p in status["ports"]],
                           status["subnets"] + status["networks"])
        else:
            text = disabled_ruleset()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        rules = self.run_dir / "firewall.nft"
        rules.write_text(text)
        # Check first: a broken file must not leave the old table half replaced.
        self.run(["nft", "-c", "-f", str(rules)])
        self.run(["nft", "-f", str(rules)])
        return status

    def set(self, enabled: bool, networks) -> dict:
        settings = {"enabled": bool(enabled), "networks": check_networks(networks)}
        old = self.config.read_text() if self.config.exists() else None
        save_settings(settings, self.config)
        try:
            return self.apply()
        except FirewallError:
            # Keep settings and loaded table in step.
            if old is None:
                self.config.unlink(missing_ok=True)
            else:
                self.config.write_text(old)
            raise


def apply_quietly(fw: Firewall | None = None) -> str:
    """For callers whose own change already worked (apps, shares): "" or the error."""
    try:
        (fw or Firewall()).apply()
    except Exception as e:  # noqa: BLE001 (the caller's change stands either way)
        print(f"dahlia-firewall: {e}", file=sys.stderr)
        return str(e)
    return ""


# --- CLI ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dahlia-firewall", description="Dahlia firewall (nftables).")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("apply", help="load the table inet dahlia (boot, after changes)")
    sub.add_parser("status", help="settings, local networks and open ports as JSON")
    sub.add_parser("show", help="print the ruleset that apply would load")
    sub.add_parser("enable", help="turn the firewall on")
    sub.add_parser("disable", help="turn the firewall off (remove the table)")
    args = p.parse_args(argv)
    fw = Firewall()
    try:
        if args.command == "apply":
            fw.apply()
        elif args.command == "status":
            print(json.dumps(fw.status(), indent=2))
        elif args.command == "show":
            st = fw.status()
            print(ruleset([Port(**x) for x in st["ports"]], st["subnets"] + st["networks"]),
                  end="")
        else:
            fw.set(args.command == "enable", load_settings(fw.config)["networks"])
            print(f"dahlia-firewall: {'enabled' if args.command == 'enable' else 'disabled'}")
    except FirewallError as e:
        print(f"dahlia-firewall: {e}", file=sys.stderr)
        return 1
    return 0
