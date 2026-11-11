"""Remote access: a WireGuard server and Tailscale.

WireGuard: Dahlia runs the interface dahlia-wg with wg-quick
(wg-quick@dahlia-wg.service). Settings and peers live in
/etc/dahlia/wireguard.json (0600), the server key in
/etc/wireguard/dahlia-wg.key, and /etc/wireguard/dahlia-wg.conf is generated
from both. A peer (phone, laptop) gets a key pair and a preshared key made
on the server; its configuration and QR code are shown once and only its
public key is kept. Clients reach the LAN (route "lan") or send all traffic
through the server (route "all"). The firewall opens the UDP port from
anywhere and masquerades the tunnel subnet (firewall.py); IPv4 forwarding
is switched on by /etc/sysctl.d/60-dahlia-forward.conf. The router has to
forward the UDP port to the server.

Tailscale: not in Debian. Installing adds Tailscale's apt repository
(/etc/apt/sources.list.d/tailscale.list, key in /usr/share/keyrings, as in
Tailscale's own instructions) and the package tailscale. Logging in runs
`tailscale up` in the background; the login link comes from
`tailscale status --json` until the server is in the tailnet. Options:
offer the LAN subnets as routes and act as exit node. Tailscale addresses
(100.64.0.0/10) count as local for the firewall.
"""

import datetime
import ipaddress
import json
import re
from pathlib import Path

from .actions import Context, Param, action, is_active, unit_state
from .firewall import local_subnets
from .network import firewall_of
from .protocol import BUSY, FAILED, INVALID_PARAMS, AgentError
from .settings import read_json, write_file

IFACE = "dahlia-wg"
WG_UNIT = f"wg-quick@{IFACE}.service"
FORWARD_SCRIPT = "/usr/lib/dahlia/wireguard-forward.sh"
DEFAULT_SUBNET = "10.253.0.0/24"
DEFAULT_PORT = 51820
ROUTES = ("lan", "all")
MAX_PEERS = 250
KEEPALIVE = 25
PEER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$")
LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
ENDPOINT_RE = re.compile(rf"^(?:{LABEL}(?:\.{LABEL})*|\[[0-9A-Fa-f:.]{{2,45}}\])$")
SUBNET_RE = re.compile(r"^(10|172|192)\.[0-9]{1,3}\.[0-9]{1,3}\.0/(2[0-4]|1[6-9])$")
DNS_RE = re.compile(r"^$|^[0-9]{1,3}(\.[0-9]{1,3}){3}$")
KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$")

TAILSCALE = Path("/usr/bin/tailscale")
TS_INSTALL = "/usr/lib/dahlia/tailscale-install.sh"
TS_INSTALL_UNIT = "dahlia-tailscale-install.service"
TS_UP_UNIT = "dahlia-tailscale-up.service"
TS_LOGIN_TIMEOUT = "600s"
FORWARD_CONF = "60-dahlia-forward.conf"

WG_DEFAULTS = {"enabled": False, "port": DEFAULT_PORT, "endpoint": "", "subnet": DEFAULT_SUBNET,
               "route": "lan", "dns": "", "peers": []}


# --- WireGuard settings and files ------------------------------------------------


def config_file(ctx: Context) -> Path:
    return ctx.etc / "dahlia" / "wireguard.json"


def key_file(ctx: Context) -> Path:
    return ctx.etc / "wireguard" / f"{IFACE}.key"


def conf_file(ctx: Context) -> Path:
    return ctx.etc / "wireguard" / f"{IFACE}.conf"


def load(ctx: Context) -> dict:
    data = read_json(config_file(ctx), WG_DEFAULTS)
    out = dict(WG_DEFAULTS)
    for k, default in WG_DEFAULTS.items():
        if type(data.get(k)) is type(default):
            out[k] = data[k]
    out["peers"] = [p for p in out["peers"] if isinstance(p, dict)
                    and isinstance(p.get("name"), str) and isinstance(p.get("address"), str)
                    and isinstance(p.get("public_key"), str) and isinstance(p.get("psk"), str)]
    return out


