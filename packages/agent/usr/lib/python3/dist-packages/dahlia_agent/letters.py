"""Weekly letter and year review: what the server did, in numbers.

letters.timer runs "letters run" every morning. On Mondays it writes the
letter of the past week (Monday to Sunday), on 1 January the review of the
past year. A letter holds numbers only; the dashboard and the desktop app
turn them into sentences in the reader's language. Each new letter is also
sent as a short notification (event "letters").

Sources: the history database (CPU, temperature, network), the data pool
or /srv (space used), backup-runs.json, backup-probe.json, the apt history
log, the installed apps and the notification history. Space added is the
difference to the previous letter, so the first letter has none.

    /var/lib/dahlia/letters/<year>-W<week>.json   weekly letter
    /var/lib/dahlia/letters/<year>.json           year review
"""

import argparse
import contextlib
import datetime
import gzip
import json
import re
import sqlite3
import sys
from pathlib import Path

from . import apps, history
from .actions import Context, Param, action, read_filesystems
from .protocol import INVALID_PARAMS, AgentError
from .settings import read_json, write_file

ID_RE = re.compile(r"^(?:[0-9]{4}-W[0-9]{2}|[0-9]{4})$")
# Interfaces whose traffic is internal: loopback, Docker, Incus, VPN tunnels.
VIRTUAL_RE = re.compile(r"^(lo|docker|veth|br-|incusbr|tap|vnet|wg|tailscale|virbr)")
UPGRADE_RE = re.compile(r"[^\s,]+ \([^)]*\)")
KEPT = 120
UTC = datetime.UTC


def letters_dir(ctx: Context) -> Path:
    return ctx.var / "letters"


