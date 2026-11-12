"""Firewall: one nftables table, inet dahlia, generated from what is in use.

Incoming connections are accepted only from local networks (private IPv4
ranges, IPv6 ULA and link-local, the subnets of the server's own
interfaces, plus networks the admin adds) and only on ports of enabled
functions: SSH, the dashboard and app links on nginx, mDNS, SMB and
WS-Discovery while shares exist, NFS while a share is exported, and the
ports of installed apps.
Container ports Docker forwards (lan_ports) are dropped for other sources.

WireGuard (vpn.py): while the tunnel is on, its UDP port is open from
anywhere, and the tunnel subnet is masqueraded on the way into the LAN.
The NAT stays loaded with the firewall turned off.

Own rules (firewall.json "rules", at most 50) come before the generated
ones, in their order: accept or drop by protocol, port or port range and
source (local networks, any address, or one network). They open ports for
services Dahlia does not know about, or block single devices.

Docker keeps its own tables; the ruleset never flushes anything but
inet dahlia, so it can be loaded at any time. Settings live in
/etc/dahlia/firewall.json; firewall.service loads the table at boot,
and every change of apps, shares or settings loads it again.
"""

import argparse
import ipaddress
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CONFIG = Path("/etc/dahlia/firewall.json")
RULES = Path("/run/dahlia/firewall.nft")
TABLE = "dahlia"
MAX_NETWORKS = 20
MAX_RULES = 50
RULE_ACTIONS = ("accept", "drop")
RULE_PROTOCOLS = ("tcp", "udp", "all")
PORTS_RE = re.compile(r"^([0-9]{1,5})(?:-([0-9]{1,5}))?$")
# Shown in nft list ruleset; no quotes or backslashes.
COMMENT_RE = re.compile(r"^[^\x00-\x1f\x7f\"\\]{0,64}$")

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


def check_rule(rule) -> dict:
    """One own rule, normalized; FirewallError if invalid."""
    if not isinstance(rule, dict):
        raise FirewallError("a rule is an object")
    action = rule.get("action")
    protocol = rule.get("protocol")
    ports = str(rule.get("ports") or "").replace(" ", "")
    source = str(rule.get("source") or "local").strip()
    comment = str(rule.get("comment") or "").strip()
    if action not in RULE_ACTIONS:
        raise FirewallError("action: accept or drop")
    if protocol not in RULE_PROTOCOLS:
        raise FirewallError("protocol: tcp, udp or all")
    if protocol == "all":
        if ports:
            raise FirewallError("ports need tcp or udp")
    else:
        m = PORTS_RE.match(ports)
        if not m:
            raise FirewallError(f"{ports!r} is not a port like 8080 or 8000-8100")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1 <= lo <= hi <= 65535:
            raise FirewallError(f"{ports!r}: ports are 1 to 65535, the lower one first")
        ports = str(lo) if lo == hi else f"{lo}-{hi}"
    if source not in ("local", "any"):
        try:
            net = ipaddress.ip_network(source, strict=False)
        except ValueError:
            raise FirewallError(f"{source!r} is not local, any or a network like "
                                "192.168.1.20/32") from None
        source = "any" if net.prefixlen == 0 else str(net)
    if action == "accept" and source == "any" and protocol == "all":
        raise FirewallError("accepting everything from anywhere turns the firewall off; "
                            "disable it instead")
    if action == "drop" and source in ("local", "any"):
        # Would lock out the dashboard and SSH too.
        raise FirewallError("drop rules need a single device or network as source")
    if not COMMENT_RE.match(comment):
        raise FirewallError("comment: at most 64 characters, no quotes")
    return {"action": action, "protocol": protocol, "ports": ports, "source": source,
            "comment": comment}


def check_rules(rules) -> list[dict]:
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise FirewallError(f"rules: a list of at most {MAX_RULES} rules")
    return [check_rule(r) for r in rules]


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
    try:
        rules = check_rules(data.get("rules", []))
    except FirewallError:
        rules = []
    return {"enabled": data.get("enabled", True) is not False, "networks": networks,
            "rules": rules}


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


