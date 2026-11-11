"""Network settings of the main interface (ifupdown, /etc/network/interfaces).

A change is applied by dahlia-network in a transient unit. It keeps a
copy of the old configuration and puts it back unless the change is
confirmed within ROLLBACK_SECONDS, so a wrong address does not lock the
admin out of a headless server.
"""

import argparse
import ipaddress
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .actions import Context, Param, Runner, action
from .protocol import BUSY, FAILED, INVALID_PARAMS, AgentError

ROLLBACK_SECONDS = 180
APPLY_UNIT = "dahlia-network-apply"
IFACE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,14}$")
CIDR_RE = re.compile(r"^$|^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
IPV4_RE = re.compile(r"^$|^\d{1,3}(\.\d{1,3}){3}$")
DNS_RE = re.compile(r"^$|^\d{1,3}(\.\d{1,3}){3}(\s*,\s*\d{1,3}(\.\d{1,3}){3}){0,2}$")


@dataclass(frozen=True)
class Paths:
    interfaces: Path
    resolv: Path
    run_dir: Path

    @classmethod
    def of(cls, ctx: Context) -> "Paths":
        return cls(ctx.etc / "network/interfaces", ctx.etc / "resolv.conf", ctx.run_dir)

    def new(self, p: Path) -> Path:
        return p.with_name(p.name + ".dahlia-new")

    def backup(self, p: Path) -> Path:
        return p.with_name(p.name + ".dahlia-backup")

    @property
    def pending(self) -> Path:
        return self.run_dir / "network-pending"

    @property
    def confirmed(self) -> Path:
        return self.run_dir / "network-confirmed"

    @property
    def reverted(self) -> Path:
        return self.run_dir / "network-reverted"


# --- /etc/network/interfaces --------------------------------------------------


def stanza_lines(lines: list[str], start: int) -> int:
    """Index after the indented option lines of the stanza at start."""
    end = start + 1
    while end < len(lines) and lines[end].startswith((" ", "\t")) and lines[end].strip():
        end += 1
    return end


def parse_interfaces(text: str) -> dict | None:
    """The first non-loopback IPv4 interface: name, mode, address, gateway."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        words = line.split()
        if len(words) >= 4 and words[0] == "iface" and words[2] == "inet" and words[1] != "lo" \
                and words[3] in ("dhcp", "static"):
            opts = {}
            for opt in lines[i + 1:stanza_lines(lines, i)]:
                parts = opt.split()
                if len(parts) >= 2:
                    opts[parts[0]] = parts[1]
            address = opts.get("address", "")
            if address and "/" not in address and "netmask" in opts:
                try:
                    prefix = ipaddress.IPv4Network(f"0.0.0.0/{opts['netmask']}").prefixlen
                    address = f"{address}/{prefix}"
                except ValueError:
                    pass
            return {"iface": words[1], "mode": words[3], "address": address,
                    "gateway": opts.get("gateway", "")}
    return None


def render_interfaces(text: str, iface: str, mode: str, address: str, gateway: str) -> str:
    """Replace the IPv4 stanza of iface; everything else stays as it is."""
    lines = text.splitlines()
    new = [f"iface {iface} inet {mode}"]
    if mode == "static":
        new += [f"    address {address}", f"    gateway {gateway}"]
    for i, line in enumerate(lines):
        words = line.split()
        if words[:3] == ["iface", iface, "inet"]:
            lines[i:stanza_lines(lines, i)] = new
            return "\n".join(lines) + "\n"
    raise AgentError(FAILED, f"no IPv4 configuration for {iface} in /etc/network/interfaces")


def read_dns(resolv: Path) -> list[str]:
    try:
        text = resolv.read_text()
    except OSError:
        return []
    return [ln.split()[1] for ln in text.splitlines()
            if ln.split()[:1] == ["nameserver"] and len(ln.split()) > 1]


def check_static(address: str, gateway: str, dns: str) -> list[str]:
    try:
        iface = ipaddress.IPv4Interface(address)
        gw = ipaddress.IPv4Address(gateway)
        servers = [ipaddress.IPv4Address(s.strip()) for s in dns.split(",") if s.strip()]
    except ValueError as e:
        raise AgentError(INVALID_PARAMS, str(e)) from None
    if not 8 <= iface.network.prefixlen <= 30:
        raise AgentError(INVALID_PARAMS, "prefix length must be between 8 and 30")
    if iface.ip in (iface.network.network_address, iface.network.broadcast_address):
        raise AgentError(INVALID_PARAMS, "address is the network or broadcast address")
    if gw not in iface.network or gw == iface.ip:
        raise AgentError(INVALID_PARAMS, "gateway must be another address in the same network")
    if not servers:
        raise AgentError(INVALID_PARAMS, "at least one DNS server is needed")
    return [str(s) for s in servers]


def pending_seconds(paths: Paths) -> int | None:
    try:
        deadline = float(paths.pending.read_text())
    except (OSError, ValueError):
        return None
    return max(0, int(deadline - time.time()))


# --- actions ------------------------------------------------------------------


@action("settings.network", mutating=False)
def settings_network(ctx: Context):
    """Configured mode, address, gateway, DNS and the current addresses."""
    paths = Paths.of(ctx)
    cfg = parse_interfaces(paths.interfaces.read_text()) if paths.interfaces.exists() else None
    if cfg is None:
        raise AgentError(FAILED, "no network interface configured in /etc/network/interfaces")
    proc = ctx.runner.run(["ip", "-j", "-4", "addr", "show", "dev", cfg["iface"]], check=False)
    current = []
    try:
        for link in json.loads(proc.stdout or "[]"):
            for a in link.get("addr_info", []):
                current.append(f"{a['local']}/{a['prefixlen']}")
    except (ValueError, KeyError, TypeError):
        pass
    route = ctx.runner.run(["ip", "-j", "-4", "route", "show", "default"], check=False)
    try:
        gateways = [r["gateway"] for r in json.loads(route.stdout or "[]") if "gateway" in r]
    except (ValueError, TypeError):
        gateways = []
    return {
        **cfg,
        "dns": read_dns(paths.resolv),
        "current": current,
        "current_gateway": gateways[0] if gateways else "",
        "pending": pending_seconds(paths),
        "reverted": paths.reverted.exists(),
    }


@action(
    "settings.set_network",
    mutating=True,
    params={
        "mode": Param(str, choices=("dhcp", "static")),
        "address": Param(str, required=False, default="", pattern=CIDR_RE),
        "gateway": Param(str, required=False, default="", pattern=IPV4_RE),
        "dns": Param(str, required=False, default="", pattern=DNS_RE),
    },
)
def settings_set_network(ctx: Context, mode: str, address: str, gateway: str, dns: str):
    """Switch the main interface to DHCP or a static address, with rollback."""
    paths = Paths.of(ctx)
    if pending_seconds(paths) is not None:
        raise AgentError(BUSY, "a network change is waiting for confirmation")
    text = paths.interfaces.read_text()
    cfg = parse_interfaces(text)
    if cfg is None:
        raise AgentError(FAILED, "no network interface configured in /etc/network/interfaces")
    servers = check_static(address, gateway, dns) if mode == "static" else []
    paths.new(paths.interfaces).write_text(
        render_interfaces(text, cfg["iface"], mode, address, gateway))
    resolv_new = paths.new(paths.resolv)
    if servers:
        resolv_new.write_text("".join(f"nameserver {s}\n" for s in servers))
    else:
        resolv_new.unlink(missing_ok=True)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    for f in (paths.confirmed, paths.reverted):
        f.unlink(missing_ok=True)
    ctx.runner.run(["systemd-run", f"--unit={APPLY_UNIT}", "--collect",
                    "dahlia-network", "apply", "--iface", cfg["iface"]])
    return {"iface": cfg["iface"], "mode": mode, "rollback_seconds": ROLLBACK_SECONDS}


@action("settings.confirm_network", mutating=True)
def settings_confirm_network(ctx: Context):
    """Keep the new network settings; stops the rollback."""
    paths = Paths.of(ctx)
    if pending_seconds(paths) is None:
        raise AgentError(FAILED, "no network change is waiting for confirmation")
    paths.confirmed.touch()
    return {"confirmed": True}


# --- dahlia-network apply -----------------------------------------------------


def swap_in(paths: Paths, runner: Runner, iface: str, files: list[tuple[Path, Path]]) -> None:
    """Take iface down with the config it was brought up with, swap, bring up."""
    runner.run(["ifdown", "--force", iface], timeout=60, check=False)
    for src, dst in files:
        shutil.copyfile(src, dst)
    runner.run(["ip", "addr", "flush", "dev", iface], check=False)
    runner.run(["ifup", iface], timeout=120, check=False)


def apply(paths: Paths, runner: Runner, iface: str, timeout: int = ROLLBACK_SECONDS,
          sleep=time.sleep, clock=time.time) -> bool:
    """Apply the staged config; roll back unless confirmed. True if kept."""
    staged = [(paths.new(paths.interfaces), paths.interfaces)]
    backups = [(paths.backup(paths.interfaces), paths.interfaces)]
    shutil.copyfile(paths.interfaces, paths.backup(paths.interfaces))
    if paths.new(paths.resolv).exists():
        staged.append((paths.new(paths.resolv), paths.resolv))
        if paths.resolv.exists():
            # resolv.conf may be a symlink; keep its content.
            paths.backup(paths.resolv).write_text(paths.resolv.read_text())
            backups.append((paths.backup(paths.resolv), paths.resolv))
        paths.resolv.unlink(missing_ok=True)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.pending.write_text(str(clock() + timeout))
    swap_in(paths, runner, iface, staged)
    for src, _ in staged:
        src.unlink(missing_ok=True)
    deadline = clock() + timeout
    while clock() < deadline:
        if paths.confirmed.exists():
            paths.pending.unlink(missing_ok=True)
            paths.confirmed.unlink(missing_ok=True)
            return True
        sleep(1)
    swap_in(paths, runner, iface, backups)
    paths.pending.unlink(missing_ok=True)
    paths.reverted.touch()
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dahlia-network")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="apply the staged configuration with rollback")
    a.add_argument("--iface", required=True)
    a.add_argument("--timeout", type=int, default=ROLLBACK_SECONDS)
    args = parser.parse_args(argv)
    if not IFACE_RE.match(args.iface):
        parser.error("invalid interface name")
    kept = apply(Paths.of(Context()), Runner(), args.iface, args.timeout)
    print("network change kept" if kept else "network change not confirmed, rolled back",
          file=sys.stderr)
    return 0 if kept else 1


# --- firewall -----------------------------------------------------------------


def firewall_of(ctx: Context):
    from .firewall import Firewall, FirewallError

    def run(cmd: list[str]) -> str:
        try:
            return ctx.runner.run(cmd).stdout
        except AgentError as e:
            raise FirewallError(e.message) from None

    return Firewall(etc=ctx.etc, run_dir=ctx.run_dir, apps_dir=ctx.var / "apps", run=run)


@action("settings.firewall", mutating=False)
def settings_firewall(ctx: Context):
    """Firewall on/off, allowed networks and the ports open for them."""
    return firewall_of(ctx).status()


@action(
    "settings.set_firewall",
    mutating=True,
    params={"enabled": Param(bool), "networks": Param(list, required=False, default=[])},
)
def settings_set_firewall(ctx: Context, enabled: bool, networks: list):
    """Turn the firewall on or off and set extra allowed networks (CIDR)."""
    from .firewall import FirewallError, check_networks

    try:
        networks = check_networks(networks)
    except FirewallError as e:
        raise AgentError(INVALID_PARAMS, str(e)) from None
    try:
        return firewall_of(ctx).set(enabled, networks)
    except FirewallError as e:
        raise AgentError(FAILED, str(e)) from None
