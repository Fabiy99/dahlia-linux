"""The fixed action list.

Every action declares its parameters. Unknown parameters, missing ones and
values outside the allowed form are rejected before any command runs.
Commands are argument lists, never shell strings.
"""

import json
import os
import re
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .protocol import BUSY, FAILED, INVALID_PARAMS, UNKNOWN_ACTION, AgentError

# --- running commands ---------------------------------------------------------


class Runner:
    """Runs system commands. Tests replace it with a fake."""

    def run(
        self, cmd: list[str], *, timeout: int = 120, check: bool = True, env: dict | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess:
        # input is for secrets (passwords); they never go on the command line.
        stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                **stdin,
            )
        except subprocess.TimeoutExpired:
            raise AgentError(FAILED, f"{cmd[0]} did not finish within {timeout}s") from None
        except FileNotFoundError:
            raise AgentError(FAILED, f"{cmd[0]} is not installed") from None
        if check and proc.returncode != 0:
            raise AgentError(FAILED, command_error(cmd, proc))
        return proc


def command_error(cmd: list[str], proc: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (proc.stderr or proc.stdout or "").splitlines() if ln.strip()]
    detail = lines[-1].strip() if lines else f"exit code {proc.returncode}"
    return f"{os.path.basename(cmd[0])}: {detail}"


@dataclass
class Context:
    runner: Runner = field(default_factory=Runner)
    proc: Path = Path("/proc")
    release_file: Path = Path("/etc/dahlia/release")
    hosts_file: Path = Path("/etc/hosts")
    sys: Path = Path("/sys")
    etc: Path = Path("/etc")
    run_dir: Path = Path("/run/dahlia")
    pool: Path = Path("/mnt/user")
    system: Path = Path("/srv")
    var: Path = Path("/var/lib/dahlia")
    log_dir: Path = Path("/var/log")
    dashboard_run: Path = Path("/run/dahlia-dashboard")
    reboot_flag: Path = Path("/run/reboot-required")
    hostname: Callable[[], str] = socket.gethostname
    clock: Callable[[], float] = time.time
    # Notification senders by channel; tests replace them, None = the real ones.
    senders: dict | None = None


# --- parameter checks ---------------------------------------------------------

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# ext4/XFS/Btrfs UUIDs and the short NTFS/FAT serials lsblk shows.
UUID_RE = re.compile(r"^[0-9A-Fa-f]{4}([0-9A-Fa-f-]{4,32}[0-9A-Fa-f])?$")

DISK_ROLES = ("data", "parity", "cache", "external")
FORMAT_FILESYSTEMS = ("xfs", "ext4", "btrfs")
IMPORT_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
IMPORT_UNIT = "dahlia-import"
# Whole disks only; dahlia-disk checks that the disk is blank or imported.
DEVICE_RE = re.compile(r"^/dev/((sd|vd|xvd)[a-z]{1,2}|nvme[0-9]{1,2}n[0-9]{1,2}|loop[0-9]{1,3})$")
STORAGE_OPTIONS = (
    "mover_min_age_minutes",
    "snapraid_delete_threshold",
    "scrub_percent",
    "scrub_older_than_days",
)
JOBS = {
    "mover": "dahlia-mover.service",
    "snapraid-sync": "dahlia-snapraid-sync.service",
    "snapraid-scrub": "dahlia-snapraid-scrub.service",
    "update": "dahlia-update.service",
    "backup": "dahlia-backup.service",
}
# Jobs job.start may start. Updates go through updates.apply.
STARTABLE_JOBS = ("mover", "snapraid-sync", "snapraid-scrub", "backup")


