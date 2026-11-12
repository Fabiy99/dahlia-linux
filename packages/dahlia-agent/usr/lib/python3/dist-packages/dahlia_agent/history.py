"""History of CPU, memory, network and CPU temperature, optional InfluxDB export.

dahlia-history (dahlia-history.service) takes a sample every minute and
stores it in /var/lib/dahlia/history.db (SQLite). Three resolutions are
kept: one minute for two days, 15 minutes for 35 days, one hour for 400
days. The coarse rows are averages of the minute rows, updated on every
sample. The database stays at a few MB.

With an InfluxDB target in /etc/dahlia/history.json (root, 0600) each
sample is also written there (line protocol, InfluxDB 1.x or 2.x). The
result of the last write is kept in /var/lib/dahlia/history-influx.json.
"""

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .actions import Context, Param, action, read_cpu, read_meminfo, read_network
from .hardware import cpu_temperature
from .protocol import FAILED, INVALID_PARAMS, AgentError
from .settings import read_json, write_file

INTERVAL = 60
# resolution (s) -> keep (s)
KEEP = {60: 2 * 86400, 900: 35 * 86400, 3600: 400 * 86400}
# range -> (resolution read, step returned)
RANGES = {
    "hour": (60, 60, 3600),
    "day": (60, 300, 86400),
    "week": (900, 1800, 7 * 86400),
    "month": (3600, 10800, 30 * 86400),
    "year": (3600, 86400, 365 * 86400),
}
URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%/-]*)?$")
NAME_RE = re.compile(r"^[A-Za-z0-9._ -]{1,64}$")
SECRET_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,512}$")
USER_RE = re.compile(r"^[^\x00-\x1f\x7f\s]{0,128}$")
TIMEOUT = 10

DEFAULTS = {"enabled": False, "url": "", "version": 2, "org": "", "bucket": "", "token": "",
            "database": "", "username": "", "password": ""}


def db_file(ctx: Context) -> Path:
    return ctx.var / "history.db"


def config_file(ctx: Context) -> Path:
    return ctx.etc / "dahlia/history.json"


def influx_state_file(ctx: Context) -> Path:
    return ctx.var / "history-influx.json"


def schema(db: sqlite3.Connection) -> sqlite3.Connection:
    db.execute("CREATE TABLE IF NOT EXISTS samples (res INTEGER, key TEXT, ts INTEGER, "
               "value REAL, n INTEGER, PRIMARY KEY (res, key, ts)) WITHOUT ROWID")
    return db


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return schema(sqlite3.connect(path, timeout=10))


# --- sampling -------------------------------------------------------------------


