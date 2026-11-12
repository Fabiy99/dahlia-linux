"""Network layout: bond, bridge and VLANs on top of the physical ports, and
Wake-on-LAN per port.

The layout is written as a whole /etc/network/interfaces (ifupdown with
ifenslave and bridge-utils) and applied by network with the same
rollback as an address change. The IPv4 settings of the main interface
(DHCP or static) move along to the new main interface (bond0, br0 or the
port). Layers, bottom up: ports -> bond0 (two or more ports) -> br0
(optional) -> VLANs <main>.<id> (static address or no address).

Wake-on-LAN: ethtool -s <port> wol g now, and wol@<port>.service
sets it again at every boot (drivers reset it).
"""

import ipaddress
import re

from .actions import Context, Param, action
from .network import (
    APPLY_UNIT,
    ROLLBACK_SECONDS,
    Paths,
    parse_interfaces,
    pending_seconds,
    stanza_lines,
)
from .protocol import BUSY, FAILED, INVALID_PARAMS, AgentError

BOND = "bond0"
BRIDGE = "br0"
BOND_MODES = ("active-backup", "802.3ad", "balance-alb")
VLAN_MODES = ("static", "manual")
MAX_PORTS = 4
MAX_VLANS = 8
HEADER = ("# Written by agent (Settings > Network layout).\n"
          "# Changing the layout in the dashboard rewrites this file.\n")
SUPPORTS_RE = re.compile(r"^\s*Supports Wake-on:\s*(\S+)", re.M)
WAKE_RE = re.compile(r"^\s*Wake-on:\s*(\S+)", re.M)


# --- physical ports ---------------------------------------------------------------


