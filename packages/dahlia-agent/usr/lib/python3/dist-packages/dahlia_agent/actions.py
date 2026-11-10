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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .protocol import BUSY, FAILED, INVALID_PARAMS, UNKNOWN_ACTION, AgentError

# --- running commands ---------------------------------------------------------


class Runner:
    """Runs system commands. Tests replace it with a fake."""

    def run(
        self, cmd: list[str], *, timeout: int = 120, check: bool = True, env: dict | None = None
    ) -> subprocess.CompletedProcess:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                env=env,
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


# --- parameter checks ---------------------------------------------------------

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# ext4/XFS/Btrfs UUIDs and the short NTFS/FAT serials lsblk shows.
UUID_RE = re.compile(r"^[0-9A-Fa-f]{4}([0-9A-Fa-f-]{4,32}[0-9A-Fa-f])?$")

DISK_ROLES = ("data", "parity", "cache", "external")
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
}
# Jobs job.start may start. Updates go through updates.apply.
STARTABLE_JOBS = ("mover", "snapraid-sync", "snapraid-scrub")


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


def is_running(ctx: Context, unit: str) -> bool:
    return unit_state(ctx, unit)["active"] in ("active", "activating", "deactivating", "reloading")


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
    return {"job": job, **unit_state(ctx, JOBS[job])}


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


UPDATE_CMD = [
    "systemd-run",
    "--unit=dahlia-update",
    "--description=Dahlia system update",
    "--property=CollectMode=inactive-or-failed",
    "--setenv=DEBIAN_FRONTEND=noninteractive",
    "--setenv=LC_ALL=C.UTF-8",
    "--",
    "/usr/bin/apt-get",
    "-y",
    "-q",
    "-o",
    "Dpkg::Options::=--force-confdef",
    "-o",
    "Dpkg::Options::=--force-confold",
    "full-upgrade",
]


@action("updates.apply", mutating=True)
def updates_apply(ctx: Context):
    """Install all pending upgrades as job 'update'. snapper snapshots before and after."""
    if is_running(ctx, JOBS["update"]):
        raise AgentError(BUSY, "an update is already running")
    ctx.runner.run(UPDATE_CMD)
    return {"job": "update", "started": True}


# --- apps ---------------------------------------------------------------------

APP_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


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
    """Templates in the app catalog; refresh downloads it first."""
    args = ["catalog", "--refresh"] if refresh else ["catalog"]
    return {"apps": dahlia_apps(ctx, *args, timeout=600)}


@action("apps.list", mutating=False)
def apps_list(ctx: Context):
    """Installed apps with container status."""
    return {"apps": dahlia_apps(ctx, "list")}


def start_app_job(ctx: Context, app_id: str, command: str):
    unit = app_unit(app_id)
    if is_running(ctx, unit):
        raise AgentError(BUSY, f"{app_id} is busy")
    ctx.runner.run([
        "systemd-run",
        f"--unit={unit}",
        f"--description=Dahlia app {command} {app_id}",
        "--property=CollectMode=inactive-or-failed",
        "--property=After=docker.service",
        "--",
        "/usr/sbin/dahlia-apps",
        command,
        app_id,
    ])
    return {"id": app_id, "started": True}


@action("apps.install", mutating=True, params={"id": Param(str, pattern=APP_ID_RE)})
def apps_install(ctx: Context, id: str):
    """Install an app from the catalog in the background (image pull)."""
    return start_app_job(ctx, id, "install")


@action("apps.update", mutating=True, params={"id": Param(str, pattern=APP_ID_RE)})
def apps_update(ctx: Context, id: str):
    """Pull the current image and template of an app in the background."""
    return start_app_job(ctx, id, "update")


@action("apps.job", mutating=False, params={"id": Param(str, pattern=APP_ID_RE)})
def apps_job(ctx: Context, id: str):
    """State of the last install or update of an app."""
    return {"id": id, **unit_state(ctx, app_unit(id))}


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