@dataclass(frozen=True)
class Param:
    kind: type
    required: bool = True
    default: object = None
    pattern: re.Pattern | None = None
    choices: tuple | None = None
    minimum: int | None = None
    maximum: int | None = None

    def check(self, name: str, value):
        # bool is a subclass of int; keep them apart.
        if type(value) is not self.kind:
            raise AgentError(INVALID_PARAMS, f"'{name}' must be {self.kind.__name__}")
        if self.pattern is not None and not self.pattern.fullmatch(value):
            raise AgentError(INVALID_PARAMS, f"'{name}' has an invalid format")
        if self.choices is not None and value not in self.choices:
            allowed = ", ".join(map(str, self.choices))
            raise AgentError(INVALID_PARAMS, f"'{name}' must be one of: {allowed}")
        if self.minimum is not None and value < self.minimum:
            raise AgentError(INVALID_PARAMS, f"'{name}' must be at least {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise AgentError(INVALID_PARAMS, f"'{name}' must be at most {self.maximum}")
        return value


@dataclass(frozen=True)
class Action:
    name: str
    func: Callable
    params: dict[str, Param]
    mutating: bool
    description: str


ACTIONS: dict[str, Action] = {}


def action(name: str, *, mutating: bool, params: dict[str, Param] | None = None):
    def register(func):
        doc = (func.__doc__ or "").strip().splitlines()
        ACTIONS[name] = Action(name, func, params or {}, mutating, doc[0] if doc else "")
        return func

    return register


def check_params(act: Action, params: dict) -> dict:
    unknown = set(params) - set(act.params)
    if unknown:
        raise AgentError(INVALID_PARAMS, f"unknown parameters: {', '.join(sorted(unknown))}")
    checked = {}
    for name, spec in act.params.items():
        if name in params:
            checked[name] = spec.check(name, params[name])
        elif spec.required:
            raise AgentError(INVALID_PARAMS, f"missing parameter '{name}'")
        else:
            checked[name] = spec.default
    return checked


def lookup(name: str) -> Action:
    act = ACTIONS.get(name)
    if act is None:
        raise AgentError(UNKNOWN_ACTION, f"unknown action '{name}'")
    return act


# --- system -------------------------------------------------------------------


def read_release(path: Path) -> str | None:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "DAHLIA_VERSION":
            return value.strip().strip('"')
    return None


def read_meminfo(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key] = int(parts[0]) * 1024
    return {"total": values.get("MemTotal"), "available": values.get("MemAvailable")}


@action("system.info", mutating=False)
def system_info(ctx: Context):
    """Hostname, Dahlia version, kernel, uptime, load and memory."""
    uptime = float((ctx.proc / "uptime").read_text().split()[0])
    load = [float(x) for x in (ctx.proc / "loadavg").read_text().split()[:3]]
    return {
        "hostname": socket.gethostname(),
        "version": read_release(ctx.release_file),
        "kernel": os.uname().release,
        "uptime_seconds": int(uptime),
        "load": load,
        "memory": read_meminfo(ctx.proc / "meminfo"),
    }


# Interfaces of containers, bridges and VMs; the stats show the physical ones.
VIRTUAL_IFACES = ("lo", "docker", "br-", "veth", "virbr", "incusbr", "tap", "tun")


def read_cpu(proc: Path) -> dict:
    """CPU model, core count and the summed jiffies since boot.

    The client takes two samples and computes the busy share from the
    difference of total and idle.
    """
    model, cores = None, 0
    for line in (proc / "cpuinfo").read_text().splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "processor":
            cores += 1
        elif key == "model name" and model is None:
            model = value.strip()
    fields = (proc / "stat").read_text().splitlines()[0].split()
    times = [int(x) for x in fields[1:9]]
    idle = times[3] + times[4]  # idle + iowait
    return {"model": model, "cores": cores, "total": sum(times), "idle": idle}


def read_swap(proc: Path) -> dict:
    values = {}
    for line in (proc / "meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key] = int(parts[0]) * 1024
    total = values.get("SwapTotal", 0)
    return {"total": total, "used": total - values.get("SwapFree", 0)}


def read_network(proc: Path) -> list[dict]:
    """Byte counters of the physical interfaces from /proc/net/dev."""
    out = []
    for line in (proc / "net/dev").read_text().splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if not name or name.startswith(VIRTUAL_IFACES):
            continue
        cols = rest.split()
        out.append({"name": name, "rx_bytes": int(cols[0]), "tx_bytes": int(cols[8])})
    return out


def interface_addresses(ctx: Context) -> dict[str, list[str]]:
    proc = ctx.runner.run(["ip", "-j", "addr", "show", "scope", "global"], check=False)
    try:
        data = json.loads(proc.stdout or "[]")
    except ValueError:
        return {}
    return {
        str(i.get("ifname")): [f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", [])
                               if a.get("local") and a.get("prefixlen") is not None]
        for i in data
    }


def unescape_mount(path: str) -> str:
    """Octal escapes in /proc/self/mounts (space is \\040)."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def read_filesystems(proc: Path) -> list[dict]:
    """Size and use of / and of every filesystem under /mnt.

    Data disks, parity, cache, the mergerfs pools and external disks all
    live under /mnt, so this is what the dashboard shows as storage.
    """
    out, seen = [], set()
    for line in (proc / "self/mounts").read_text().splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        source, mountpoint, fstype = unescape_mount(parts[0]), unescape_mount(parts[1]), parts[2]
        if mountpoint != "/" and not mountpoint.startswith("/mnt/"):
            continue
        if mountpoint in seen:
            continue
        try:
            st = os.statvfs(mountpoint)
        except OSError:
            continue
        seen.add(mountpoint)
        size = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = size - st.f_bfree * st.f_frsize
        out.append({"mountpoint": mountpoint, "source": source, "fstype": fstype,
                    "size": size, "used": used, "free": free})
    return out


@action("system.stats", mutating=False)
def system_stats(ctx: Context):
    """Live values for the overview: CPU, memory, swap, network, filesystems."""
    uptime = float((ctx.proc / "uptime").read_text().split()[0])
    load = [float(x) for x in (ctx.proc / "loadavg").read_text().split()[:3]]
    addresses = interface_addresses(ctx)
    ifaces = read_network(ctx.proc)
    for iface in ifaces:
        iface["addresses"] = addresses.get(iface["name"], [])
    return {
        "uptime_seconds": int(uptime),
        "load": load,
        "cpu": read_cpu(ctx.proc),
        "memory": read_meminfo(ctx.proc / "meminfo"),
        "swap": read_swap(ctx.proc),
        "network": ifaces,
        "filesystems": read_filesystems(ctx.proc),
    }


def rewrite_hosts(text: str, hostname: str) -> str:
    """Point the Debian 127.0.1.1 line at the new hostname."""
    entry = f"127.0.1.1\t{hostname}"
    out = []
    found = False
    for line in text.splitlines():
        if line.split("#", 1)[0].split()[:1] == ["127.0.1.1"]:
            if not found:
                out.append(entry)
                found = True
            continue
        out.append(line)
    if not found:
        out.append(entry)
    return "\n".join(out) + "\n"


@action(
    "system.set_hostname",
    mutating=True,
    params={"hostname": Param(str, pattern=HOSTNAME_RE)},
)
def system_set_hostname(ctx: Context, hostname: str):
    """Set the hostname (one DNS label, lower case) and the mDNS name."""
    ctx.runner.run(["hostnamectl", "set-hostname", hostname])
    hosts = ctx.hosts_file.read_text() if ctx.hosts_file.exists() else ""
    tmp = ctx.hosts_file.with_name(ctx.hosts_file.name + ".dahlia-tmp")
    tmp.write_text(rewrite_hosts(hosts, hostname))
    tmp.chmod(0o644)
    tmp.replace(ctx.hosts_file)
    # Avahi reads the hostname at start only.
    ctx.runner.run(["systemctl", "try-restart", "avahi-daemon.service"])
    # The certificate names <hostname>.local.
    ctx.runner.run(["dahlia-domains", "apply"], timeout=120)
    return {"hostname": hostname}


@action("system.reboot", mutating=True)
def system_reboot(ctx: Context):
    """Reboot. The reply is sent before the system goes down."""
    ctx.runner.run(["systemctl", "--no-block", "reboot"])
    return {"scheduled": True}


@action("system.poweroff", mutating=True)
def system_poweroff(ctx: Context):
    """Power off. The reply is sent before the system goes down."""
    ctx.runner.run(["systemctl", "--no-block", "poweroff"])
    return {"scheduled": True}


# --- storage ------------------------------------------------------------------


def dahlia_disk(ctx: Context, *args: str, timeout: int = 300) -> str:
    return ctx.runner.run(["dahlia-disk", *args], timeout=timeout).stdout


@action("storage.list", mutating=False)
def storage_list(ctx: Context):
    """Disks with their status, as dahlia-disk list --json shows them."""
    return {"disks": json.loads(dahlia_disk(ctx, "list", "--json"))}


@action(
    "storage.add",
    mutating=True,
    params={
        "uuid": Param(str, pattern=UUID_RE),
        "role": Param(str, choices=DISK_ROLES),
    },
)
def storage_add(ctx: Context, uuid: str, role: str):
    """Mount an existing filesystem in a role. Never formats."""
    dahlia_disk(ctx, "add", uuid, "--role", role)
    return {"uuid": uuid, "role": role}


@action(
    "storage.format",
    mutating=True,
    params={
        "device": Param(str, pattern=DEVICE_RE),
        "filesystem": Param(str, choices=FORMAT_FILESYSTEMS),
        "confirm": Param(str),
    },
)
def storage_format(ctx: Context, device: str, filesystem: str, confirm: str):
    """Partition and format a blank or imported disk. confirm repeats the disk name."""
    if confirm != device.removeprefix("/dev/"):
        raise AgentError(INVALID_PARAMS, f"confirm must be '{device.removeprefix('/dev/')}'")
    out = dahlia_disk(ctx, "format", device, "--fs", filesystem, "--json", timeout=900)
    return json.loads(out)


@action(
    "storage.import",
    mutating=True,
    params={"uuid": Param(str, pattern=UUID_RE), "target": Param(str, pattern=IMPORT_TARGET_RE)},
)
def storage_import(ctx: Context, uuid: str, target: str):
    """Copy a disk's data to /mnt/user/<target> in the background, then verify it."""
    state = ctx.runner.run(["systemctl", "is-active", f"{IMPORT_UNIT}.service"], check=False)
    if state.stdout.strip() in ("active", "activating"):
        raise AgentError(BUSY, "an import is already running")
    # A bad source or target fails here, before the background job starts.
    dahlia_disk(ctx, "import", uuid, "--to", target, "--check", timeout=60)
    ctx.runner.run(["systemctl", "reset-failed", f"{IMPORT_UNIT}.service"], check=False)
    ctx.runner.run(["systemd-run", f"--unit={IMPORT_UNIT}", "--collect",
                    "--property=Nice=10", "--property=IOSchedulingClass=idle",
                    "dahlia-disk", "import", uuid, "--to", target])
    return {"uuid": uuid, "target": f"{ctx.pool}/{target}"}


@action("storage.import_status", mutating=False)
def storage_import_status(ctx: Context):
    """Progress of the last import, or null."""
    path = ctx.run_dir / "import.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"import": None}
    if data.get("phase") not in ("done", "failed"):
        unit = ctx.runner.run(["systemctl", "is-active", f"{IMPORT_UNIT}.service"], check=False)
        if unit.stdout.strip() not in ("active", "activating"):
            # The job died without writing its end state.
            data.update(phase="failed", error=data.get("error") or "import stopped")
    return {"import": data}


@action(
    "storage.remove",
    mutating=True,
    params={
        "uuid": Param(str, pattern=UUID_RE),
        "force": Param(bool, required=False, default=False),
    },
)
def storage_remove(ctx: Context, uuid: str, force: bool):
    """Unmount a disk and drop it from Dahlia. Data stays on the disk."""
    args = ["remove", uuid]
    if force:
        args.append("--force")
    dahlia_disk(ctx, *args)
    return {"uuid": uuid}


@action(
    "storage.set_option",
    mutating=True,
    params={
        "name": Param(str, choices=STORAGE_OPTIONS),
        "value": Param(int, minimum=0, maximum=100000),
    },
)
def storage_set_option(ctx: Context, name: str, value: int):
    """Change a mover or SnapRAID setting. dahlia-disk checks the range."""
    dahlia_disk(ctx, "set-option", name, str(value))
    return {"name": name, "value": value}


# --- jobs ---------------------------------------------------------------------

JOB_PROPERTIES = (
    "ActiveState",
    "SubState",
    "Result",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
    "ExecMainStatus",
)


def unit_state(ctx: Context, unit: str) -> dict:
    cmd = ["systemctl", "show", unit]
    for prop in JOB_PROPERTIES:
        cmd += ["-p", prop]
    out = ctx.runner.run(cmd).stdout
    props = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value
    return {
        "active": props.get("ActiveState", ""),
        "sub": props.get("SubState", ""),
        "result": props.get("Result", ""),
        "started": props.get("ExecMainStartTimestamp", ""),
        "finished": props.get("ExecMainExitTimestamp", ""),
        "exit_status": int(props["ExecMainStatus"]) if props.get("ExecMainStatus") else None,
    }


def is_active(state: dict) -> bool:
    return state["active"] in ("active", "activating", "deactivating", "reloading")


def is_running(ctx: Context, unit: str) -> bool:
    return is_active(unit_state(ctx, unit))


@action(
    "job.start",
    mutating=True,
    params={"job": Param(str, choices=STARTABLE_JOBS)},
)
def job_start(ctx: Context, job: str):
    """Start mover, SnapRAID sync or scrub now. Returns at once."""
    unit = JOBS[job]
    if is_running(ctx, unit):
        raise AgentError(BUSY, f"{job} is already running")
    ctx.runner.run(["systemctl", "start", "--no-block", unit])
    return {"job": job, "started": True}


@action(
    "job.status",
    mutating=False,
    params={"job": Param(str, choices=tuple(JOBS))},
)
def job_status(ctx: Context, job: str):
    """State and last result of a job."""
    state = unit_state(ctx, JOBS[job])
    # Jobs are static units; systemd reports "success" for one that never ran.
    if not state["started"]:
        state["result"] = ""
    return {"job": job, **state}


# --- parity -------------------------------------------------------------------

# Forced sync and repairs run as a transient unit; it stays after it finished
# (RemainAfterExit) so the dashboard can show the result.
PARITY_UNIT = "dahlia-parity.service"
PARITY_WHAT_RE = re.compile(r"^(bad|disk[1-9][0-9]?)$")


def parity_busy(ctx: Context) -> str | None:
    for name, unit in (("mover", JOBS["mover"]), ("sync", JOBS["snapraid-sync"]),
                       ("scrub", JOBS["snapraid-scrub"]), ("repair", PARITY_UNIT)):
        if is_running(ctx, unit):
            return name
    return None


def start_parity_unit(ctx: Context, description: str, *args: str) -> None:
    busy = parity_busy(ctx)
    if busy:
        raise AgentError(BUSY, f"{busy} is running")
    ctx.runner.run(["systemctl", "stop", PARITY_UNIT], check=False)
    ctx.runner.run(["systemctl", "reset-failed", PARITY_UNIT], check=False)
    ctx.runner.run(["systemd-run", "--unit", PARITY_UNIT, "--no-block",
                    "--property", "Type=oneshot", "--property", "RemainAfterExit=yes",
                    "--property", f"OnFailure=dahlia-notify-failure@{PARITY_UNIT}.service",
                    "--description", description, "/usr/sbin/dahlia-snapraid", *args])


@action("parity.get", mutating=False)
def parity_get(ctx: Context):
    """Parity disks, problems, snapraid status, last runs and running jobs."""
    out = ctx.runner.run(["dahlia-snapraid", "status", "--json"], timeout=600).stdout
    info = json.loads(out)
    info["jobs"] = {
        "sync": unit_state(ctx, JOBS["snapraid-sync"]),
        "scrub": unit_state(ctx, JOBS["snapraid-scrub"]),
        "mover": unit_state(ctx, JOBS["mover"]),
        "repair": unit_state(ctx, PARITY_UNIT),
    }
    return info


@action(
    "parity.sync",
    mutating=True,
    params={"force": Param(bool, required=False, default=False)},
)
def parity_sync(ctx: Context, force: bool):
    """Sync now; force skips the delete threshold after the admin checked the disks."""
    if not force:
        busy = parity_busy(ctx)
        if busy:
            raise AgentError(BUSY, f"{busy} is running")
        ctx.runner.run(["systemctl", "start", "--no-block", JOBS["snapraid-sync"]])
        return {"started": True, "force": False}
    start_parity_unit(ctx, "Dahlia parity: forced sync", "sync", "--force")
    return {"started": True, "force": True}


@action(
    "parity.fix",
    mutating=True,
    params={"what": Param(str, pattern=PARITY_WHAT_RE)},
)
def parity_fix(ctx: Context, what: str):
    """Repair bad blocks ("bad") or rebuild data disk N ("diskN") from the parity."""
    if what == "bad":
        start_parity_unit(ctx, "Dahlia parity: repair bad blocks", "fix", "--bad")
    else:
        disk = what.removeprefix("disk")
        start_parity_unit(ctx, f"Dahlia parity: restore /mnt/disk{disk}", "fix", "--disk", disk)
    return {"started": True, "what": what}


# --- updates ------------------------------------------------------------------

APT_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "DEBIAN_FRONTEND": "noninteractive",
    "LC_ALL": "C.UTF-8",
}
INST_RE = re.compile(r"^Inst (\S+) (?:\[([^\]]+)\] )?\((\S+) ([^)]*)\)")


def parse_simulation(out: str) -> list[dict]:
    """Packages from 'apt-get -s full-upgrade' output."""
    pkgs = []
    for line in out.splitlines():
        m = INST_RE.match(line)
        if m:
            pkgs.append(
                {
                    "name": m.group(1),
                    "current": m.group(2),
                    "new": m.group(3),
                    "origin": m.group(4).split(" [")[0],
                }
            )
    return pkgs


@action("updates.check", mutating=True)
def updates_check(ctx: Context):
    """Refresh the package lists and list pending upgrades."""
    if is_running(ctx, JOBS["update"]):
        raise AgentError(BUSY, "an update is running")
    ctx.runner.run(["apt-get", "update", "-q"], timeout=600, env=APT_ENV)
    out = ctx.runner.run(["apt-get", "-s", "-q", "full-upgrade"], timeout=120, env=APT_ENV)
    return {"packages": parse_simulation(out.stdout)}


# dahlia-update.service is a static unit, so its last run stays visible in
# systemctl show (a transient unit would be gone after it finished).
UPDATE_CMD = ["systemctl", "start", "--no-block", "dahlia-update.service"]


# apt-get -q output of the running update, per package.
GET_RE = re.compile(r"^Get:\d+ \S+ \S+ \S+ ([a-z0-9][a-z0-9+.-]*) ")
UNPACK_RE = re.compile(r"^Unpacking ([a-z0-9][a-z0-9+.-]*)(?::\S+)? \(")
SETUP_RE = re.compile(r"^Setting up ([a-z0-9][a-z0-9+.-]*)(?::\S+)? \(")
STEPS = ("waiting", "downloaded", "unpacked", "done")
START_GRACE = 120  # seconds the unit may take to show up as started


def update_plan_file(ctx: Context) -> Path:
    return ctx.run_dir / "update.json"


def update_invocation(ctx: Context) -> str:
    out = ctx.runner.run(["systemctl", "show", "-p", "InvocationID", "--value", JOBS["update"]])
    value = out.stdout.strip()
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else ""


@action("updates.apply", mutating=True)
def updates_apply(ctx: Context):
    """Install all pending upgrades as job 'update'. snapper snapshots before and after.

    The packages apt will install are kept in /run/dahlia/update.json, so
    updates.progress can show what is done and what is still open.
    """
    if is_running(ctx, JOBS["update"]):
        raise AgentError(BUSY, "an update is already running")
    out = ctx.runner.run(["apt-get", "-s", "-q", "full-upgrade"], timeout=120, env=APT_ENV)
    # The run before this one: until systemd has a new invocation, the update is starting.
    plan = {"packages": [p["name"] for p in parse_simulation(out.stdout)],
            "started": int(ctx.clock()), "previous": update_invocation(ctx)}
    ctx.runner.run(UPDATE_CMD)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    update_plan_file(ctx).write_text(json.dumps(plan) + "\n")
    return {"job": "update", "started": True, "packages": len(plan["packages"])}


def update_steps(log: str, names: list[str]) -> dict[str, str]:
    """Step of each planned package from the apt-get output: waiting, downloaded, unpacked, done."""
    steps = dict.fromkeys(names, "waiting")
    for line in log.splitlines():
        for regex, step in ((GET_RE, "downloaded"), (UNPACK_RE, "unpacked"), (SETUP_RE, "done")):
            m = regex.match(line)
            if m and m.group(1) in steps and STEPS.index(step) > STEPS.index(steps[m.group(1)]):
                steps[m.group(1)] = step
    return steps


@action("updates.progress", mutating=False)
def updates_progress(ctx: Context):
    """State of the last update: running/done/failed, percent, done and open packages."""
    state = unit_state(ctx, JOBS["update"])
    try:
        plan = json.loads(update_plan_file(ctx).read_text())
        names = [n for n in plan["packages"] if isinstance(n, str)]
    except (OSError, ValueError, KeyError, TypeError):
        return {"state": "none"}
    invocation = update_invocation(ctx)
    log = ""
    if invocation and invocation != plan.get("previous"):
        log = ctx.runner.run(["journalctl", f"_SYSTEMD_INVOCATION_ID={invocation}", "-o", "cat",
                              "--no-pager"], timeout=30).stdout
    steps = update_steps(log, names)
    waiting = invocation == plan.get("previous")
    if is_active(state) or (waiting and ctx.clock() - plan.get("started", 0) < START_GRACE):
        status = "running"
    elif waiting:
        status = "failed"  # systemd never started it
    elif state["result"] == "success" and state["started"]:
        status = "done"
    else:
        status = "failed"
    total = len(names) * (len(STEPS) - 1)
    reached = sum(STEPS.index(v) for v in steps.values())
    percent = 100 if status == "done" or not total else reached * 100 // total
    lines = [ln for ln in log.splitlines() if ln.strip()]
    return {
        "state": status,
        "percent": percent,
        "started": plan.get("started"),
        "packages": [{"name": n, "step": steps[n]} for n in names],
        "last": lines[-1] if lines else "",
    }


# --- apps ---------------------------------------------------------------------

APP_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
APP_SHARE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Where app data and folders live: the array or the system SSD.
ARRAY_ROOT = "/mnt/user"
SYSTEM_ROOT = "/srv"
ROOTS = (ARRAY_ROOT, SYSTEM_ROOT)
# A folder below a root, as dahlia-apps checks it (no "..", no leading "/").
FOLDER_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9 ._()+,-]{0,99}"
FOLDER_RE = re.compile(rf"^{FOLDER_SEGMENT}(/{FOLDER_SEGMENT}){{0,9}}$")
FOLDER_REF_RE = re.compile(rf"^(/mnt/user|/srv)/({FOLDER_RE.pattern[1:-1]})$")
BROWSE_RE = re.compile(rf"^$|{FOLDER_RE.pattern}")
BROWSE_MAX = 500