def read(path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def ports(ctx: Context) -> list[dict]:
    """Wired network ports: name, MAC, link, speed, Wake-on-LAN support and state."""
    out = []
    for d in sorted((ctx.sys / "class/net").glob("*")):
        if not (d / "device").exists() or (d / "wireless").exists():
            continue
        speed = read(d / "speed")
        eth = ctx.runner.run(["ethtool", d.name], check=False).stdout or ""
        supports = SUPPORTS_RE.search(eth)
        wake = WAKE_RE.search(eth)
        out.append({
            "name": d.name,
            "mac": read(d / "address"),
            "link": read(d / "carrier") == "1",
            "speed": int(speed) if speed.isdigit() else None,
            "wol_supported": bool(supports and "g" in supports.group(1)),
            "wol": bool(wake and "g" in wake.group(1)),
            "wol_at_boot": wol_unit_file(ctx, d.name).exists(),
        })
    return out


def wol_unit_file(ctx: Context, port: str):
    return ctx.etc / f"systemd/system/multi-user.target.wants/wol@{port}.service"


# --- /etc/network/interfaces ------------------------------------------------------


def stanzas(text: str) -> dict[str, dict[str, str]]:
    """iface name -> {"method": ..., option: value} for the inet stanzas."""
    lines = text.splitlines()
    out = {}
    for i, line in enumerate(lines):
        words = line.split()
        if len(words) >= 4 and words[0] == "iface" and words[2] == "inet":
            opts = {"method": words[3]}
            for opt in lines[i + 1:stanza_lines(lines, i)]:
                key, _, value = opt.strip().partition(" ")
                opts[key] = value.strip()
            out[words[1]] = opts
    return out


def parse_layout(text: str) -> dict:
    """The layout of an interfaces file: ports, bond mode, bridge, VLANs."""
    cfg = parse_interfaces(text)
    if cfg is None:
        raise AgentError(FAILED, "no network interface configured in /etc/network/interfaces")
    st = stanzas(text)
    main = cfg["iface"]
    lower = main
    bridge = "bridge_ports" in st.get(main, {})
    if bridge:
        lower = st[main]["bridge_ports"].split()[0]
    bond = st.get(lower, {}) if "bond-slaves" in st.get(lower, {}) else None
    nics = bond["bond-slaves"].split() if bond else [lower]
    vlans = []
    for name, opts in st.items():
        head, _, vid = name.rpartition(".")
        if head == main and vid.isdigit() and opts["method"] in VLAN_MODES:
            vlans.append({"id": int(vid), "mode": opts["method"],
                          "address": opts.get("address", "")})
    return {"main": main, "nics": nics, "bond_mode": bond.get("bond-mode", "") if bond else "",
            "bridge": bridge, "vlans": vlans, "managed": text.startswith(HEADER)}


def render_layout(nics: list[str], bond_mode: str, bridge: bool, vlans: list[dict],
                  mode: str, address: str, gateway: str) -> str:
    """A complete interfaces file for the layout; main gets the IPv4 settings."""
    blocks = [HEADER + "source /etc/network/interfaces.d/*",
              "auto lo\niface lo inet loopback"]
    lower = nics[0]
    if bond_mode:
        for n in nics:
            blocks.append(f"iface {n} inet manual")
        opts = [f"bond-slaves {' '.join(nics)}", f"bond-mode {bond_mode}", "bond-miimon 100"]
        if bond_mode == "active-backup":
            opts.append(f"bond-primary {nics[0]}")
        if bond_mode == "802.3ad":
            opts.append("bond-xmit-hash-policy layer3+4")
        lower = BOND
        if bridge:
            blocks.append(f"auto {BOND}\niface {BOND} inet manual\n" +
                          "\n".join(f"    {o}" for o in opts))
            opts = []
    else:
        opts = []
    main = lower
    if bridge:
        if not bond_mode:
            blocks.append(f"iface {lower} inet manual")
        opts = [f"bridge_ports {lower}", "bridge_stp off", "bridge_fd 0",
                "bridge_waitport 0"]
        main = BRIDGE
    ip = [f"address {address}", f"gateway {gateway}"] if mode == "static" else []
    blocks.append(f"auto {main}\niface {main} inet {mode}\n" +
                  "\n".join(f"    {o}" for o in ip + opts) +
                  f"\niface {main} inet6 auto")
    for v in vlans:
        name = f"{main}.{v['id']}"
        text = f"auto {name}\niface {name} inet {v['mode']}"
        if v["mode"] == "static":
            text += f"\n    address {v['address']}"
        blocks.append(text)
    return "\n\n".join(b.replace("\n\n", "\n") for b in blocks) + "\n"


def check_vlans(vlans, main_address: str) -> list[dict]:
    if not isinstance(vlans, list) or len(vlans) > MAX_VLANS:
        raise AgentError(INVALID_PARAMS, f"vlans: at most {MAX_VLANS}")
    out, ids, nets = [], set(), []
    if main_address:
        nets.append(ipaddress.IPv4Interface(main_address).network)
    for v in vlans:
        if not isinstance(v, dict) or type(v.get("id")) is not int or not 1 <= v["id"] <= 4094:
            raise AgentError(INVALID_PARAMS, "vlan id: 1-4094")
        if v["id"] in ids:
            raise AgentError(INVALID_PARAMS, f"vlan {v['id']} twice")
        ids.add(v["id"])
        mode = v.get("mode")
        if mode not in VLAN_MODES:
            raise AgentError(INVALID_PARAMS, f"vlan {v['id']}: mode static or manual")
        address = ""
        if mode == "static":
            try:
                iface = ipaddress.IPv4Interface(str(v.get("address", "")))
            except ValueError:
                raise AgentError(INVALID_PARAMS,
                                 f"vlan {v['id']}: address like 10.0.10.5/24") from None
            if not 8 <= iface.network.prefixlen <= 30 or iface.ip in (
                    iface.network.network_address, iface.network.broadcast_address):
                raise AgentError(INVALID_PARAMS, f"vlan {v['id']}: not a host address")
            if any(iface.network.overlaps(n) for n in nets):
                raise AgentError(INVALID_PARAMS,
                                 f"vlan {v['id']}: network overlaps another interface")
            nets.append(iface.network)
            address = str(iface)
        out.append({"id": v["id"], "mode": mode, "address": address})
    return out


# --- actions ------------------------------------------------------------------------


@action("settings.network_layout", mutating=False)
def settings_network_layout(ctx: Context):
    """Ports (link, speed, MAC, Wake-on-LAN) and the configured bond, bridge and VLANs."""
    paths = Paths.of(ctx)
    text = paths.interfaces.read_text() if paths.interfaces.exists() else ""
    return {**parse_layout(text), "ports": ports(ctx), "bond_modes": list(BOND_MODES),
            "pending": pending_seconds(paths)}


@action(
    "settings.set_network_layout",
    mutating=True,
    params={
        "nics": Param(list),
        "bond_mode": Param(str, required=False, default="", choices=("", *BOND_MODES)),
        "bridge": Param(bool, required=False, default=False),
        "vlans": Param(list, required=False, default=[]),
    },
)
def settings_set_network_layout(ctx: Context, nics: list, bond_mode: str, bridge: bool,
                                vlans: list):
    """Rewrite the layout (ports, bond, bridge, VLANs) and apply it with rollback."""
    paths = Paths.of(ctx)
    if pending_seconds(paths) is not None:
        raise AgentError(BUSY, "a network change is waiting for confirmation")
    known = {p["name"] for p in ports(ctx)}
    if not nics or len(nics) > MAX_PORTS or len(set(nics)) != len(nics) \
            or not all(isinstance(n, str) and n in known for n in nics):
        raise AgentError(INVALID_PARAMS, f"nics: 1 to {MAX_PORTS} of: {', '.join(sorted(known))}")
    if len(nics) > 1 and not bond_mode:
        raise AgentError(INVALID_PARAMS, "two or more ports need a bond mode")
    if len(nics) == 1 and bond_mode:
        raise AgentError(INVALID_PARAMS, "a bond needs at least two ports")
    text = paths.interfaces.read_text()
    old = parse_layout(text)
    cfg = parse_interfaces(text)
    vlans = check_vlans(vlans, cfg["address"] if cfg["mode"] == "static" else "")
    main = BRIDGE if bridge else BOND if bond_mode else nics[0]
    if any(len(f"{main}.{v['id']}") > 15 for v in vlans):
        raise AgentError(INVALID_PARAMS, f"VLAN names on {main} would be longer than 15 characters")
    if not cfg["address"] and cfg["mode"] == "static":
        raise AgentError(FAILED, "the main interface has no address")
    paths.new(paths.interfaces).write_text(render_layout(
        nics, bond_mode, bridge, vlans, cfg["mode"], cfg["address"], cfg["gateway"]))
    paths.new(paths.resolv).unlink(missing_ok=True)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    for f in (paths.confirmed, paths.reverted):
        f.unlink(missing_ok=True)
    ctx.runner.run(["systemd-run", f"--unit={APPLY_UNIT}", "--collect",
                    "network", "apply", "--iface", old["main"]])
    return {"nics": nics, "bond_mode": bond_mode, "bridge": bridge, "vlans": vlans,
            "rollback_seconds": ROLLBACK_SECONDS}


@action(
    "settings.set_wol",
    mutating=True,
    params={"port": Param(str), "enabled": Param(bool)},
)
def settings_set_wol(ctx: Context, port: str, enabled: bool):
    """Wake-on-LAN (magic packet) for a port, now and at every boot."""
    found = next((p for p in ports(ctx) if p["name"] == port), None)
    if found is None:
        raise AgentError(INVALID_PARAMS, f"{port!r} is not a network port")
    if enabled and not found["wol_supported"]:
        raise AgentError(FAILED, f"{port} does not support Wake-on-LAN")
    proc = ctx.runner.run(["ethtool", "-s", port, "wol", "g" if enabled else "d"], check=False)
    if proc.returncode != 0:
        raise AgentError(FAILED, f"ethtool: {(proc.stderr or proc.stdout or '').strip()}")
    unit = f"wol@{port}.service"
    ctx.runner.run(["systemctl", "enable" if enabled else "disable", unit], check=False)
    return next(p for p in ports(ctx) if p["name"] == port)
