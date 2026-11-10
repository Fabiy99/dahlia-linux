"""System settings: time zone and NTP, disk spindown, job schedules.

Settings Dahlia owns are kept as JSON under /etc/dahlia. Timer schedules
go into systemd drop-ins, so the packaged units stay untouched.
"""

import json
import re
from pathlib import Path

from .actions import Context, Param, action
from .protocol import INVALID_PARAMS, AgentError

# --- helpers ------------------------------------------------------------------


def read_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def write_file(path: Path, text: str, mode: int = 0o644) -> None:
    """Write atomically: temp file next to the target, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".dahlia-tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)


def show_props(ctx: Context, cmd: list[str]) -> dict[str, str]:
    props = {}
    for line in ctx.runner.run(cmd).stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value
    return props


# --- time ---------------------------------------------------------------------

TZ_RE = re.compile(r"^[A-Za-z0-9_+-]{1,32}(/[A-Za-z0-9_+-]{1,32}){0,2}$")


def timezones(ctx: Context) -> list[str]:
    return ctx.runner.run(["timedatectl", "list-timezones"]).stdout.split()


@action("settings.time", mutating=False)
def settings_time(ctx: Context):
    """Time zone, NTP state and the list of known time zones."""
    props = show_props(ctx, ["timedatectl", "show"])
    return {
        "timezone": props.get("Timezone", ""),
        "ntp": props.get("NTP") == "yes",
        "synchronized": props.get("NTPSynchronized") == "yes",
        "time": props.get("TimeUSec", ""),
        "timezones": timezones(ctx),
    }


@action(
    "settings.set_time",
    mutating=True,
    params={
        "timezone": Param(str, pattern=TZ_RE),
        "ntp": Param(bool),
    },
)
def settings_set_time(ctx: Context, timezone: str, ntp: bool):
    """Set the time zone and switch NTP (systemd-timesyncd) on or off."""
    if timezone not in timezones(ctx):
        raise AgentError(INVALID_PARAMS, f"unknown time zone '{timezone}'")
    ctx.runner.run(["timedatectl", "set-timezone", timezone])
    ctx.runner.run(["timedatectl", "set-ntp", "true" if ntp else "false"])
    return {"timezone": timezone, "ntp": ntp}


# --- disk spindown ------------------------------------------------------------

# Minutes; 0 = never. hdparm -S takes 5 s steps up to 20 min, then 30 min steps.
SPINDOWN_MINUTES = (0, 10, 15, 20, 30, 60, 120, 180, 240, 300)
# Per disk: -1 = use the default.
DISK_SPINDOWN = (-1, *SPINDOWN_MINUTES)
SERIAL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def spindown_file(ctx: Context) -> Path:
    return ctx.etc / "dahlia/spindown.json"


def hdparm_value(minutes: int) -> int:
    if minutes <= 20:
        return minutes * 12
    return 240 + minutes // 30


def spinning_disks(ctx: Context) -> list[dict]:
    out = ctx.runner.run(["lsblk", "-dJb", "-o", "NAME,TYPE,ROTA,SERIAL,MODEL,SIZE"]).stdout
    disks = []
    for d in json.loads(out or "{}").get("blockdevices", []):
        name = str(d.get("name", ""))
        if d.get("type") != "disk" or name.startswith(("zram", "loop")):
            continue
        if not d.get("rota") or d.get("rota") in ("0", 0):
            continue
        disks.append({
            "path": f"/dev/{name}",
            "serial": (d.get("serial") or "").strip() or None,
            "model": (d.get("model") or "").strip() or None,
            "size": int(d.get("size") or 0),
        })
    return disks


def spindown_config(ctx: Context) -> dict:
    data = read_json(spindown_file(ctx), {})
    default = data.get("default")
    disks = data.get("disks") if isinstance(data.get("disks"), dict) else {}
    return {
        "default": default if default in SPINDOWN_MINUTES else None,
        "disks": {k: v for k, v in disks.items() if v in SPINDOWN_MINUTES and SERIAL_RE.match(k)},
    }


def apply_spindown(ctx: Context) -> dict:
    """Set the standby timer of every spinning disk. NVMe/SSDs are skipped."""
    cfg = spindown_config(ctx)
    applied, failed = [], []
    for d in spinning_disks(ctx):
        minutes = cfg["disks"].get(d["serial"] or "", cfg["default"])
        if minutes is None:
            continue
        proc = ctx.runner.run(
            ["hdparm", "-S", str(hdparm_value(minutes)), d["path"]], timeout=30, check=False
        )
        if proc.returncode == 0:
            applied.append({"path": d["path"], "minutes": minutes})
        else:
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            failed.append({"path": d["path"], "error": lines[-1] if lines else "hdparm failed"})
    return {"applied": applied, "failed": failed}


@action("settings.spindown", mutating=False)
def settings_spindown(ctx: Context):
    """Spindown default, per-disk values and the spinning disks."""
    cfg = spindown_config(ctx)
    disks = []
    for d in spinning_disks(ctx):
        disks.append({**d, "minutes": cfg["disks"].get(d["serial"] or "", -1)})
    return {"default": cfg["default"], "choices": list(SPINDOWN_MINUTES), "disks": disks}


@action(
    "settings.set_spindown",
    mutating=True,
    params={
        "default": Param(int, choices=SPINDOWN_MINUTES),
        "disks": Param(dict, required=False, default=None),
    },
)
def settings_set_spindown(ctx: Context, default: int, disks: dict | None):
    """Save spindown times (minutes, 0 = never) and apply them now."""
    overrides = {}
    for serial, minutes in (disks or {}).items():
        if not isinstance(serial, str) or not SERIAL_RE.match(serial):
            raise AgentError(INVALID_PARAMS, "'disks' has an invalid serial")
        if type(minutes) is not int or minutes not in DISK_SPINDOWN:
            raise AgentError(INVALID_PARAMS, f"'disks.{serial}' must be one of: "
                             + ", ".join(map(str, DISK_SPINDOWN)))
        if minutes >= 0:
            overrides[serial] = minutes
    write_file(spindown_file(ctx),
               json.dumps({"default": default, "disks": overrides}, indent=2) + "\n")
    return apply_spindown(ctx)


def spindown_main() -> int:
    """dahlia-spindown: apply the saved spindown times (boot, hotplug)."""
    result = apply_spindown(Context())
    for item in result["applied"]:
        print(f"{item['path']}: {item['minutes']} min")
    for item in result["failed"]:
        print(f"{item['path']}: {item['error']}")
    return 0


# --- schedules ----------------------------------------------------------------

SCHEDULE_TIMERS = {
    "mover": "dahlia-mover.timer",
    "snapraid-sync": "dahlia-snapraid-sync.timer",
    "snapraid-scrub": "dahlia-snapraid-scrub.timer",
}
FREQUENCIES = ("off", "hourly", "daily", "weekly", "monthly")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
# Same times as the packaged timers.
DEFAULT_SCHEDULES = {
    "mover": {"frequency": "daily", "time": "03:00", "weekday": "sun", "monthday": 1},
    "snapraid-sync": {"frequency": "daily", "time": "04:30", "weekday": "sun", "monthday": 1},
    "snapraid-scrub": {"frequency": "weekly", "time": "06:00", "weekday": "sun", "monthday": 1},
}


def schedules_file(ctx: Context) -> Path:
    return ctx.etc / "dahlia/schedules.json"


def on_calendar(s: dict) -> str:
    hour, minute = s["time"].split(":")
    if s["frequency"] == "hourly":
        return f"*-*-* *:{minute}:00"
    if s["frequency"] == "weekly":
        return f"{s['weekday'].capitalize()} *-*-* {hour}:{minute}:00"
    if s["frequency"] == "monthly":
        return f"*-*-{s['monthday']:02d} {hour}:{minute}:00"
    return f"*-*-* {hour}:{minute}:00"


def saved_schedules(ctx: Context) -> dict:
    data = read_json(schedules_file(ctx), {})
    out = {}
    for job, default in DEFAULT_SCHEDULES.items():
        s = data.get(job) if isinstance(data.get(job), dict) else {}
        out[job] = {
            "frequency": s.get("frequency") if s.get("frequency") in FREQUENCIES
            else default["frequency"],
            "time": s.get("time") if isinstance(s.get("time"), str) and TIME_RE.match(s["time"])
            else default["time"],
            "weekday": s.get("weekday") if s.get("weekday") in WEEKDAYS else default["weekday"],
            "monthday": s.get("monthday") if type(s.get("monthday")) is int
            and 1 <= s["monthday"] <= 28 else default["monthday"],
        }
    return out


@action("settings.schedules", mutating=False)
def settings_schedules(ctx: Context):
    """When mover, SnapRAID sync and scrub run, and their next run."""
    out = {}
    for job, s in saved_schedules(ctx).items():
        props = show_props(ctx, ["systemctl", "show", SCHEDULE_TIMERS[job],
                                 "-p", "NextElapseUSecRealtime", "-p", "UnitFileState"])
        out[job] = {**s, "next": props.get("NextElapseUSecRealtime", ""),
                    "enabled": props.get("UnitFileState") == "enabled"}
    return {"schedules": out}


@action(
    "settings.set_schedule",
    mutating=True,
    params={
        "job": Param(str, choices=tuple(SCHEDULE_TIMERS)),
        "frequency": Param(str, choices=FREQUENCIES),
        "time": Param(str, required=False, default="03:00", pattern=TIME_RE),
        "weekday": Param(str, required=False, default="sun", choices=WEEKDAYS),
        "monthday": Param(int, required=False, default=1, minimum=1, maximum=28),
    },
)
def settings_set_schedule(ctx: Context, job: str, frequency: str, time: str, weekday: str,
                          monthday: int):
    """Change when a job runs, or switch its timer off."""
    s = {"frequency": frequency, "time": time, "weekday": weekday, "monthday": monthday}
    timer = SCHEDULE_TIMERS[job]
    if frequency == "off":
        ctx.runner.run(["systemctl", "disable", "--now", timer])
    else:
        dropin = ctx.etc / f"systemd/system/{timer}.d/dahlia-schedule.conf"
        # The empty OnCalendar= clears the packaged schedule.
        write_file(dropin, f"[Timer]\nOnCalendar=\nOnCalendar={on_calendar(s)}\n")
        ctx.runner.run(["systemctl", "daemon-reload"])
        ctx.runner.run(["systemctl", "enable", timer])
        ctx.runner.run(["systemctl", "restart", timer])
    data = read_json(schedules_file(ctx), {})
    data[job] = s
    write_file(schedules_file(ctx), json.dumps(data, indent=2, sort_keys=True) + "\n")
    return {"job": job, **s}