def dahlia_apps(ctx: Context, *args: str, timeout: int = 300):
    out = ctx.runner.run(["dahlia-apps", *args], timeout=timeout).stdout
    return json.loads(out) if out.strip() else None


def app_unit(app_id: str) -> str:
    return f"dahlia-app-{app_id}.service"


@action(
    "apps.catalog",
    mutating=True,
    params={"refresh": Param(bool, required=False, default=False)},
)
def apps_catalog(ctx: Context, refresh: bool):
    """Templates in the app catalog and its age; refresh downloads it first."""
    args = ["catalog", "--refresh"] if refresh else ["catalog"]
    return dahlia_apps(ctx, *args, timeout=600)


@action("apps.list", mutating=False)
def apps_list(ctx: Context):
    """Installed apps with container status and available updates."""
    return {"apps": dahlia_apps(ctx, "list")}


def folder_args(folders: dict) -> list[str]:
    """{"media": "/mnt/user/Filme"} -> --folder media=/mnt/user/Filme."""
    args = []
    for share, ref in sorted(folders.items()):
        m = FOLDER_REF_RE.match(ref) if isinstance(ref, str) else None
        if not APP_SHARE_RE.match(str(share)) or not m or ".." in m.group(2).split("/"):
            raise AgentError(INVALID_PARAMS,
                             f"'folders.{share}' must be a folder below /mnt/user or /srv")
        args += ["--folder", f"{share}={ref}"]
    return args


