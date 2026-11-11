"""Notifications: e-mail (SMTP), ntfy and Gotify, plus the checks that send them.

dahlia-notify runs the checks from a timer (SMART, temperatures, disk usage),
reports failed jobs through OnFailure= of the job units and is the NOTIFYCMD
of the UPS monitor. Each problem is sent once when it starts and once when
it is gone; the state lives in /var/lib/dahlia/notify-state.json.
"""

import argparse
import json
import re
import smtplib
import ssl
import sys
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from .actions import Context, Param, action
from .protocol import FAILED, INVALID_PARAMS, AgentError
from .settings import read_json, write_file

CHANNELS = ("email", "ntfy", "gotify")
EVENTS = ("smart", "temperature", "usage", "jobs", "ups", "updates")
LEVELS = ("info", "warning", "critical")
HISTORY_MAX = 100
# Disk temperature (°C) that counts as a problem; SSDs run warmer.
TEMP_LIMIT = {"hdd": 55, "ssd": 70}
USAGE_LIMIT = 90  # percent
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,252}[A-Za-z0-9])?$")
EMAIL_RE = re.compile(r"^[^@\s,;<>]{1,64}@[A-Za-z0-9.-]{1,253}$")
URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%/-]*)?$")
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SECRET_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,256}$")
USER_RE = re.compile(r"^[^\x00-\x1f\x7f\s]{0,128}$")

DEFAULTS = {
    "email": {"enabled": False, "host": "", "port": 587, "security": "starttls", "user": "",
              "password": "", "sender": "", "to": ""},
    "ntfy": {"enabled": False, "url": "https://ntfy.sh", "topic": "", "token": ""},
    "gotify": {"enabled": False, "url": "", "token": ""},
    "events": {e: True for e in EVENTS},
}
SECRETS = {"email": "password", "ntfy": "token", "gotify": "token"}


def config_file(ctx: Context) -> Path:
    return ctx.etc / "dahlia/notify.json"


def history_file(ctx: Context) -> Path:
    return ctx.var / "notifications.json"


def state_file(ctx: Context) -> Path:
    return ctx.var / "notify-state.json"


def load_config(ctx: Context) -> dict:
    data = read_json(config_file(ctx), {})
    cfg = {}
    for key, default in DEFAULTS.items():
        section = data.get(key) if isinstance(data.get(key), dict) else {}
        cfg[key] = {k: section.get(k, v) if type(section.get(k, v)) is type(v) else v
                    for k, v in default.items()}
    return cfg


def public_config(cfg: dict) -> dict:
    """The config without secrets; the UI only learns whether one is set."""
    out = json.loads(json.dumps(cfg))
    for channel, field in SECRETS.items():
        out[channel][f"{field}_set"] = bool(out[channel].pop(field))
    return out


# --- sending ------------------------------------------------------------------


def send_email(c: dict, hostname: str, level: str, title: str, message: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"[{hostname}] {title}"
    msg["From"] = c["sender"]
    msg["To"] = c["to"]
    msg.set_content(f"{message}\n\n-- \nDahlia on {hostname}, level {level}\n")
    context = ssl.create_default_context()
    if c["security"] == "tls":
        smtp = smtplib.SMTP_SSL(c["host"], c["port"], timeout=20, context=context)
    else:
        smtp = smtplib.SMTP(c["host"], c["port"], timeout=20)
    with smtp:
        if c["security"] == "starttls":
            smtp.starttls(context=context)
        if c["user"]:
            smtp.login(c["user"], c["password"])
        smtp.send_message(msg)


def http_post(url: str, data: bytes, headers: dict) -> None:
    # URL_RE allows http and https only.
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=20) as res:  # noqa: S310
        res.read()


def send_ntfy(c: dict, hostname: str, level: str, title: str, message: str) -> None:
    # JSON publishing keeps UTF-8 titles intact (HTTP headers are Latin-1).
    body = json.dumps({
        "topic": c["topic"], "title": f"{hostname}: {title}", "message": message,
        "priority": {"info": 3, "warning": 4, "critical": 5}[level],
        "tags": [{"info": "information_source", "warning": "warning",
                  "critical": "rotating_light"}[level]],
    }).encode()
    headers = {"Content-Type": "application/json"}
    if c["token"]:
        headers["Authorization"] = f"Bearer {c['token']}"
    http_post(c["url"].rstrip("/"), body, headers)


def send_gotify(c: dict, hostname: str, level: str, title: str, message: str) -> None:
    body = json.dumps({"title": f"{hostname}: {title}", "message": message,
                       "priority": {"info": 4, "warning": 7, "critical": 9}[level]}).encode()
    http_post(f"{c['url'].rstrip('/')}/message", body,
              {"Content-Type": "application/json", "X-Gotify-Key": c["token"]})