class Sampler:
    """Turns counters into rates; the first call only stores the counters."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.last: dict | None = None

    def read(self) -> dict:
        return {"time": self.ctx.clock(), "cpu": read_cpu(self.ctx.proc),
                "net": {i["name"]: (i["rx_bytes"], i["tx_bytes"])
                        for i in read_network(self.ctx.proc)}}

    def sample(self) -> dict[str, float]:
        now = self.read()
        last, self.last = self.last, now
        values: dict[str, float] = {}
        mem = read_meminfo(self.ctx.proc / "meminfo")
        if mem["total"] and mem["available"] is not None:
            values["mem"] = round(100 * (mem["total"] - mem["available"]) / mem["total"], 1)
        values["load"] = float((self.ctx.proc / "loadavg").read_text().split()[0])
        temp = cpu_temperature(self.ctx.sys)
        if temp is not None:
            values["temp_cpu"] = temp
        if last is None:
            return values
        total = now["cpu"]["total"] - last["cpu"]["total"]
        idle = now["cpu"]["idle"] - last["cpu"]["idle"]
        if total > 0:
            values["cpu"] = round(100 * (total - idle) / total, 1)
        dt = now["time"] - last["time"]
        if dt > 0:
            for name, (rx, tx) in now["net"].items():
                old = last["net"].get(name)
                # Counters restart when an interface comes back.
                if old and rx >= old[0] and tx >= old[1]:
                    values[f"net_rx:{name}"] = round((rx - old[0]) / dt)
                    values[f"net_tx:{name}"] = round((tx - old[1]) / dt)
        return values


def store(db: sqlite3.Connection, ts: int, values: dict[str, float]) -> None:
    """Minute row plus running averages in the 15-minute and hour rows."""
    with db:
        for key, value in values.items():
            db.execute("INSERT OR REPLACE INTO samples VALUES (60, ?, ?, ?, 1)",
                       (key, ts // 60 * 60, value))
            for res in (900, 3600):
                db.execute(
                    "INSERT INTO samples VALUES (?, ?, ?, ?, 1) ON CONFLICT (res, key, ts) "
                    "DO UPDATE SET value = (value * n + excluded.value) / (n + 1), n = n + 1",
                    (res, key, ts // res * res, value))
        for res, keep in KEEP.items():
            db.execute("DELETE FROM samples WHERE res = ? AND ts < ?", (res, ts - keep))


def query(db: sqlite3.Connection, range_: str, now: float) -> dict:
    res, step, span = RANGES[range_]
    start = (int(now) - span) // step * step
    rows = db.execute(
        "SELECT key, ts / ? * ? AS b, AVG(value) FROM samples WHERE res = ? AND ts >= ? "
        "GROUP BY key, b ORDER BY b", (step, step, res, start)).fetchall()
    stamps = list(range(start, int(now) // step * step + 1, step))
    index = {t: i for i, t in enumerate(stamps)}
    series: dict[str, list] = {}
    for key, bucket, value in rows:
        if bucket in index:
            series.setdefault(key, [None] * len(stamps))[index[bucket]] = round(value, 2)
    return {"range": range_, "step": step, "start": start, "points": len(stamps),
            "series": series}


# --- InfluxDB -------------------------------------------------------------------


def load_config(ctx: Context) -> dict:
    data = read_json(config_file(ctx), {})
    out = dict(DEFAULTS)
    for k, v in DEFAULTS.items():
        # type() so that True does not pass as an int.
        if type(data.get(k)) is type(v):
            out[k] = data[k]
    if out["version"] not in (1, 2):
        out["version"] = 2
    return out


def escape_tag(value: str) -> str:
    return re.sub(r"([,= ])", r"\\\1", value)


def line_protocol(host: str, ts: int, values: dict[str, float]) -> str:
    """One line for the system values, one per interface."""
    host = escape_tag(host)
    lines = []
    system = {k: v for k, v in values.items() if ":" not in k}
    if system:
        fields = ",".join(f"{k}={float(v)}" for k, v in sorted(system.items()))
        lines.append(f"dahlia,host={host} {fields} {ts}")
    ifaces = sorted({k.split(":", 1)[1] for k in values if ":" in k})
    for name in ifaces:
        fields = ",".join(f"{d}={float(values[f'net_{d}:{name}'])}" for d in ("rx", "tx")
                          if f"net_{d}:{name}" in values)
        lines.append(f"dahlia_net,host={host},interface={escape_tag(name)} {fields} {ts}")
    return "\n".join(lines) + "\n"


def write_target(c: dict) -> tuple[str, dict]:
    base = c["url"].rstrip("/")
    if c["version"] == 2:
        q = urllib.parse.urlencode({"org": c["org"], "bucket": c["bucket"], "precision": "s"})
        return f"{base}/api/v2/write?{q}", {"Authorization": f"Token {c['token']}"}
    params = {"db": c["database"], "precision": "s"}
    if c["username"]:
        params.update(u=c["username"], p=c["password"])
    return f"{base}/write?{urllib.parse.urlencode(params)}", {}


def http_post(url: str, data: bytes, headers: dict) -> None:
    # URL_RE allows http and https only.
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:  # noqa: S310
        res.read()


def influx_write(c: dict, body: str, post=None) -> None:
    url, headers = write_target(c)
    headers["Content-Type"] = "text/plain; charset=utf-8"
    try:
        (post or http_post)(url, body.encode(), headers)
    except OSError as e:  # URLError and HTTPError are OSErrors
        raise AgentError(FAILED, f"InfluxDB: {getattr(e, 'reason', None) or e}") from None


def export(ctx: Context, ts: int, values: dict[str, float], post=None) -> None:
    c = load_config(ctx)
    if not c["enabled"] or not values:
        return
    state = {"time": ts, "ok": True, "error": ""}
    try:
        influx_write(c, line_protocol(ctx.hostname(), ts, values), post)
    except AgentError as e:
        state = {"time": ts, "ok": False, "error": e.message}
    write_file(influx_state_file(ctx), json.dumps(state) + "\n")


# --- actions --------------------------------------------------------------------


@action("history.get", mutating=False,
        params={"range": Param(str, required=False, default="day", choices=tuple(RANGES))})
def history_get(ctx: Context, range: str):
    """Values of the last hour, day, week, month or year, averaged per step."""
    path = db_file(ctx)
    # No samples yet: same answer shape, all series empty.
    db = (sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) if path.exists()
          else schema(sqlite3.connect(":memory:")))
    try:
        return query(db, range, ctx.clock())
    finally:
        db.close()


def public(c: dict) -> dict:
    out = {k: v for k, v in c.items() if k not in ("token", "password")}
    out["has_token"] = bool(c["token"])
    out["has_password"] = bool(c["password"])
    return out


@action("history.influx", mutating=False)
def history_influx(ctx: Context):
    """InfluxDB settings without secrets, and the result of the last write."""
    return {"settings": public(load_config(ctx)),
            "last": read_json(influx_state_file(ctx), {}) or None}


@action(
    "history.set_influx",
    mutating=True,
    params={
        "enabled": Param(bool),
        "url": Param(str, required=False, default=""),
        "version": Param(int, required=False, default=2, choices=(1, 2)),
        "org": Param(str, required=False, default=""),
        "bucket": Param(str, required=False, default=""),
        "database": Param(str, required=False, default=""),
        "username": Param(str, required=False, default="", pattern=USER_RE),
        "token": Param(str, required=False, default=None, pattern=SECRET_RE),
        "password": Param(str, required=False, default=None, pattern=SECRET_RE),
    },
)
def history_set_influx(ctx: Context, enabled: bool, url: str, version: int, org: str,
                       bucket: str, database: str, username: str, token: str | None,
                       password: str | None):
    """Set the InfluxDB target. A test write must succeed before it is saved.

    token/password None keep the saved one.
    """
    old = load_config(ctx)
    c = {"enabled": enabled, "url": url.strip(), "version": version, "org": org.strip(),
         "bucket": bucket.strip(), "database": database.strip(), "username": username,
         "token": old["token"] if token is None else token,
         "password": old["password"] if password is None else password}
    if enabled:
        if not URL_RE.match(c["url"]):
            raise AgentError(INVALID_PARAMS, "'url' must be http(s)://host[:port][/path]")
        needed = ("org", "bucket", "token") if version == 2 else ("database",)
        for k in needed:
            if not c[k]:
                raise AgentError(INVALID_PARAMS, f"'{k}' is missing")
        for k in ("org", "bucket", "database"):
            if c[k] and not NAME_RE.match(c[k]):
                raise AgentError(INVALID_PARAMS, f"'{k}' contains characters that are not "
                                 "allowed")
        now = int(ctx.clock())
        influx_write(c, f"dahlia,host={escape_tag(ctx.hostname())} up=1i {now}\n",
                     ctx.senders.get("influx") if ctx.senders else None)
    write_file(config_file(ctx), json.dumps(c, indent=2) + "\n", mode=0o600)
    influx_state_file(ctx).unlink(missing_ok=True)
    return public(c)


# --- dahlia-history -------------------------------------------------------------


def run(ctx: Context, once: bool = False) -> None:
    sampler = Sampler(ctx)
    sampler.sample()  # counters only
    db = connect(db_file(ctx))
    while True:
        # Sleep to the next full minute so the rows line up.
        time.sleep(INTERVAL - ctx.clock() % INTERVAL)
        ts = int(ctx.clock())
        try:
            values = sampler.sample()
        except OSError as e:
            print(f"dahlia-history: {e}", file=sys.stderr)
            continue
        store(db, ts, values)
        export(ctx, ts, values)
        if once:
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dahlia-history")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="take a sample every minute")
    r.add_argument("--once", action="store_true", help="one sample, then exit")
    g = sub.add_parser("show", help="print the history as JSON")
    g.add_argument("range", choices=tuple(RANGES), nargs="?", default="hour")
    args = parser.parse_args(argv)
    ctx = Context()
    if args.cmd == "run":
        run(ctx, args.once)
    else:
        print(json.dumps(history_get(ctx, args.range)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
