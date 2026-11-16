"""letters: weekly letter and year review from history, backups, apt and apps."""

import datetime
import gzip
import json

import pytest
from dahlia_agent import actions, history, letters
from test_agent import FakeRunner, run

WHOAMI = """\
schema: 2
id: whoami
name: Whoami
category: tools
description: {de: Test, en: Test}
services:
  app:
    image: traefik/whoami:VERSION
"""

MONDAY = datetime.date(2026, 11, 16)


def at(day: datetime.date, hour: int = 12) -> int:
    return int(datetime.datetime.combine(day, datetime.time(hour)).astimezone().timestamp())


@pytest.fixture
def ctx(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "self").mkdir()
    (proc / "self/mounts").write_text("/dev/sda1 / ext4 rw 0 0\n")
    (proc / "uptime").write_text("90000.5 10.0\n")
    c = actions.Context(runner=FakeRunner(), proc=proc, var=tmp_path / "var",
                        log_dir=tmp_path / "log", pool=tmp_path, system=tmp_path / "srv",
                        etc=tmp_path / "etc", clock=lambda: at(MONDAY, 8))
    (c.var / "apps/whoami").mkdir(parents=True)
    (c.var / "apps/whoami/app.yaml").write_text(WHOAMI.replace("VERSION", "v1.11.0"))
    (c.var / "apps/whoami/state.json").write_text("{}")
    return c


def fill_history(ctx, start, hours, rows):
    db = history.connect(history.db_file(ctx))
    with db:
        for h in range(hours):
            ts = start + h * 3600
            for key, value in rows.items():
                db.execute("INSERT INTO samples VALUES (3600, ?, ?, ?, 1)", (key, ts, value))
    db.close()