def start_app_job(ctx: Context, app_id: str, command: str, extra: list[str] | None = None):
    extra = extra or []
    unit = app_unit(app_id)
    if is_running(ctx, unit):
        raise AgentError(BUSY, f"{app_id} is busy")
    # No data disk, port taken, ...: report it now, not as a failed job.
    dahlia_apps(ctx, "precheck", command, app_id, *extra)
    # A failed job stays loaded, so apps.job can report it; clear it first.
    # (CollectMode=inactive-or-failed would unload it, and systemctl show
    # then reports an unknown unit as inactive with Result=success.)
    ctx.runner.run(["systemctl", "reset-failed", unit], check=False)
    ctx.runner.run([
        "systemd-run",
        f"--unit={unit}",
        f"--description=Dahlia app {command} {app_id}",
        "--property=After=docker.service",
        "--",
        "/usr/sbin/dahlia-apps",
        command,
        app_id,
        *extra,
    ])
    return {"id": app_id, "started": True}


@action(
    "apps.install",
    mutating=True,
    params={"id": Param(str, pattern=APP_ID_RE),
            "folders": Param(dict, required=False, default={}),
            "location": Param(str, required=False, default="", choices=("", *ROOTS))},
)
def apps_install(ctx: Context, id: str, folders: dict, location: str):
    """Install an app from the catalog in the background (image pull).

    location: where app data goes, /mnt/user or /srv; empty = /mnt/user if
    mounted. folders maps a share of the app to a folder below either.
    """
    extra = folder_args(folders) + (["--location", location] if location else [])
    return start_app_job(ctx, id, "install", extra)