def save(ctx: Context, cfg: dict) -> None:
    write_file(config_file(ctx), json.dumps(cfg, indent=2) + "\n", mode=0o600)


def genkey(ctx: Context) -> tuple[str, str]:
    """(private, public) from wg."""
    private = ctx.runner.run(["wg", "genkey"]).stdout.strip()
    public = ctx.runner.run(["wg", "pubkey"], input=private + "\n").stdout.strip()
    if not KEY_RE.fullmatch(private) or not KEY_RE.fullmatch(public):
        raise AgentError(FAILED, "wg returned an invalid key")
    return private, public


def server_key(ctx: Context) -> tuple[str, str]:
    """The server's key pair, made on first use."""
    f = key_file(ctx)
    try:
        private = f.read_text().strip()
    except FileNotFoundError:
        private = ""
    if not KEY_RE.fullmatch(private):
        private, _ = genkey(ctx)
        f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_file(f, private + "\n", mode=0o600)
    public = ctx.runner.run(["wg", "pubkey"], input=private + "\n").stdout.strip()
    return private, public


def server_address(subnet: str) -> str:
    net = ipaddress.ip_network(subnet)
    return f"{net.network_address + 1}/{net.prefixlen}"


def next_address(cfg: dict) -> str:
    net = ipaddress.ip_network(cfg["subnet"])
    used = {p["address"].split("/")[0] for p in cfg["peers"]}
    for host in list(net.hosts())[1:]:
        if str(host) not in used:
            return f"{host}/32"
    raise AgentError(FAILED, f"no free address left in {cfg['subnet']}")


def render_conf(cfg: dict, private: str) -> str:
    lines = [
        "# Generated by Dahlia (dashboard: Settings > Remote access). Changes are overwritten.",
        "[Interface]",
        f"Address = {server_address(cfg['subnet'])}",
        f"ListenPort = {cfg['port']}",
        f"PrivateKey = {private}",
        f"PostUp = {FORWARD_SCRIPT} up %i",
        f"PostDown = {FORWARD_SCRIPT} down %i",
    ]
    for p in cfg["peers"]:
        lines += ["", f"# {p['name']}", "[Peer]", f"PublicKey = {p['public_key']}",
                  f"PresharedKey = {p['psk']}", f"AllowedIPs = {p['address']}"]
    return "\n".join(lines) + "\n"


def render_sync(cfg: dict, private: str) -> str:
    """What `wg syncconf` takes: the conf without the wg-quick lines."""
    keep = ("Address", "PostUp", "PostDown", "#")
    return "".join(line + "\n" for line in render_conf(cfg, private).splitlines()
                   if not line.startswith(keep))


def client_allowed(ctx: Context, cfg: dict) -> str:
    if cfg["route"] == "all":
        return "0.0.0.0/0, ::/0"
    nets = [cfg["subnet"]]
    for n in local_subnets(lambda cmd: ctx.runner.run(cmd, check=False).stdout):
        if ":" not in n and n not in nets:
            nets.append(n)
    return ", ".join(nets)


def render_client(ctx: Context, cfg: dict, peer: dict, private: str, server_public: str) -> str:
    host = cfg["endpoint"]
    lines = ["[Interface]", f"PrivateKey = {private}", f"Address = {peer['address']}"]
    if cfg["dns"]:
        lines.append(f"DNS = {cfg['dns']}")
    lines += ["", "[Peer]", f"PublicKey = {server_public}", f"PresharedKey = {peer['psk']}",
              f"Endpoint = {host}:{cfg['port']}", f"AllowedIPs = {client_allowed(ctx, cfg)}",
              f"PersistentKeepalive = {KEEPALIVE}"]
    return "\n".join(lines) + "\n"


def enable_forwarding(ctx: Context) -> None:
    write_file(ctx.etc / "sysctl.d" / FORWARD_CONF,
               "# Written by Dahlia: WireGuard and Tailscale clients reach the LAN.\n"
               "net.ipv4.ip_forward = 1\n")
    ctx.runner.run(["sysctl", "-q", "-w", "net.ipv4.ip_forward=1"], check=False)