SENDERS = {"email": send_email, "ntfy": send_ntfy, "gotify": send_gotify}


def send(ctx: Context, level: str, title: str, message: str, event: str | None = None) -> dict:
    """Send to every enabled channel and keep it in the history."""
    cfg = load_config(ctx)
    if event and not cfg["events"].get(event, True):
        return {}
    hostname = ctx.hostname()
    results = {}
    for channel in CHANNELS:
        c = cfg[channel]
        if not c["enabled"]:
            continue
        try:
            (ctx.senders or SENDERS)[channel](c, hostname, level, title, message)
            results[channel] = "ok"
        except Exception as e:  # noqa: BLE001 (every channel failure is reported, not raised)
            results[channel] = f"{type(e).__name__}: {e}"[:200]
    history = read_json(history_file(ctx), {}).get("items", [])
    history.insert(0, {"time": int(ctx.clock()), "level": level, "title": title,
                       "message": message, "results": results})
    write_file(history_file(ctx), json.dumps({"items": history[:HISTORY_MAX]}) + "\n", 0o640)
    return results


# --- checks -------------------------------------------------------------------


def current_problems(ctx: Context) -> dict[str, tuple[str, str, str, str]]:
    """key -> (event, level, title, message) for everything wrong right now."""
    from . import actions, hardware

    problems = {}
    for d in hardware.list_disks(ctx):
        s = hardware.read_smart(ctx, d["path"])
        name = f"{d['path']} ({s.get('model') or d.get('transport') or 'disk'})"
        if s["health"] in ("warning", "failed"):
            level = "critical" if s["health"] == "failed" else "warning"
            detail = ", ".join(f"{k}={v}" for k, v in s.get("warnings", {}).items())
            problems[f"smart:{d['path']}"] = (
                "smart", level, f"SMART {s['health']}: {name}",
                f"SMART reports {s['health']} for {name}. {detail}".strip())
        temp = s.get("temperature")
        limit = TEMP_LIMIT["hdd" if d["rotational"] else "ssd"]
        if temp is not None and temp >= limit:
            problems[f"temp:{d['path']}"] = (
                "temperature", "warning", f"Disk hot: {name} {temp} °C",
                f"{name} is at {temp} °C (limit {limit} °C).")
    for fs in actions.read_filesystems(ctx.proc):
        if not fs["size"]:
            continue
        pct = round(100 * fs["used"] / fs["size"])
        if pct >= USAGE_LIMIT:
            problems[f"usage:{fs['mountpoint']}"] = (
                "usage", "critical" if pct >= 97 else "warning",
                f"{fs['mountpoint']} {pct} % full",
                f"{fs['mountpoint']} is {pct} % full ({fs['free'] // 1024**3} GiB free).")
    from . import unattended

    pkgs = unattended.reboot_required(ctx)
    # With the automatic reboot on, unattended-upgrades restarts the server itself.
    if pkgs is not None and not (unattended.enabled(ctx) and unattended.load(ctx)["reboot"]):
        which = f" ({', '.join(pkgs)})" if pkgs else ""
        problems["reboot:required"] = (
            "updates", "info", "Restart needed",
            f"Installed updates{which} take effect after a restart of the server.")
    return problems


def check(ctx: Context) -> list[str]:
    """Send new problems and resolved ones. Returns the keys that were sent."""
    old = read_json(state_file(ctx), {})
    now = current_problems(ctx)
    sent = []
    for key, (event, level, title, message) in now.items():
        if old.get(key) != level:
            send(ctx, level, title, message, event)
            sent.append(key)
    for key in old:
        if key == "reboot:required":
            continue  # the restart itself is the resolution, nothing to report
        if key not in now:
            event = {"smart": "smart", "temp": "temperature", "usage": "usage"}.get(
                key.split(":", 1)[0], "smart")
            send(ctx, "info", f"Resolved: {key.split(':', 1)[1]}",
                 f"The {event} problem on {key.split(':', 1)[1]} is gone.", event)
            sent.append(key)
    write_file(state_file(ctx), json.dumps({k: v[1] for k, v in now.items()}) + "\n", 0o640)
    return sent


JOB_NAMES = {
    "dahlia-mover.service": "Mover",
    "dahlia-snapraid-sync.service": "SnapRAID sync",
    "dahlia-snapraid-scrub.service": "SnapRAID scrub",
    "dahlia-parity.service": "Parity repair",
    "dahlia-update.service": "System update",
}


def job_failed(ctx: Context, unit: str) -> None:
    name = JOB_NAMES.get(unit, unit)
    log = ctx.runner.run(["journalctl", "-u", unit, "-n", "15", "--no-pager", "-q", "-o", "cat"],
                         check=False).stdout.strip()
    send(ctx, "warning", f"{name} failed", f"{name} ({unit}) failed.\n\n{log}"[:3000], "jobs")