# --- folder picker ------------------------------------------------------------


def location_root(ctx: Context, location: str) -> tuple[Path, bool]:
    """Root of a location and whether it can be used now."""
    if location == ARRAY_ROOT:
        return ctx.pool, os.path.ismount(ctx.pool)
    return ctx.system, ctx.system.is_dir()


def root_dir(root: Path, rel: str) -> Path:
    """rel below root, refusing symlinks that lead out of it."""
    if ".." in rel.split("/"):
        raise AgentError(INVALID_PARAMS, f"'path' must stay below {root}")
    base = root.resolve()
    real = (root / rel).resolve() if rel else base
    if real != base and base not in real.parents:
        raise AgentError(INVALID_PARAMS, f"'path' leads outside {root}")
    return real


def locations(ctx: Context) -> list[dict]:
    out = []
    for name, path in (("array", ARRAY_ROOT), ("system", SYSTEM_ROOT)):
        _, ok = location_root(ctx, path)
        out.append({"name": name, "path": path, "available": ok})
    return out


@action("files.browse", mutating=False, params={
    "root": Param(str, required=False, default=ARRAY_ROOT, choices=ROOTS),
    "path": Param(str, required=False, default="", pattern=BROWSE_RE)})
def files_browse(ctx: Context, root: str, path: str):
    """Folders directly below <root>/<path>, for the folder picker. No files.

    root is /mnt/user (array) or /srv (system SSD); locations lists both
    with whether they can be used.
    """
    base, ok = location_root(ctx, root)
    out = {"root": root, "path": path, "locations": locations(ctx),
           "mounted": ok, "exists": False, "dirs": []}
    if not ok:
        return out
    d = root_dir(base, path)
    if not d.is_dir():
        return out
    dirs = []
    with os.scandir(d) as it:
        for e in it:
            if e.name.startswith(".") or not FOLDER_RE.match(e.name):
                continue
            if e.is_dir(follow_symlinks=False):
                dirs.append(e.name)
    dirs.sort(key=str.lower)
    return {**out, "exists": True, "dirs": dirs[:BROWSE_MAX], "truncated": len(dirs) > BROWSE_MAX}