def apply(ctx: Context, cfg: dict, restart: bool) -> None:
    """Write the conf, load the firewall, start, reload or stop the interface."""
    private, _ = server_key(ctx)
    write_file(conf_file(ctx), render_conf(cfg, private), mode=0o600)
    try:
        firewall_of(ctx).apply()
    except ValueError as e:  # FirewallError
        raise AgentError(FAILED, str(e)) from None
    if not cfg["enabled"]:
        ctx.runner.run(["systemctl", "disable", "--now", WG_UNIT], check=False)
        return
    enable_forwarding(ctx)
    ctx.runner.run(["systemctl", "enable", WG_UNIT])
    if restart or not is_active(unit_state(ctx, WG_UNIT)):
        ctx.runner.run(["systemctl", "restart", WG_UNIT])
        return
    # Peers changed: keep the tunnels of the others up.
    sync = ctx.run_dir / f"{IFACE}.sync"
    write_file(sync, render_sync(cfg, private), mode=0o600)
    try:
        ctx.runner.run(["wg", "syncconf", IFACE, str(sync)])
    finally:
        sync.unlink(missing_ok=True)


def peer_stats(ctx: Context) -> dict[str, dict]:
    """public key -> {"handshake", "rx", "tx", "endpoint"} from `wg show dump`."""
    out = ctx.runner.run(["wg", "show", IFACE, "dump"], check=False).stdout
    stats = {}
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 8:
            continue
        try:
            stats[f[0]] = {"endpoint": None if f[2] == "(none)" else f[2],
                           "handshake": int(f[4]) or None, "rx": int(f[5]), "tx": int(f[6])}
        except ValueError:
            continue
    return stats


def wireguard_status(ctx: Context) -> dict:
    cfg = load(ctx)
    stats = peer_stats(ctx) if cfg["enabled"] else {}
    public = None
    if key_file(ctx).exists():
        public = server_key(ctx)[1]
    return {
        **{k: cfg[k] for k in ("enabled", "port", "endpoint", "subnet", "route", "dns")},
        "public_key": public,
        "running": is_active(unit_state(ctx, WG_UNIT)),
        "peers": [{"name": p["name"], "address": p["address"], "public_key": p["public_key"],
                   "created": p.get("created"), **stats.get(p["public_key"], {})}
                  for p in cfg["peers"]],
    }


def check_subnet(ctx: Context, subnet: str, current: str) -> str:
    """A private subnet that does not overlap a network of the server (other
    than the tunnel itself, which shows up there while it runs)."""
    try:
        net = ipaddress.ip_network(subnet)
    except ValueError:
        raise AgentError(INVALID_PARAMS, f"'{subnet}' is not a network") from None
    if not net.is_private:
        raise AgentError(INVALID_PARAMS, "the tunnel subnet must be a private network")
    own = ipaddress.ip_network(current)
    for n in local_subnets(lambda cmd: ctx.runner.run(cmd, check=False).stdout):
        other = ipaddress.ip_network(n)
        if other.version == 4 and other != own and net.overlaps(other):
            raise AgentError(INVALID_PARAMS, f"{subnet} overlaps the local network {n}")
    return str(net)


# --- WireGuard actions --------------------------------------------------------------


@action("vpn.status", mutating=False)
def vpn_status(ctx: Context):
    """WireGuard server with peers and Tailscale state."""
    return {"wireguard": wireguard_status(ctx), "tailscale": tailscale_status(ctx)}


