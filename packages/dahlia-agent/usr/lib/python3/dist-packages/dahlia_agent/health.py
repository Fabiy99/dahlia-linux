"""Health check: one pass over disks, array, parity, services, time, updates,
backup, notifications and firewall.

Each finding has an id, a level (critical, warning, info) and parameters; the
dashboard turns the id into a text with a hint what to do (locales health.*).
Checks that found nothing are listed under "passed". A check that cannot run
(tool missing, command failed) is skipped, not reported as a problem.
"""

import datetime
import json

from .actions import Context, action

LEVELS = ("critical", "warning", "info")
CPU_TEMP_LIMIT = 90  # °C
PARITY_MAX_AGE_DAYS = 8  # weekly sync plus a day
BACKUP_MAX_AGE_DAYS = 8


def finding(check: str, id_: str, level: str, **params) -> dict:
    return {"check": check, "id": id_, "level": level, "params": params}


def check_disks(ctx: Context) -> list[dict]:
    from . import hardware, notify

    out = []
    for d in hardware.list_disks(ctx):
        s = hardware.read_smart(ctx, d["path"])
        disk = d["path"]
        model = s.get("model") or ""
        if s["health"] == "failed":
            out.append(finding("disks", "smart_failed", "critical", disk=disk, model=model))
        elif s["health"] == "warning":
            detail = ", ".join(f"{k}={v}" for k, v in s.get("warnings", {}).items())
            out.append(finding("disks", "smart_warning", "warning", disk=disk, model=model,
                               detail=detail))
        last = (s.get("self_test") or {}).get("last") or {}
        if last.get("result") == "failed":
            out.append(finding("disks", "selftest_failed", "critical", disk=disk, model=model))
        temp = s.get("temperature")
        limit = notify.TEMP_LIMIT["hdd" if d["rotational"] else "ssd"]
        if temp is not None and temp >= limit:
            out.append(finding("disks", "disk_hot", "warning", disk=disk, temp=temp,
                               limit=limit))
    return out


def check_cpu(ctx: Context) -> list[dict]:
    from . import hardware

    temp = hardware.cpu_temperature(ctx.sys)
    if temp is not None and temp >= CPU_TEMP_LIMIT:
        return [finding("cpu", "cpu_hot", "warning", temp=round(temp), limit=CPU_TEMP_LIMIT)]
    return []