@action("files.mkdir", mutating=True, params={
    "root": Param(str, required=False, default=ARRAY_ROOT, choices=ROOTS),
    "path": Param(str, pattern=FOLDER_RE)})
def files_mkdir(ctx: Context, root: str, path: str):
    """New folder below /mnt/user or /srv, group dahlia, like a share."""
    base, ok = location_root(ctx, root)
    if not ok:
        raise AgentError(FAILED, f"{base} is not available")
    if path.split("/")[0] == "appdata":
        raise AgentError(INVALID_PARAMS, "folders below appdata belong to apps")
    parent = root_dir(base, path.rsplit("/", 1)[0] if "/" in path else "")
    if not parent.is_dir():
        raise AgentError(FAILED, "parent folder does not exist")
    target = parent / path.rsplit("/", 1)[-1]
    if target.exists():
        raise AgentError(FAILED, f"{path} already exists")
    target.mkdir(mode=0o2775)
    ctx.runner.run(["chgrp", "dahlia", str(target)], check=False)
    target.chmod(0o2775)
    return {"root": root, "path": path}


@action("apps.update", mutating=True, params={"id": Param(str, pattern=APP_ID_RE)})
def apps_update(ctx: Context, id: str):
    """Take the catalog's template of an app and pull its images, in the background."""
    return start_app_job(ctx, id, "update")