@action(
    "vpn.wireguard_set",
    mutating=True,
    params={
        "enabled": Param(bool),
        "port": Param(int, minimum=1024, maximum=65535),
        "endpoint": Param(str, pattern=re.compile(rf"^$|{ENDPOINT_RE.pattern}")),
        "route": Param(str, choices=ROUTES),
        "dns": Param(str, pattern=DNS_RE, required=False, default=""),
        "subnet": Param(str, pattern=SUBNET_RE, required=False, default=DEFAULT_SUBNET),
    },
)
def vpn_wireguard_set(ctx: Context, enabled: bool, port: int, endpoint: str, route: str,
                      dns: str, subnet: str):
    """WireGuard on/off, UDP port, address clients connect to, routing, DNS for clients."""
    from .apps import RESERVED_PUBLIC

    if port in RESERVED_PUBLIC:
        raise AgentError(INVALID_PARAMS, f"port {port} is used by Dahlia")
    if dns:
        try:
            ipaddress.IPv4Address(dns)
        except ValueError:
            raise AgentError(INVALID_PARAMS, f"'{dns}' is not an IPv4 address") from None
    cfg = load(ctx)
    subnet = check_subnet(ctx, subnet, cfg["subnet"])
    if cfg["peers"] and subnet != cfg["subnet"]:
        raise AgentError(FAILED, "remove all peers before changing the tunnel subnet")
    restart = (port, subnet) != (cfg["port"], cfg["subnet"]) or not cfg["enabled"]
    cfg.update(enabled=enabled, port=port, endpoint=endpoint, route=route, dns=dns,
               subnet=subnet)
    save(ctx, cfg)
    apply(ctx, cfg, restart)
    return wireguard_status(ctx)


@action(
    "vpn.wireguard_add_peer",
    mutating=True,
    params={"name": Param(str, pattern=PEER_NAME_RE)},
)
def vpn_wireguard_add_peer(ctx: Context, name: str):
    """New client: returns its configuration and QR code once; the server keeps only its
    public key."""
    cfg = load(ctx)
    if not cfg["enabled"]:
        raise AgentError(FAILED, "WireGuard is off")
    if not cfg["endpoint"]:
        raise AgentError(FAILED, "set the address clients connect to first")
    if any(p["name"] == name for p in cfg["peers"]):
        raise AgentError(FAILED, f"{name} exists already")
    if len(cfg["peers"]) >= MAX_PEERS:
        raise AgentError(FAILED, f"at most {MAX_PEERS} peers")
    private, public = genkey(ctx)
    psk = ctx.runner.run(["wg", "genpsk"]).stdout.strip()
    if not KEY_RE.fullmatch(psk):
        raise AgentError(FAILED, "wg returned an invalid key")
    peer = {"name": name, "address": next_address(cfg), "public_key": public, "psk": psk,
            "created": datetime.datetime.fromtimestamp(ctx.clock(), datetime.UTC)
            .isoformat(timespec="seconds")}
    cfg["peers"].append(peer)
    save(ctx, cfg)
    apply(ctx, cfg, restart=False)
    text = render_client(ctx, cfg, peer, private, server_key(ctx)[1])
    qr = ctx.runner.run(["qrencode", "-t", "SVG", "-o", "-"], input=text, check=False)
    return {"name": name, "address": peer["address"], "config": text,
            "qr_svg": qr.stdout if qr.returncode == 0 and "<svg" in qr.stdout else None}


@action(
    "vpn.wireguard_remove_peer",
    mutating=True,
    params={"name": Param(str, pattern=PEER_NAME_RE)},
)
def vpn_wireguard_remove_peer(ctx: Context, name: str):
    """Remove a client; its tunnel ends at once."""
    cfg = load(ctx)
    peers = [p for p in cfg["peers"] if p["name"] != name]
    if len(peers) == len(cfg["peers"]):
        raise AgentError(FAILED, f"{name} does not exist")
    cfg["peers"] = peers
    save(ctx, cfg)
    apply(ctx, cfg, restart=False)
    return {"name": name}


# --- Tailscale --------------------------------------------------------------------


def ts_config_file(ctx: Context) -> Path:
    return ctx.etc / "dahlia" / "tailscale.json"


