"""VMs and system containers with Incus (vm.*, CLI machines).

Incus comes from Debian (incus, qemu-system-x86, ovmf, swtpm) and is
installed on demand: vm.install starts incus-install.service
(/usr/lib/dahlia/incus-install.sh). The script creates the storage pool
"default" (dir driver, on the system SSD under /var/lib/incus or on the
cache under /mnt/cache/incus), the NAT bridge incusbr0, and adds the
dashboard account to incus-admin, so `incus exec <name> -- bash` works in
the terminal. incus-forward.service lets incusbr0 traffic pass Docker's
FORWARD policy.

Instances come from the images: remote (a fixed list) or, for VMs, from an
ISO file on the server. Networks: "nat" = incusbr0 (reachable from the
server), "lan" = macvlan on the interface of the default route (an own
address from the router; the server itself cannot reach it, a macvlan limit).

Graphical console: every VM created here gets raw.qemu "-vnc 127.0.0.1:D"
with its own display D (user.dahlia.vnc); the dashboard relays it as a
WebSocket. The port stays on 127.0.0.1, like the ports of Docker apps.
Creating runs in the background (vm-create-<name>.service) because images
are downloaded; the request waits in /run/dahlia/vm-create/<name>.json.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from .actions import ROOTS, Context, Param, action, is_active, unit_state
from .protocol import BUSY, FAILED, INVALID_PARAMS, AgentError

INCUS = Path("/usr/bin/incus")
CACHE = Path("/mnt/cache")
INSTALL = "/usr/lib/dahlia/incus-install.sh"
INSTALL_UNIT = "incus-install.service"
POOL = "default"
BRIDGE = "incusbr0"
NAME_RE = re.compile(r"^[a-z]([a-z0-9-]{0,30}[a-z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# Images of the images: remote that have container and VM variants.
IMAGES = ("debian/13", "debian/12", "ubuntu/24.04", "alpine/3.22", "archlinux",
          "rockylinux/9", "almalinux/9")
OPS = ("start", "stop", "restart", "force-stop")
VNC_DISPLAYS = range(50, 100)  # ports 5950-5999
VNC_RE = re.compile(r"-vnc 127\.0\.0\.1:(\d{1,2})\b")
ISO_VOLUME = "{name}-install"
MAX_CPUS = 64
MAX_MEMORY = 1024 * 1024  # MiB
MAX_DISK = 16384  # GiB


def incus(ctx: Context, *args: str, timeout: int = 120, check: bool = True):
    return ctx.runner.run([str(INCUS), *args], timeout=timeout, check=check)


def create_dir(ctx: Context) -> Path:
    return ctx.run_dir / "vm-create"


def create_unit(name: str) -> str:
    return f"vm-create-{name}.service"


def host(ctx: Context) -> dict:
    """Processors and memory (MiB) of the server, the limits for new instances."""
    try:
        cpus = sum(1 for ln in (ctx.proc / "cpuinfo").read_text().splitlines()
                   if ln.startswith("processor"))
    except OSError:
        cpus = 1
    memory = 0
    try:
        for ln in (ctx.proc / "meminfo").read_text().splitlines():
            if ln.startswith("MemTotal:"):
                memory = int(ln.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return {"cpus": max(cpus, 1), "memory": memory}


def lan_parent(ctx: Context) -> str | None:
    """Interface of the default route, the parent of macvlan NICs."""
    proc = ctx.runner.run(["ip", "-j", "route", "show", "default"], check=False)
    try:
        routes = json.loads(proc.stdout or "[]")
    except ValueError:
        return None
    for r in routes:
        dev = r.get("dev") if isinstance(r, dict) else None
        if isinstance(dev, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", dev):
            return dev
    return None


def memory_mib(value: str) -> int | None:
    m = re.fullmatch(r"(\d+)\s*(MiB|GiB|MB|GB)?", value.strip())
    if not m:
        return None
    n = int(m.group(1))
    factor = {None: 1, "MiB": 1024**2, "GiB": 1024**3, "MB": 1000**2, "GB": 1000**3}[m.group(2)]
    return n * factor // 1024**2


def vnc_display(config: dict) -> int | None:
    value = config.get("user.dahlia.vnc")
    if isinstance(value, str) and value.isdigit() and int(value) in VNC_DISPLAYS:
        return int(value)
    return None


def addresses(state: dict) -> list[str]:
    out = []
    for nic, data in ((state or {}).get("network") or {}).items():
        if nic == "lo" or not isinstance(data, dict):
            continue
        for a in data.get("addresses") or []:
            if a.get("family") == "inet" and a.get("scope") == "global":
                out.append(a.get("address", ""))
    return [a for a in out if a]


def instance(raw: dict) -> dict:
    config = raw.get("expanded_config") or raw.get("config") or {}
    devices = raw.get("expanded_devices") or raw.get("devices") or {}
    state = raw.get("state") or {}
    cpus = config.get("limits.cpu", "")
    nic = devices.get("eth0") or {}
    vm = raw.get("type") == "virtual-machine"
    return {
        "name": raw.get("name", ""),
        "type": "vm" if vm else "container",
        "status": str(raw.get("status", "")).lower(),
        "cpus": int(cpus) if str(cpus).isdigit() else None,
        "memory": memory_mib(config["limits.memory"]) if config.get("limits.memory") else None,
        "autostart": config.get("boot.autostart") == "true",
        "image": config.get("image.description", ""),
        "addresses": addresses(state),
        "network": "lan" if nic.get("nictype") == "macvlan" else "nat",
        "console": vm and vnc_display(raw.get("config") or {}) is not None,
        "iso": "install" in (raw.get("devices") or {}),
        "memory_used": ((state.get("memory") or {}).get("usage")) or 0,
        "created": raw.get("created_at", ""),
    }


def raw_list(ctx: Context) -> list[dict]:
    proc = incus(ctx, "list", "--format", "json", timeout=60, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise AgentError(FAILED, f"incus: {detail[-1] if detail else 'not running'}")
    try:
        data = json.loads(proc.stdout or "[]")
    except ValueError:
        raise AgentError(FAILED, "incus: unreadable list") from None
    return [i for i in data if isinstance(i, dict)]


def find(ctx: Context, name: str) -> dict:
    for raw in raw_list(ctx):
        if raw.get("name") == name:
            return raw
    raise AgentError(FAILED, f"{name} does not exist")


def creating(ctx: Context) -> list[dict]:
    out = []
    d = create_dir(ctx)
    for spec in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            data = json.loads(spec.read_text())
        except (OSError, ValueError):
            continue
        out.append({"name": spec.stem, "type": data.get("kind", ""),
                    "job": unit_state(ctx, create_unit(spec.stem))})
    return out


def require_installed() -> None:
    if not INCUS.exists():
        raise AgentError(FAILED, "Incus is not installed")


@action("vm.status", mutating=False)
def vm_status(ctx: Context):
    """Incus state, the instances and what new ones may use."""
    out = {
        "installed": INCUS.exists(),
        "install_job": unit_state(ctx, INSTALL_UNIT),
        "host": host(ctx),
        "images": list(IMAGES),
        "cache": CACHE.is_mount(),
        "lan": lan_parent(ctx) is not None,
        "instances": [],
        "creating": [],
        "error": None,
    }
    if not out["installed"]:
        return out
    try:
        out["instances"] = [instance(r) for r in raw_list(ctx)]
    except AgentError as e:
        out["error"] = e.message
    out["creating"] = creating(ctx)
    return out


@action(
    "vm.install",
    mutating=True,
    params={
        "location": Param(str, choices=("system", "cache")),
        "user": Param(str, pattern=USER_RE),
    },
)
def vm_install(ctx: Context, location: str, user: str):
    """Install Incus and create its storage pool and bridge, in the background."""
    if INCUS.exists():
        raise AgentError(FAILED, "Incus is installed already")
    if is_active(unit_state(ctx, INSTALL_UNIT)):
        raise AgentError(BUSY, "Incus is being installed")
    if location == "cache" and not CACHE.is_mount():
        raise AgentError(FAILED, "there is no cache")
    ctx.runner.run(["systemctl", "reset-failed", INSTALL_UNIT], check=False)
    ctx.runner.run(["systemd-run", "--unit", INSTALL_UNIT, "--no-block",
                    "--property", "Type=oneshot",
                    "--property", f"OnFailure=notify-failure@{INSTALL_UNIT}.service",
                    "--description", "Dahlia: install Incus", INSTALL, location, user])
    return {"started": True}


@action(
    "vm.create",
    mutating=True,
    params={
        "name": Param(str, pattern=NAME_RE),
        "kind": Param(str, choices=("container", "vm")),
        "source": Param(str, choices=("image", "iso")),
        "image": Param(str, required=False, default="debian/13", choices=IMAGES),
        "iso_root": Param(str, required=False, default="", choices=("", *ROOTS)),
        "iso_path": Param(str, required=False, default=""),
        "cpus": Param(int, minimum=1, maximum=MAX_CPUS),
        "memory": Param(int, minimum=128, maximum=MAX_MEMORY),
        "disk": Param(int, minimum=1, maximum=MAX_DISK),
        "network": Param(str, choices=("nat", "lan")),
        "autostart": Param(bool, required=False, default=False),
        "secureboot": Param(bool, required=False, default=True),
        "windows": Param(bool, required=False, default=False),
        "start": Param(bool, required=False, default=True),
    },
)
def vm_create(ctx: Context, name: str, kind: str, source: str, image: str, iso_root: str,
              iso_path: str, cpus: int, memory: int, disk: int, network: str, autostart: bool,
              secureboot: bool, windows: bool, start: bool):
    """Create a container or VM in the background (vm-create-<name>.service)."""
    require_installed()
    h = host(ctx)
    if cpus > h["cpus"]:
        raise AgentError(INVALID_PARAMS, f"'cpus' must be at most {h['cpus']}")
    if h["memory"] and memory > h["memory"]:
        raise AgentError(INVALID_PARAMS, f"'memory' must be at most {h['memory']}")
    if source == "iso" and kind != "vm":
        raise AgentError(INVALID_PARAMS, "only VMs start from an ISO file")
    if windows and kind != "vm":
        raise AgentError(INVALID_PARAMS, "Windows runs only as a VM")
    spec = {"name": name, "kind": kind, "source": source, "cpus": cpus, "memory": memory,
            "disk": disk, "network": network, "autostart": autostart, "secureboot": secureboot,
            "windows": windows, "start": start}
    raws = raw_list(ctx)
    if any(r.get("name") == name for r in raws) or (create_dir(ctx) / f"{name}.json").exists():
        raise AgentError(FAILED, f"{name} exists already")
    if source == "iso":
        from .files import checked_entry

        if not iso_root or not iso_path.lower().endswith(".iso"):
            raise AgentError(INVALID_PARAMS, "choose an .iso file")
        _, path = checked_entry(ctx, iso_root, iso_path)
        if not path.is_file():
            raise AgentError(FAILED, f"{iso_path} is not a file")
        spec["iso"] = str(path)
    else:
        spec["image"] = image
    if network == "lan":
        parent = lan_parent(ctx)
        if parent is None:
            raise AgentError(FAILED, "no default route for a LAN network")
        spec["parent"] = parent
    if kind == "vm":
        used = {vnc_display(r.get("config") or {}) for r in raws}
        used |= {s.get("vnc") for s in pending_specs(ctx)}
        free = [d for d in VNC_DISPLAYS if d not in used]
        if not free:
            raise AgentError(FAILED, "no console display left")
        spec["vnc"] = free[0]
    d = create_dir(ctx)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(spec))
    unit = create_unit(name)
    ctx.runner.run(["systemctl", "reset-failed", unit], check=False)
    ctx.runner.run(["systemd-run", "--unit", unit, "--no-block",
                    "--property", "Type=oneshot",
                    "--property", f"OnFailure=notify-failure@{unit}.service",
                    "--description", f"Dahlia: create {name}",
                    "/usr/sbin/machines", "create", name])
    return {"started": True, "name": name}


def pending_specs(ctx: Context) -> list[dict]:
    d = create_dir(ctx)
    out = []
    for p in d.glob("*.json") if d.is_dir() else []:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


@action(
    "vm.op",
    mutating=True,
    params={"name": Param(str, pattern=NAME_RE), "op": Param(str, choices=OPS)},
)
def vm_op(ctx: Context, name: str, op: str):
    """Start, stop (cleanly or forced) or restart an instance."""
    require_installed()
    find(ctx, name)
    if op == "force-stop":
        incus(ctx, "stop", "--force", "--", name)
    else:
        incus(ctx, op, "--", name, timeout=180)
    return {"name": name, "op": op}


@action(
    "vm.set",
    mutating=True,
    params={
        "name": Param(str, pattern=NAME_RE),
        "cpus": Param(int, minimum=1, maximum=MAX_CPUS),
        "memory": Param(int, minimum=128, maximum=MAX_MEMORY),
        "autostart": Param(bool),
    },
)
def vm_set(ctx: Context, name: str, cpus: int, memory: int, autostart: bool):
    """Processors, memory and start with the server; VMs take new limits on the next start."""
    require_installed()
    find(ctx, name)
    h = host(ctx)
    if cpus > h["cpus"]:
        raise AgentError(INVALID_PARAMS, f"'cpus' must be at most {h['cpus']}")
    incus(ctx, "config", "set", name, f"limits.cpu={cpus}", f"limits.memory={memory}MiB",
          f"boot.autostart={'true' if autostart else 'false'}")
    return {"name": name}


@action("vm.eject", mutating=True, params={"name": Param(str, pattern=NAME_RE)})
def vm_eject(ctx: Context, name: str):
    """Remove the installation ISO of a VM (stopped)."""
    require_installed()
    raw = find(ctx, name)
    if "install" not in (raw.get("devices") or {}):
        raise AgentError(FAILED, f"{name} has no ISO")
    if str(raw.get("status", "")).lower() != "stopped":
        raise AgentError(FAILED, f"stop {name} first")
    incus(ctx, "config", "device", "remove", name, "install")
    incus(ctx, "storage", "volume", "delete", POOL, ISO_VOLUME.format(name=name), check=False)
    return {"name": name}


@action("vm.delete", mutating=True, params={"name": Param(str, pattern=NAME_RE)})
def vm_delete(ctx: Context, name: str):
    """Delete a stopped instance with its disk."""
    require_installed()
    raw = find(ctx, name)
    if str(raw.get("status", "")).lower() != "stopped":
        raise AgentError(FAILED, f"stop {name} first")
    incus(ctx, "delete", "--", name, timeout=300)
    incus(ctx, "storage", "volume", "delete", POOL, ISO_VOLUME.format(name=name), check=False)
    return {"name": name}


@action("vm.console", mutating=False, params={"name": Param(str, pattern=NAME_RE)})
def vm_console(ctx: Context, name: str):
    """Port of the graphical console of a running VM (on 127.0.0.1)."""
    require_installed()
    raw = find(ctx, name)
    display = vnc_display(raw.get("config") or {})
    if raw.get("type") != "virtual-machine" or display is None:
        raise AgentError(FAILED, f"{name} has no console")
    if str(raw.get("status", "")).lower() != "running":
        raise AgentError(FAILED, f"{name} is not running")
    return {"name": name, "port": 5900 + display}


# --- create (runs as vm-create-<name>.service) ------------------------------------


def create_commands(spec: dict) -> list[list[str]]:
    """incus commands for a request of vm.create, in order."""
    name = spec["name"]
    vm = spec["kind"] == "vm"
    cfg = [f"limits.cpu={spec['cpus']}", f"limits.memory={spec['memory']}MiB",
           f"boot.autostart={'true' if spec['autostart'] else 'false'}"]
    if vm:
        cfg += [f"raw.qemu=-vnc 127.0.0.1:{spec['vnc']}", f"user.dahlia.vnc={spec['vnc']}"]
        if not spec["secureboot"]:
            cfg.append("security.secureboot=false")
        if spec["windows"]:
            cfg.append("image.os=Windows")
    init = ["init"]
    init += ["--empty"] if spec["source"] == "iso" else [f"images:{spec['image']}"]
    init.append(name)
    if vm:
        init.append("--vm")
    for c in cfg:
        init += ["-c", c]
    if vm:
        root = f"root,size={spec['disk']}GiB"
        if spec["windows"]:
            root += ",io.bus=nvme"
        init += ["-d", root]
    cmds = [init]
    if spec["network"] == "lan":
        cmds.append(["config", "device", "add", name, "eth0", "nic", "nictype=macvlan",
                     f"parent={spec['parent']}", "name=eth0"])
    if spec["windows"]:
        cmds.append(["config", "device", "add", name, "vtpm", "tpm"])
    if spec["source"] == "iso":
        volume = ISO_VOLUME.format(name=name)
        cmds += [
            ["storage", "volume", "import", POOL, spec["iso"], volume, "--type=iso"],
            ["config", "device", "add", name, "install", "disk", f"pool={POOL}",
             f"source={volume}", "boot.priority=10"],
        ]
    if spec["start"]:
        cmds.append(["start", name])
    return cmds


def create(ctx: Context, name: str) -> None:
    path = create_dir(ctx) / f"{name}.json"
    spec = json.loads(path.read_text())
    made = False
    try:
        for i, cmd in enumerate(create_commands(spec)):
            print(f"machines: incus {' '.join(cmd)}", flush=True)
            incus(ctx, *cmd, timeout=3600)
            made = made or i == 0
    except AgentError:
        if made:
            incus(ctx, "delete", "--force", "--", name, check=False)
            incus(ctx, "storage", "volume", "delete", POOL, ISO_VOLUME.format(name=name),
                  check=False)
        raise
    finally:
        path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="machines", description="Dahlia VMs and containers (Incus).")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="create a requested instance (vm-create-<name>.service)")
    c.add_argument("name")
    sub.add_parser("list", help="print the instances as JSON")
    args = p.parse_args(argv)
    ctx = Context()
    if args.command == "list":
        print(json.dumps(vm_status(ctx), indent=2))
        return 0
    if not NAME_RE.fullmatch(args.name):
        print("machines: invalid name", file=sys.stderr)
        return 2
    try:
        create(ctx, args.name)
    except (AgentError, OSError, ValueError, KeyError) as e:
        print(f"machines: {getattr(e, 'message', e)}", file=sys.stderr)
        return 1
    print(f"machines: {args.name} created")
    return 0