# --- actions ------------------------------------------------------------------


def check_channel(channel: str, s: dict, old: dict) -> dict:
    """Validated settings of one channel; an empty secret keeps the old one."""
    if not isinstance(s, dict):
        raise AgentError(INVALID_PARAMS, "'settings' must be an object")
    out = dict(DEFAULTS[channel])
    unknown = set(s) - set(out)
    if unknown:
        raise AgentError(INVALID_PARAMS, f"unknown settings: {', '.join(sorted(unknown))}")
    for k, v in s.items():
        if type(v) is not type(out[k]):
            raise AgentError(INVALID_PARAMS, f"'{k}' has the wrong type")
        out[k] = v
    secret = SECRETS[channel]
    if not out[secret]:
        out[secret] = old.get(secret, "")
    checks = {
        "host": HOST_RE, "user": USER_RE, "password": SECRET_RE, "token": SECRET_RE,
        "url": URL_RE, "topic": TOPIC_RE,
    }
    for k, pattern in checks.items():
        if k in out and out[k] and not pattern.match(out[k]):
            raise AgentError(INVALID_PARAMS, f"'{k}' has an invalid format")
    if channel == "email":
        if out["security"] not in ("starttls", "tls", "none"):
            raise AgentError(INVALID_PARAMS, "'security' must be starttls, tls or none")
        if not 1 <= out["port"] <= 65535:
            raise AgentError(INVALID_PARAMS, "'port' must be between 1 and 65535")
        for k in ("sender", "to"):
            if out[k] and not EMAIL_RE.match(out[k]):
                raise AgentError(INVALID_PARAMS, f"'{k}' is not an e-mail address")
        needed = ("host", "sender", "to")
    elif channel == "ntfy":
        needed = ("url", "topic")
    else:
        needed = ("url", "token")
    if out["enabled"]:
        missing = [k for k in needed if not out[k]]
        if missing:
            raise AgentError(INVALID_PARAMS, f"missing: {', '.join(missing)}")
    return out


@action("notify.get", mutating=False)
def notify_get(ctx: Context):
    """Channels (without secrets), event switches and the last notifications."""
    history = read_json(history_file(ctx), {}).get("items", [])
    return {**public_config(load_config(ctx)), "history": history[:30]}


@action(
    "notify.set",
    mutating=True,
    params={"channel": Param(str, choices=CHANNELS), "settings": Param(dict)},
)
def notify_set(ctx: Context, channel: str, settings: dict):
    """Save the settings of one channel."""
    cfg = load_config(ctx)
    cfg[channel] = check_channel(channel, settings, cfg[channel])
    write_file(config_file(ctx), json.dumps(cfg, indent=2) + "\n", 0o600)
    return public_config(cfg)[channel]


@action("notify.set_events", mutating=True, params={"events": Param(dict)})
def notify_set_events(ctx: Context, events: dict):
    """Switch notification types on or off."""
    cfg = load_config(ctx)
    for k, v in events.items():
        if k not in EVENTS or type(v) is not bool:
            raise AgentError(INVALID_PARAMS, f"'events.{k}' is not a known switch")
        cfg["events"][k] = v
    write_file(config_file(ctx), json.dumps(cfg, indent=2) + "\n", 0o600)
    return cfg["events"]


@action("notify.test", mutating=True)
def notify_test(ctx: Context):
    """Send a test message to every enabled channel."""
    results = send(ctx, "info", "Test", "Test message from the Dahlia dashboard.")
    if not results:
        raise AgentError(FAILED, "no notification channel is enabled")
    return {"results": results}


# --- dahlia-notify ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from . import ups

    parser = argparse.ArgumentParser(prog="dahlia-notify")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check disks and usage, send changes")
    j = sub.add_parser("job-failed", help="report a failed unit (OnFailure=)")
    j.add_argument("unit")
    u = sub.add_parser("ups-event", help="NOTIFYCMD of upsmon")
    u.add_argument("message", nargs="?", default="")
    s = sub.add_parser("send", help="send a message")
    s.add_argument("--level", choices=LEVELS, default="info")
    s.add_argument("title")
    s.add_argument("message")
    args = parser.parse_args(argv)
    ctx = Context()
    if args.cmd == "check":
        for key in check(ctx):
            print(f"sent: {key}")
    elif args.cmd == "job-failed":
        if not re.fullmatch(r"[A-Za-z0-9@._-]{1,128}", args.unit):
            parser.error("invalid unit name")
        job_failed(ctx, args.unit)
    elif args.cmd == "ups-event":
        ups.handle_event(ctx, ups.event_type(), args.message)
    else:
        print(json.dumps(send(ctx, args.level, args.title, args.message)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