def week_id(day: datetime.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_range(day: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """Monday 00:00 to the next Monday 00:00 (local time) of the week of day."""
    monday = day - datetime.timedelta(days=day.weekday())
    start = datetime.datetime.combine(monday, datetime.time()).astimezone()
    return start, start + datetime.timedelta(days=7)


# --- sources ----------------------------------------------------------------------


def history_numbers(ctx: Context, start: int, end: int) -> dict:
    """Average CPU, highest CPU temperature and LAN traffic from the hour rows."""
    path = history.db_file(ctx)
    if not path.is_file():
        return {}
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        rows = db.execute("SELECT key, value FROM samples WHERE res = 3600 AND ts >= ? AND ts < ?",
                          (start, end)).fetchall()
        db.close()
    except sqlite3.Error:
        return {}
    cpu = [v for k, v in rows if k == "cpu"]
    temp = [v for k, v in rows if k == "temp_cpu"]
    rx = tx = 0.0
    for key, value in rows:
        kind, _, name = key.partition(":")
        if kind in ("net_rx", "net_tx") and name and not VIRTUAL_RE.match(name):
            if kind == "net_rx":
                rx += value * 3600
            else:
                tx += value * 3600
    out = {}
    if cpu:
        out["cpu_avg"] = round(sum(cpu) / len(cpu), 1)
    if temp:
        out["temp_max"] = round(max(temp), 1)
    if rows:
        out["traffic"] = {"rx": int(rx), "tx": int(tx)}
    return out


def storage_numbers(ctx: Context) -> dict | None:
    """Space of the data pool, or of the filesystem holding /srv without one."""
    found = {fs["mountpoint"]: fs for fs in read_filesystems(ctx.proc)}
    fs = found.get(str(ctx.pool)) or found.get("/")
    if fs is None:
        return None
    return {"mount": fs["mountpoint"], "used": fs["used"], "size": fs["size"]}


def _stamp(text: str | None) -> float | None:
    if not isinstance(text, str):
        return None
    with contextlib.suppress(ValueError):
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    return None


def backup_numbers(ctx: Context, start: int, end: int) -> dict:
    runs = read_json(ctx.var / "backup-runs.json", {}).get("runs", [])
    runs = [r for r in runs if isinstance(r, dict)
            and (t := _stamp(r.get("time"))) is not None and start <= t < end]
    out = {"runs": len(runs), "ok": sum(r.get("result") == "success" for r in runs),
           "added": sum(r.get("added") or 0 for r in runs if isinstance(r.get("added"), int))}
    probe = read_json(ctx.var / "backup-probe.json", {})
    t = _stamp(probe.get("started"))
    if t is not None and start <= t < end:
        out["probe"] = probe.get("result")
    return out


def apt_logs(ctx: Context) -> list[str]:
    out = []
    for path in sorted((ctx.log_dir / "apt").glob("history.log*")):
        try:
            if path.suffix == ".gz":
                with gzip.open(path, "rt", errors="replace") as f:
                    out.append(f.read())
            else:
                out.append(path.read_text(errors="replace"))
        except OSError:
            continue
    return out


def upgraded_packages(ctx: Context, start: int, end: int) -> int:
    """Packages apt upgraded between start and end."""
    count = 0
    for text in apt_logs(ctx):
        when = None
        for line in text.splitlines():
            if line.startswith("Start-Date:"):
                with contextlib.suppress(ValueError):
                    when = datetime.datetime.strptime(
                        " ".join(line.split()[1:3]), "%Y-%m-%d %H:%M:%S").astimezone().timestamp()
            elif line.startswith("Upgrade:") and when is not None and start <= when < end:
                count += len(UPGRADE_RE.findall(line))
    return count


def installed_apps(ctx: Context) -> dict[str, dict]:
    paths = apps.Paths(apps=ctx.var / "apps", pool=ctx.pool, system=ctx.system)
    out = {}
    for app_id in apps.installed(paths):
        with contextlib.suppress(apps.AppError, OSError):
            t, _ = apps.load_installed(paths, app_id)
            out[app_id] = {"name": t.name, "version": t.version}
    return out


def notice_numbers(ctx: Context, start: int, end: int) -> dict:
    items = [i for i in read_json(ctx.var / "notifications.json", {}).get("items", [])
             if isinstance(i, dict) and isinstance(i.get("time"), int) and start <= i["time"] < end
             and i.get("title") != "Weekly letter"]
    rank = {"critical": 0, "warning": 1, "info": 2}
    worst = sorted((i for i in items if i.get("level") in ("warning", "critical")),
                   key=lambda i: (rank[i["level"]], -i["time"]))
    return {"warning": sum(i.get("level") == "warning" for i in items),
            "critical": sum(i.get("level") == "critical" for i in items),
            "titles": [str(i.get("title", ""))[:120] for i in worst[:3]]}


def uptime_seconds(ctx: Context) -> int:
    try:
        return int(float((ctx.proc / "uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


# --- letters ----------------------------------------------------------------------


def load(ctx: Context, letter_id: str) -> dict | None:
    if not ID_RE.fullmatch(letter_id):
        return None
    data = read_json(letters_dir(ctx) / f"{letter_id}.json", {})
    return data or None


def weekly_ids(ctx: Context) -> list[str]:
    d = letters_dir(ctx)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*-W*.json") if ID_RE.fullmatch(p.stem))


def previous_week(ctx: Context, before: str) -> dict | None:
    older = [i for i in weekly_ids(ctx) if i < before]
    return load(ctx, older[-1]) if older else None


def build_week(ctx: Context, day: datetime.date, now: float | None = None) -> dict:
    """The letter of the week of day; for the running week up to now."""
    start, end = week_range(day)
    s, e = int(start.timestamp()), int(end.timestamp())
    if now is not None:
        e = min(e, int(now))
    letter_id = week_id(day)
    before = previous_week(ctx, letter_id)
    current = installed_apps(ctx)
    old_apps = (before or {}).get("apps", {}).get("versions")
    storage = storage_numbers(ctx)
    if storage and before and (before.get("storage") or {}).get("mount") == storage["mount"]:
        storage["added"] = storage["used"] - before["storage"]["used"]
    letter = {
        "kind": "week", "id": letter_id, "from": s, "to": e,
        "written": int(ctx.clock()),
        "uptime": uptime_seconds(ctx),
        **history_numbers(ctx, s, e),
        "storage": storage,
        "backups": backup_numbers(ctx, s, e),
        "updates": upgraded_packages(ctx, s, e),
        "apps": {
            "count": len(current),
            "versions": {k: v["version"] for k, v in current.items()},
            "new": [] if old_apps is None else
            [current[k]["name"] for k in sorted(current) if k not in old_apps],
            "updated": [] if old_apps is None else
            [current[k]["name"] for k in sorted(current)
             if k in old_apps and old_apps[k] != current[k]["version"]],
        },
        "notices": notice_numbers(ctx, s, e),
    }
    from . import tiles

    photos = tiles.photo_total(ctx)
    if photos is not None:
        letter["photo_total"] = photos
        old = (before or {}).get("photo_total")
        if isinstance(old, int) and photos > old:
            letter["photos"] = photos - old
    return letter


def build_year(ctx: Context, year: int) -> dict:
    """Sum of the weekly letters of a year (ISO weeks of that year)."""
    weeks = [w for w in (load(ctx, i) for i in weekly_ids(ctx) if i.startswith(f"{year}-W")) if w]
    out = {"kind": "year", "id": str(year), "weeks": len(weeks), "written": int(ctx.clock()),
           "from": weeks[0]["from"] if weeks else None, "to": weeks[-1]["to"] if weeks else None}
    if not weeks:
        return out
    out["traffic"] = {k: sum((w.get("traffic") or {}).get(k, 0) for w in weeks)
                      for k in ("rx", "tx")}
    temps = [w["temp_max"] for w in weeks if isinstance(w.get("temp_max"), (int, float))]
    if temps:
        out["temp_max"] = max(temps)
    cpus = [w["cpu_avg"] for w in weeks if isinstance(w.get("cpu_avg"), (int, float))]
    if cpus:
        out["cpu_avg"] = round(sum(cpus) / len(cpus), 1)
    out["backups"] = {k: sum((w.get("backups") or {}).get(k, 0) for w in weeks)
                      for k in ("runs", "ok", "added")}
    out["updates"] = sum(w.get("updates", 0) for w in weeks)
    photos = sum(w.get("photos", 0) for w in weeks)
    if photos:
        out["photos"] = photos
    out["uptime_max"] = max(w.get("uptime", 0) for w in weeks)
    first = next((w["storage"] for w in weeks if w.get("storage")), None)
    last = next((w["storage"] for w in reversed(weeks) if w.get("storage")), None)
    if first and last:
        out["storage"] = {"used": last["used"], "size": last["size"],
                          "added": last["used"] - first["used"]}
    new, updated = [], []
    for w in weeks:
        a = w.get("apps") or {}
        new += [n for n in a.get("new", []) if n not in new]
        updated += [n for n in a.get("updated", []) if n not in updated]
    out["apps"] = {"count": (weeks[-1].get("apps") or {}).get("count", 0), "new": new,
                   "updated": updated}
    out["notices"] = {k: sum((w.get("notices") or {}).get(k, 0) for w in weeks)
                      for k in ("warning", "critical")}
    return out


def save(ctx: Context, letter: dict) -> None:
    write_file(letters_dir(ctx) / f"{letter['id']}.json", json.dumps(letter) + "\n")
    # Only the newest letters stay.
    for old in weekly_ids(ctx)[:-KEPT]:
        (letters_dir(ctx) / f"{old}.json").unlink(missing_ok=True)


def summary(letter: dict) -> str:
    """Short English text for the notification channels (e-mail, ntfy)."""
    def size(n: int) -> str:
        for unit in ("B", "kB", "MB", "GB", "TB"):
            if abs(n) < 1000 or unit == "TB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1000
        return str(n)

    lines = []
    st = letter.get("storage") or {}
    if "added" in st:
        lines.append(f"Data: {'+' if st['added'] >= 0 else ''}{size(st['added'])} "
                     f"({size(st['used'])} of {size(st['size'])} used)")
    b = letter.get("backups") or {}
    if b.get("runs"):
        lines.append(f"Backups: {b['ok']} of {b['runs']} succeeded")
    if letter.get("updates"):
        lines.append(f"Updates: {letter['updates']} packages")
    a = letter.get("apps") or {}
    if a.get("new"):
        lines.append(f"New apps: {', '.join(a['new'])}")
    n = letter.get("notices") or {}
    if n.get("warning") or n.get("critical"):
        lines.append(f"Warnings: {n.get('warning', 0)}, critical: {n.get('critical', 0)}")
    t = letter.get("traffic")
    if t:
        lines.append(f"Network: {size(t['rx'])} in, {size(t['tx'])} out")
    return "\n".join(lines) or "A quiet time: nothing to report."


def run_daily(ctx: Context, today: datetime.date | None = None) -> list[str]:
    """Letters due today: last week's on Monday, last year's on 1 January.
    Missed ones (server off on the day) are written on the next run."""
    from . import notify

    today = today or datetime.date.fromtimestamp(ctx.clock())
    written = []
    last_week = today - datetime.timedelta(days=today.weekday() + 7)
    if load(ctx, week_id(last_week)) is None:
        letter = build_week(ctx, last_week)
        save(ctx, letter)
        notify.send(ctx, "info", "Weekly letter", summary(letter), "letters")
        written.append(letter["id"])
    year = today.year - 1
    if load(ctx, str(year)) is None and any(i.startswith(f"{year}-W") for i in weekly_ids(ctx)):
        review = build_year(ctx, year)
        save(ctx, review)
        notify.send(ctx, "info", f"Year {year} in review", summary(review), "letters")
        written.append(review["id"])
    return written


# --- actions ----------------------------------------------------------------------


def brief(letter: dict) -> dict:
    return {k: letter.get(k) for k in ("kind", "id", "from", "to")}


@action("letters.list", mutating=False)
def letters_list(ctx: Context):
    """Saved letters and year reviews, newest first, without their numbers."""
    d = letters_dir(ctx)
    ids = sorted((p.stem for p in d.glob("*.json") if ID_RE.fullmatch(p.stem)), reverse=True) \
        if d.is_dir() else []
    out = [brief(x) for x in (load(ctx, i) for i in ids) if x]
    out.sort(key=lambda x: x.get("to") or 0, reverse=True)
    return {"letters": out}


@action("letters.get", mutating=False, params={"id": Param(str, pattern=ID_RE)})
def letters_get(ctx: Context, id: str):
    """One saved letter or year review."""
    letter = load(ctx, id)
    if letter is None:
        raise AgentError(INVALID_PARAMS, f"no letter {id}")
    return letter


@action("letters.now", mutating=False, params={"kind": Param(str, choices=("week", "year"))})
def letters_now(ctx: Context, kind: str):
    """The running week or year so far, not saved."""
    today = datetime.date.fromtimestamp(ctx.clock())
    if kind == "week":
        return {**build_week(ctx, today, now=ctx.clock()), "preview": True}
    review = build_year(ctx, today.year)
    return {**review, "preview": True}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="letters", description="Dahlia weekly letter and year review.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="write the letters due today (letters.timer)")
    s = sub.add_parser("show", help="print a letter as JSON")
    s.add_argument("id", help="YYYY-Www, YYYY, week (running) or year (running)")
    args = p.parse_args(argv)
    ctx = Context()
    if args.command == "run":
        for letter_id in run_daily(ctx):
            print(f"letters: wrote {letter_id}")
        return 0
    if args.id in ("week", "year"):
        print(json.dumps(letters_now(ctx, args.id), indent=2))
        return 0
    letter = load(ctx, args.id)
    if letter is None:
        print(f"letters: no letter {args.id}", file=sys.stderr)
        return 1
    print(json.dumps(letter, indent=2))
    return 0