def job_error(ctx: Context, unit: str) -> str:
    """Last error dahlia-apps logged in the unit's journal, or ""."""
    out = ctx.runner.run(["journalctl", "-u", unit, "-n", "50", "-o", "cat", "--no-pager"]).stdout
    errors = [ln.strip() for ln in out.splitlines() if ln.startswith("dahlia-apps: ")]
    return errors[-1].removeprefix("dahlia-apps: ")[:500] if errors else ""


@action("apps.job", mutating=False, params={"id": Param(str, pattern=APP_ID_RE)})
def apps_job(ctx: Context, id: str):
    """State of the last install or update of an app, with the error if it failed."""
    unit = app_unit(id)
    state = unit_state(ctx, unit)
    if state["result"] != "success" and not is_active(state):
        state["error"] = job_error(ctx, unit)
    return {"id": id, **state}


@action("apps.start", mutating=True, params={"id": Param(str, pattern=APP_ID_RE)})
def apps_start(ctx: Context, id: str):
    """Start an app and start it at boot."""
    return dahlia_apps(ctx, "start", id)


@action("apps.stop", mutating=True, params={"id": Param(str, pattern=APP_ID_RE)})
def apps_stop(ctx: Context, id: str):
    """Stop an app and keep it stopped at boot."""
    return dahlia_apps(ctx, "stop", id)


