"""Automatic Debian security updates with unattended-upgrades.

dahlia-base ships /etc/apt/apt.conf.d/51dahlia-unattended-upgrades, which
limits unattended-upgrades to the Debian security archive; everything else
(point releases, Dahlia, Docker) stays a click in the dashboard with a
snapshot before. The switch is the standard Debian file
/etc/apt/apt.conf.d/20auto-upgrades. The reboot settings live in
/etc/dahlia/updates.json and are written to
/etc/apt/apt.conf.d/52dahlia-auto-reboot. apt-daily-upgrade.timer runs it
(Debian default: daily around 06:00).
"""

import json
import re

from .actions import Context, Param, action
from .settings import read_json, write_file

TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
DEFAULTS = {"reboot": False, "reboot_time": "04:00"}
LOG_TAIL = 256 * 1024
LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (INFO|WARNING|ERROR) (.*)$")


def periodic_file(ctx: Context):
    return ctx.etc / "apt" / "apt.conf.d" / "20auto-upgrades"


def reboot_file(ctx: Context):
    return ctx.etc / "apt" / "apt.conf.d" / "52dahlia-auto-reboot"


def config_file(ctx: Context):
    return ctx.etc / "dahlia" / "updates.json"


def enabled(ctx: Context) -> bool:
    try:
        text = periodic_file(ctx).read_text()
    except OSError:
        return False
    m = re.search(r'^\s*APT::Periodic::Unattended-Upgrade\s+"(\d+)"\s*;', text, re.M)
    return bool(m and m.group(1) != "0")


def load(ctx: Context) -> dict:
    cfg = read_json(config_file(ctx), DEFAULTS)
    out = dict(DEFAULTS)
    if isinstance(cfg.get("reboot"), bool):
        out["reboot"] = cfg["reboot"]
    if isinstance(cfg.get("reboot_time"), str) and TIME_RE.fullmatch(cfg["reboot_time"]):
        out["reboot_time"] = cfg["reboot_time"]
    return out


def render_periodic(on: bool) -> str:
    return ("// Written by Dahlia (dashboard: Updates). Package lists stay fresh either way.\n"
            'APT::Periodic::Update-Package-Lists "1";\n'
            f'APT::Periodic::Unattended-Upgrade "{1 if on else 0}";\n')


def render_reboot(cfg: dict) -> str:
    return ("// Written by Dahlia from /etc/dahlia/updates.json. Do not edit.\n"
            f'Unattended-Upgrade::Automatic-Reboot "{str(cfg["reboot"]).lower()}";\n'
            'Unattended-Upgrade::Automatic-Reboot-WithUsers "true";\n'
            f'Unattended-Upgrade::Automatic-Reboot-Time "{cfg["reboot_time"]}";\n')


def last_run(ctx: Context) -> dict | None:
    """The last unattended-upgrades run from its log, or None."""
    path = ctx.log_dir / "unattended-upgrades" / "unattended-upgrades.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - LOG_TAIL))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    run = None
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        stamp, level, msg = m.groups()
        if msg.startswith("Starting unattended upgrades script"):
            run = {"time": stamp, "result": "running", "packages": [], "errors": []}
        elif run is None:
            continue
        elif msg.startswith("Packages that will be upgraded:"):
            run["packages"] = msg.split(":", 1)[1].split()
        elif msg.startswith("No packages found that can be upgraded unattended"):
            run["result"] = "nothing"
        elif msg.startswith("All upgrades installed"):
            run["result"] = "installed"
        elif level == "ERROR":
            run["result"] = "failed"
            run["errors"].append(msg)
    if run:
        run["errors"] = run["errors"][-5:]
    return run


def reboot_required(ctx: Context) -> list[str] | None:
    """Packages that asked for a reboot; None if no reboot is needed."""
    if not ctx.reboot_flag.exists():
        return None
    try:
        pkgs = ctx.reboot_flag.with_name(ctx.reboot_flag.name + ".pkgs").read_text().split()
    except OSError:
        pkgs = []
    return sorted(set(pkgs))


def status(ctx: Context) -> dict:
    return {"enabled": enabled(ctx), **load(ctx), "last": last_run(ctx),
            "reboot_required": reboot_required(ctx)}


@action("updates.auto", mutating=False)
def updates_auto(ctx: Context):
    """Automatic security updates: switch, reboot settings, last run, pending reboot."""
    return status(ctx)


@action(
    "updates.set_auto",
    mutating=True,
    params={"enabled": Param(bool), "reboot": Param(bool),
            "reboot_time": Param(str, pattern=TIME_RE)},
)
def updates_set_auto(ctx: Context, enabled: bool, reboot: bool, reboot_time: str):
    """Turn automatic security updates on or off and set the automatic reboot."""
    cfg = {"reboot": reboot, "reboot_time": reboot_time}
    write_file(config_file(ctx), json.dumps(cfg, indent=2) + "\n")
    write_file(reboot_file(ctx), render_reboot(cfg))
    write_file(periodic_file(ctx), render_periodic(enabled))
    return status(ctx)