def tailscale_status(ctx: Context) -> dict:
    opts = read_json(ts_config_file(ctx), {"routes": False, "exit_node": False})
    out = {
        "installed": TAILSCALE.exists(),
        "install_job": unit_state(ctx, TS_INSTALL_UNIT),
        "login_job": unit_state(ctx, TS_UP_UNIT),
        "routes": opts.get("routes") is True,
        "exit_node": opts.get("exit_node") is True,
        "state": None,
        "auth_url": None,
        "hostname": None,
        "dns_name": None,
        "addresses": [],
        "tailnet": None,
    }
    if not out["installed"]:
        return out
    res = ctx.runner.run([str(TAILSCALE), "status", "--json"], check=False)
    try:
        data = json.loads(res.stdout or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    me = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    tailnet = data.get("CurrentTailnet") if isinstance(data.get("CurrentTailnet"), dict) else {}
    url = data.get("AuthURL")
    out.update(
        state=data.get("BackendState") if isinstance(data.get("BackendState"), str) else None,
        auth_url=url if isinstance(url, str) and url.startswith("https://") else None,
        hostname=me.get("HostName"),
        dns_name=(me.get("DNSName") or "").rstrip(".") or None,
        addresses=[a for a in me.get("TailscaleIPs") or [] if isinstance(a, str)],
        tailnet=tailnet.get("Name"),
    )
    return out


@action("vpn.tailscale_install", mutating=True)
def vpn_tailscale_install(ctx: Context):
    """Add Tailscale's apt repository and install tailscale, in the background."""
    if TAILSCALE.exists():
        raise AgentError(FAILED, "Tailscale is installed already")
    if is_active(unit_state(ctx, TS_INSTALL_UNIT)):
        raise AgentError(BUSY, "Tailscale is being installed")
    ctx.runner.run(["systemctl", "reset-failed", TS_INSTALL_UNIT], check=False)
    ctx.runner.run(["systemd-run", "--unit", TS_INSTALL_UNIT, "--no-block",
                    "--property", "Type=oneshot",
                    "--property", f"OnFailure=dahlia-notify-failure@{TS_INSTALL_UNIT}.service",
                    "--description", "Dahlia: install Tailscale", TS_INSTALL])
    return {"started": True}


@action(
    "vpn.tailscale_up",
    mutating=True,
    params={"routes": Param(bool), "exit_node": Param(bool)},
)
def vpn_tailscale_up(ctx: Context, routes: bool, exit_node: bool):
    """Join the tailnet (the login link shows up in vpn.status) or change the options."""
    if not TAILSCALE.exists():
        raise AgentError(FAILED, "Tailscale is not installed")
    if is_active(unit_state(ctx, TS_UP_UNIT)):
        raise AgentError(BUSY, "Tailscale is waiting for the login")
    write_file(ts_config_file(ctx), json.dumps({"routes": routes, "exit_node": exit_node}) + "\n")
    cmd = [str(TAILSCALE), "up", "--reset", f"--timeout={TS_LOGIN_TIMEOUT}"]
    if routes:
        nets = [n for n in local_subnets(lambda c: ctx.runner.run(c, check=False).stdout)
                if ":" not in n]
        if not nets:
            raise AgentError(FAILED, "no local IPv4 network found to offer")
        cmd.append(f"--advertise-routes={','.join(nets)}")
    if exit_node:
        cmd.append("--advertise-exit-node")
    if routes or exit_node:
        enable_forwarding(ctx)
    ctx.runner.run(["systemctl", "reset-failed", TS_UP_UNIT], check=False)
    ctx.runner.run(["systemd-run", "--unit", TS_UP_UNIT, "--no-block",
                    "--property", "Type=oneshot",
                    "--description", "Dahlia: Tailscale login", *cmd])
    return {"started": True}


@action("vpn.tailscale_logout", mutating=True)
def vpn_tailscale_logout(ctx: Context):
    """Leave the tailnet; the server has to log in again to rejoin."""
    if not TAILSCALE.exists():
        raise AgentError(FAILED, "Tailscale is not installed")
    ctx.runner.run(["systemctl", "stop", TS_UP_UNIT], check=False)
    ctx.runner.run([str(TAILSCALE), "logout"])
    return {"logged_out": True}