@action(
    "apps.remove",
    mutating=True,
    params={
        "id": Param(str, pattern=APP_ID_RE),
        "delete_data": Param(bool, required=False, default=False),
    },
)
def apps_remove(ctx: Context, id: str, delete_data: bool):
    """Remove an app; its data under /mnt/user/appdata only on request."""
    if is_running(ctx, app_unit(id)):
        raise AgentError(BUSY, f"{id} is busy")
    args = ["remove", id] + (["--delete-data"] if delete_data else [])
    return dahlia_apps(ctx, *args, timeout=600)


# --- domains ------------------------------------------------------------------

LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN_RE = re.compile(rf"^{LABEL}(?:\.{LABEL}){{1,5}}$")


def dahlia_domains(ctx: Context, *args: str):
    return json.loads(ctx.runner.run(["dahlia-domains", *args], timeout=120).stdout)


@action("domains.get", mutating=False)
def domains_get(ctx: Context):
    """Domain, mDNS aliases, DNS records to create and certificate state."""
    return dahlia_domains(ctx, "show")


@action(
    "domains.set",
    mutating=True,
    params={
        "domain": Param(str, pattern=DOMAIN_RE),
        "mdns_aliases": Param(bool),
    },
)
def domains_set(ctx: Context, domain: str, mdns_aliases: bool):
    """Change the app domain and mDNS aliases; renews the certificate."""
    return dahlia_domains(ctx, "set", "--domain", domain, "--mdns", "on" if mdns_aliases else "off")


# --- meta ---------------------------------------------------------------------


@action("agent.actions", mutating=False)
def agent_actions(ctx: Context):
    """The action list with parameters."""
    out = []
    for act in sorted(ACTIONS.values(), key=lambda a: a.name):
        params = {}
        for pname, spec in act.params.items():
            params[pname] = {"type": spec.kind.__name__, "required": spec.required}
            if spec.choices is not None:
                params[pname]["choices"] = list(spec.choices)
        out.append(
            {
                "name": act.name,
                "mutating": act.mutating,
                "description": act.description,
                "params": params,
            }
        )
    return out


# Actions in their own modules; importing them registers them.
from . import (  # noqa: E402, F401
    backup,
    containers,
    diagnostics,
    hardware,
    network,
    notify,
    settings,
    smb,
    snapshots,
    unattended,
    ups,
)