def wireguard(etc: Path) -> dict | None:
    """{"port", "subnet", "interface"} while the WireGuard server is on, else None."""
    try:
        data = json.loads((etc / "dahlia/wireguard.json").read_text())
        if not isinstance(data, dict) or data.get("enabled") is not True:
            return None
        port = data["port"]
        subnet = str(ipaddress.ip_network(data["subnet"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    return {"port": port, "subnet": subnet, "interface": "wg"}


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


def nat_chain(vpn: dict | None) -> list[str]:
    if not vpn:
        return []
    return [
        "    chain postrouting {",
        "        type nat hook postrouting priority srcnat; policy accept;",
        f'        ip saddr {vpn["subnet"]} oifname != "{vpn["interface"]}" masquerade',
        "    }",
    ]


def rule_lines(rule: dict) -> list[str]:
    match = "" if rule["protocol"] == "all" else f"{rule['protocol']} dport {rule['ports']} "
    tail = f"{match}{rule['action']}"
    if rule["comment"]:
        tail += f' comment "{rule["comment"]}"'
    src = rule["source"]
    if src == "any":  # always with a port, see check_rule
        return [f"        {tail}"]
    if src == "local":
        return [f"        ip saddr @local4 {tail}", f"        ip6 saddr @local6 {tail}"]
    return [f"        {'ip6' if ':' in src else 'ip'} saddr {src} {tail}"]


def ruleset(ports: list[Port], networks: list[str], vpn: dict | None = None,
            rules: list[dict] | None = None) -> str:
    """The table inet dahlia, replacing an existing one in one transaction."""
    v4 = list(PRIVATE4) + [n for n in networks if ":" not in n]
    v6 = list(PRIVATE6) + [n for n in networks if ":" in n]
    tcp = sorted({p.port for p in ports if p.protocol == "tcp"})
    udp = sorted({p.port for p in ports if p.protocol == "udp"})
    lines = [
        "# Generated by firewall. Changes here are overwritten.",
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
    # Own rules first, so a drop also covers the ports Dahlia opens.
    for rule in rules or []:
        lines += rule_lines(rule)
    if vpn:
        # Clients connect from the internet; WireGuard drops what is not signed.
        lines.append(f"        udp dport {vpn['port']} accept")
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
        *nat_chain(vpn),
        "}",
    ]
    return "\n".join(lines) + "\n"


def disabled_ruleset(vpn: dict | None = None) -> str:
    text = f"table inet {TABLE} {{}}\ndelete table inet {TABLE}\n"
    if vpn:
        text += "\n".join([f"table inet {TABLE} {{", *nat_chain(vpn), "}"]) + "\n"
    return text


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
            "wireguard": wireguard(self.etc),
        }

    def apply(self) -> dict:
        """Write the ruleset and load it with nft; returns the status."""
        status = self.status()
        if status["enabled"]:
            text = ruleset([Port(**p) for p in status["ports"]],
                           status["subnets"] + status["networks"], status["wireguard"],
                           status["rules"])
        else:
            text = disabled_ruleset(status["wireguard"])
        self.run_dir.mkdir(parents=True, exist_ok=True)
        rules = self.run_dir / "firewall.nft"
        rules.write_text(text)
        # Check first: a broken file must not leave the old table half replaced.
        self.run(["nft", "-c", "-f", str(rules)])
        self.run(["nft", "-f", str(rules)])
        return status

    def set(self, enabled: bool, networks) -> dict:
        settings = {**load_settings(self.config), "enabled": bool(enabled),
                    "networks": check_networks(networks)}
        return self._save_and_apply(settings)

    def set_rules(self, rules) -> dict:
        return self._save_and_apply({**load_settings(self.config), "rules": check_rules(rules)})

    def _save_and_apply(self, settings: dict) -> dict:
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
        print(f"firewall: {e}", file=sys.stderr)
        return str(e)
    return ""


# --- CLI ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="firewall", description="Dahlia firewall (nftables).")
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
            print(ruleset([Port(**x) for x in st["ports"]], st["subnets"] + st["networks"],
                          st["wireguard"], st["rules"]), end="")
        else:
            fw.set(args.command == "enable", load_settings(fw.config)["networks"])
            print(f"firewall: {'enabled' if args.command == 'enable' else 'disabled'}")
    except FirewallError as e:
        print(f"firewall: {e}", file=sys.stderr)
        return 1
    return 0