def test_week_letter_numbers(ctx):
    last = MONDAY - datetime.timedelta(days=7)
    start = at(last, 0)
    fill_history(ctx, start, 24, {"cpu": 10.0, "temp_cpu": 50.0, "net_rx:eth0": 1000.0,
                                  "net_tx:eth0": 500.0, "net_rx:docker0": 99999.0})
    (ctx.var / "backup-runs.json").write_text(json.dumps({"runs": [
        {"time": datetime.datetime.fromtimestamp(at(last), datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"), "result": "success", "added": 2000},
        {"time": "2020-01-01T00:00:00Z", "result": "failed", "added": None},
    ]}))
    (ctx.log_dir / "apt").mkdir(parents=True)
    log = (f"Start-Date: {last} 10:00:00\nUpgrade: libc6:amd64 (2.41-1, 2.41-2), "
           "base:amd64 (1.4.0, 1.5.0)\nEnd-Date: x\n\n"
           "Start-Date: 2020-01-01 10:00:00\nUpgrade: old:amd64 (1, 2)\n")
    with gzip.open(ctx.log_dir / "apt/history.log.1.gz", "wt") as f:
        f.write(log)
    (ctx.var / "notifications.json").write_text(json.dumps({"items": [
        {"time": at(last), "level": "warning", "title": "Disk hot"},
        {"time": at(last), "level": "info", "title": "Test"},
    ]}))
    w = letters.build_week(ctx, last)
    assert w["id"] == "2026-W46" and w["to"] - w["from"] == 7 * 86400
    assert w["cpu_avg"] == 10.0 and w["temp_max"] == 50.0
    assert w["traffic"] == {"rx": 24 * 1000 * 3600, "tx": 24 * 500 * 3600}
    assert w["backups"] == {"runs": 1, "ok": 1, "added": 2000}
    assert w["updates"] == 2
    assert w["notices"] == {"warning": 1, "critical": 0, "titles": ["Disk hot"]}
    assert w["apps"]["count"] == 1 and w["apps"]["new"] == []
    assert w["uptime"] == 90000 and "added" not in w["storage"]


def test_run_writes_last_week_once_and_compares(ctx):
    ctx.senders = {}
    assert letters.run_daily(ctx, MONDAY) == ["2026-W46"]
    assert letters.run_daily(ctx, MONDAY) == []
    first = letters.load(ctx, "2026-W46")
    # A week later: one app updated, one new, space used grew.
    (ctx.var / "apps/whoami/app.yaml").write_text(WHOAMI.replace("VERSION", "v1.12.0"))
    (ctx.var / "apps/notes").mkdir()
    (ctx.var / "apps/notes/app.yaml").write_text(
        WHOAMI.replace("whoami", "notes").replace("Whoami", "Notes").replace("VERSION", "1"))
    (ctx.var / "apps/notes/state.json").write_text("{}")
    first["storage"]["used"] -= 5000
    (letters.letters_dir(ctx) / "2026-W46.json").write_text(json.dumps(first))
    nxt = MONDAY + datetime.timedelta(days=7)
    ctx.clock = lambda: at(nxt, 8)
    assert letters.run_daily(ctx, nxt) == ["2026-W47"]
    w = letters.load(ctx, "2026-W47")
    assert w["apps"]["new"] == ["Notes"] and w["apps"]["updated"] == ["Whoami"]
    assert w["storage"]["added"] >= 5000
    items = json.loads((ctx.var / "notifications.json").read_text())["items"]
    assert [i["title"] for i in items] == ["Weekly letter", "Weekly letter"]
    assert "New apps: Notes" in items[0]["message"]


def test_year_review(ctx):
    d = letters.letters_dir(ctx)
    d.mkdir(parents=True)
    for i, (week, used) in enumerate([("2025-W01", 100), ("2025-W02", 400)]):
        (d / f"{week}.json").write_text(json.dumps({
            "kind": "week", "id": week, "from": i * 10, "to": i * 10 + 10, "uptime": 50 * (i + 1),
            "temp_max": 40 + i, "cpu_avg": 10 + 10 * i, "traffic": {"rx": 1, "tx": 2},
            "storage": {"mount": "/", "used": used, "size": 1000},
            "backups": {"runs": 7, "ok": 6 + i, "added": 10}, "updates": 3,
            "apps": {"count": 2, "new": ["Immich"] if i else [], "updated": ["Immich"]},
            "notices": {"warning": 1, "critical": i}}))
    y = letters.build_year(ctx, 2025)
    assert y["weeks"] == 2 and y["traffic"] == {"rx": 2, "tx": 4}
    assert y["storage"]["added"] == 300 and y["backups"] == {"runs": 14, "ok": 13, "added": 20}
    assert y["apps"]["new"] == ["Immich"] and y["apps"]["updated"] == ["Immich"]
    assert y["temp_max"] == 41 and y["cpu_avg"] == 15.0 and y["uptime_max"] == 100
    assert y["notices"] == {"warning": 2, "critical": 1}
    ctx.clock = lambda: at(datetime.date(2026, 1, 1), 8)
    assert "2025" in letters.run_daily(ctx, datetime.date(2026, 1, 1))
    assert letters.load(ctx, "2025")["kind"] == "year"


def test_actions(ctx):
    letters.run_daily(ctx, MONDAY)
    got = run(ctx, "letters.list")["result"]["letters"]
    assert got == [{"kind": "week", "id": "2026-W46", "from": got[0]["from"], "to": got[0]["to"]}]
    assert run(ctx, "letters.get", {"id": "2026-W46"})["result"]["id"] == "2026-W46"
    assert not run(ctx, "letters.get", {"id": "2026-W01"})["ok"]
    assert not run(ctx, "letters.get", {"id": "../x"})["ok"]
    now = run(ctx, "letters.now", {"kind": "week"})["result"]
    assert now["preview"] and now["id"] == "2026-W47" and now["to"] == at(MONDAY, 8)
    assert run(ctx, "letters.now", {"kind": "year"})["result"]["id"] == "2026"


def test_history_without_db(ctx):
    assert letters.history_numbers(ctx, 0, 10) == {}