def check_usage(ctx: Context) -> list[dict]:
    from . import actions, notify

    out = []
    for fs in actions.read_filesystems(ctx.proc):
        if not fs["size"]:
            continue
        pct = round(100 * fs["used"] / fs["size"])
        if pct >= notify.USAGE_LIMIT:
            out.append(finding("usage", "usage_full", "critical" if pct >= 97 else "warning",
                               mountpoint=fs["mountpoint"], percent=pct,
                               free_gib=fs["free"] // 1024**3))
    return out


def parity_info(ctx: Context) -> dict | None:
    proc = ctx.runner.run(["dahlia-snapraid", "status", "--json"], timeout=600, check=False)
    if proc.returncode != 0:
        return None
    try:
        info = json.loads(proc.stdout or "")
    except ValueError:
        return None
    return info if isinstance(info, dict) else None


def check_array(ctx: Context, info: dict | None) -> list[dict]:
    if info is None:
        return []
    return [finding("array", "disk_missing", "critical", mountpoint=d["mountpoint"])
            for d in info.get("data", []) + info.get("parity", []) if not d.get("mounted")]


def check_parity(ctx: Context, info: dict | None) -> list[dict]:
    if info is None or not info.get("data"):
        return []  # no array, nothing to protect
    if not info.get("parity"):
        return [finding("parity", "parity_none", "info")]
    if not info.get("configured"):
        return [finding("parity", "parity_problem", "warning",
                        detail="; ".join(info.get("problems", [])))]
    out = []
    status = info.get("status") or {}
    if status.get("bad"):
        out.append(finding("parity", "parity_bad", "critical", blocks=status["bad"]))
    sync = (info.get("runs") or {}).get("sync")
    if not sync:
        out.append(finding("parity", "parity_never", "warning"))
        return out
    if sync.get("result") in ("failed", "blocked"):
        out.append(finding("parity", "parity_failed", "warning", result=sync["result"],
                           message=sync.get("message", "")))
    end = sync.get("end")
    if isinstance(end, int | float):
        days = int((ctx.clock() - end) // 86400)
        if days >= PARITY_MAX_AGE_DAYS:
            out.append(finding("parity", "parity_old", "warning", days=days))
    return out


def check_services(ctx: Context) -> list[dict]:
    proc = ctx.runner.run(["systemctl", "list-units", "--state=failed", "--plain",
                           "--no-legend", "--no-pager"], check=False)
    if proc.returncode != 0:
        return []
    units = [line.split()[0] for line in (proc.stdout or "").splitlines() if line.strip()]
    if units:
        return [finding("services", "units_failed", "warning", units=", ".join(units),
                        count=len(units))]
    return []


def check_time(ctx: Context) -> list[dict]:
    proc = ctx.runner.run(["timedatectl", "show", "-p", "NTP", "-p", "NTPSynchronized"],
                          check=False)
    if proc.returncode != 0:
        return []
    props = dict(line.partition("=")[::2] for line in (proc.stdout or "").splitlines())
    if props.get("NTP") == "yes" and props.get("NTPSynchronized") == "no":
        return [finding("time", "time_unsynced", "warning")]
    return []


def check_updates(ctx: Context) -> list[dict]:
    from . import unattended

    out = []
    auto = unattended.enabled(ctx)
    pkgs = unattended.reboot_required(ctx)
    # With the automatic reboot on, unattended-upgrades restarts the server itself.
    if pkgs is not None and not (auto and unattended.load(ctx)["reboot"]):
        out.append(finding("updates", "reboot_required", "info", packages=", ".join(pkgs)))
    if not auto:
        out.append(finding("updates", "auto_updates_off", "info"))
    return out


def parse_utc(value) -> float | None:
    try:
        return datetime.datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.UTC).timestamp()
    except ValueError:
        return None


def check_backup(ctx: Context) -> list[dict]:
    from . import backup

    b = backup.backup_of(ctx)
    if b.settings()["target"] is None:
        return [finding("backup", "backup_none", "info")]
    last = b.status()
    if not last:
        return [finding("backup", "backup_never", "warning")]
    out = []
    if last.get("result") != "success":
        out.append(finding("backup", "backup_failed", "warning", error=last.get("error", "")))
    when = parse_utc(last.get("finished") or last.get("started"))
    if when is not None:
        days = int((ctx.clock() - when) // 86400)
        if days >= BACKUP_MAX_AGE_DAYS:
            out.append(finding("backup", "backup_old", "warning", days=days))
    return out


def check_notify(ctx: Context) -> list[dict]:
    from . import notify

    cfg = notify.load_config(ctx)
    if not any(cfg[c]["enabled"] for c in notify.CHANNELS):
        return [finding("notify", "notify_none", "info")]
    return []


def check_firewall(ctx: Context) -> list[dict]:
    from . import firewall

    if not firewall.load_settings(ctx.etc / "dahlia/firewall.json")["enabled"]:
        return [finding("firewall", "firewall_off", "warning")]
    return []


CHECKS = ("disks", "cpu", "usage", "array", "parity", "services", "time", "updates", "backup",
          "notify", "firewall")


def run_checks(ctx: Context) -> dict:
    info = parity_info(ctx)
    funcs = {
        "disks": check_disks, "cpu": check_cpu, "usage": check_usage,
        "array": lambda c: check_array(c, info), "parity": lambda c: check_parity(c, info),
        "services": check_services, "time": check_time, "updates": check_updates,
        "backup": check_backup, "notify": check_notify, "firewall": check_firewall,
    }
    findings, passed, skipped = [], [], []
    for name in CHECKS:
        try:
            found = funcs[name](ctx)
        except Exception:  # noqa: BLE001 - one broken check must not hide the others
            skipped.append(name)
            continue
        if found:
            findings += found
        else:
            passed.append(name)
    findings.sort(key=lambda f: LEVELS.index(f["level"]))
    return {"checked": int(ctx.clock()), "findings": findings, "passed": passed,
            "skipped": skipped}


@action("system.health", mutating=False)
def system_health(ctx: Context):
    """Health check: findings (critical, warning, info) with parameters, passed checks."""
    return run_checks(ctx)
