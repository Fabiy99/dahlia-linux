"""History of system values (history) and the InfluxDB export."""

import json
import urllib.parse

import pytest
from dahlia_agent import actions, history, protocol
from test_agent import FakeRunner, err_code, run

T0 = 1_800_000_000  # a full hour


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def ctx(tmp_path):
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "cpuinfo").write_text("processor : 0\nmodel name : Test CPU\n")
    (proc / "loadavg").write_text("0.50 0.40 0.30 1/100 4242\n")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
    hw = tmp_path / "sys/class/hwmon/hwmon0"
    hw.mkdir(parents=True)
    (hw / "name").write_text("coretemp\n")
    (hw / "temp1_input").write_text("45000\n")
    c = actions.Context(runner=FakeRunner(), proc=proc, sys=tmp_path / "sys",
                        etc=tmp_path / "etc", var=tmp_path / "var", clock=Clock(T0),
                        hostname=lambda: "dahlia")
    set_counters(c, cpu=(100, 50), net=(1000, 2000))
    return c


def set_counters(ctx, cpu, net):
    total, idle = cpu
    # user nice system idle iowait irq softirq steal
    (ctx.proc / "stat").write_text(f"cpu {total - idle} 0 0 {idle} 0 0 0 0 0 0\n")
    (ctx.proc / "net/dev").write_text(
        "Inter-|   Receive\n face |bytes\n"
        f"  eth0: {net[0]} 0 0 0 0 0 0 0 {net[1]} 0 0 0 0 0 0 0\n"
        "    lo: 5 0 0 0 0 0 0 0 5 0 0 0 0 0 0 0\n")


def test_sampler_computes_rates(ctx):
    s = history.Sampler(ctx)
    first = s.sample()
    assert first == {"mem": 75.0, "load": 0.5, "temp_cpu": 45.0}
    ctx.clock.t += 60
    set_counters(ctx, cpu=(300, 100), net=(7000, 2600))
    v = s.sample()
    assert v["cpu"] == 75.0  # 200 jiffies, 50 idle
    assert v["net_rx:eth0"] == 100 and v["net_tx:eth0"] == 10
    assert "net_rx:lo" not in v
    # Counter reset (interface restarted): no rate for that minute.
    ctx.clock.t += 60
    set_counters(ctx, cpu=(400, 150), net=(10, 10))
    assert "net_rx:eth0" not in s.sample()


def test_store_rolls_up_and_prunes(tmp_path):
    db = history.connect(tmp_path / "h.db")
    history.store(db, T0, {"cpu": 10.0})
    history.store(db, T0 + 60, {"cpu": 30.0})
    rows = dict(((r, t), (v, n)) for r, t, v, n in db.execute(
        "SELECT res, ts, value, n FROM samples WHERE key = 'cpu'"))
    assert rows[(60, T0)] == (10.0, 1) and rows[(60, T0 + 60)] == (30.0, 1)
    assert rows[(900, T0)] == (20.0, 2) and rows[(3600, T0)] == (20.0, 2)
    # Minute rows older than two days go, the hour rows stay.
    history.store(db, T0 + 3 * 86400, {"cpu": 50.0})
    assert db.execute("SELECT COUNT(*) FROM samples WHERE res = 60 AND ts < ?",
                      (T0 + 86400,)).fetchone()[0] == 0
    assert db.execute("SELECT value FROM samples WHERE res = 3600 AND ts = ?",
                      (T0,)).fetchone()[0] == 20.0


def test_history_get(ctx):
    db = history.connect(history.db_file(ctx))
    for i in range(10):
        history.store(db, T0 + 60 * i, {"cpu": float(i), "net_rx:eth0": 100.0})
    db.close()
    ctx.clock.t = T0 + 600
    res = run(ctx, "history.get", {"range": "hour"})["result"]
    assert res["step"] == 60 and res["points"] == 61
    assert res["start"] == T0 - 3000
    cpu = res["series"]["cpu"]
    assert cpu[50:60] == [float(i) for i in range(10)] and cpu[0] is None and cpu[60] is None
    day = run(ctx, "history.get", {"range": "day"})["result"]
    assert day["step"] == 300
    # Two 5-minute buckets: average of 0-4 and 5-9.
    assert [v for v in day["series"]["cpu"] if v is not None] == [2.0, 7.0]
    assert err_code(run(ctx, "history.get", {"range": "decade"})) == protocol.INVALID_PARAMS


def test_history_get_without_database(ctx):
    res = run(ctx, "history.get", {})["result"]
    assert res["range"] == "day" and res["series"] == {}
    assert not history.db_file(ctx).exists()


def test_line_protocol():
    text = history.line_protocol("my host", T0, {"cpu": 12.5, "mem": 40.0,
                                                 "net_rx:eth0": 100, "net_tx:eth0": 7})
    assert text == (f"dahlia,host=my\\ host cpu=12.5,mem=40.0 {T0}\n"
                    f"dahlia_net,host=my\\ host,interface=eth0 rx=100.0,tx=7.0 {T0}\n")


def capture(ctx, fail=None):
    sent = []

    def post(url, data, headers):
        if fail:
            raise OSError(fail)
        sent.append((url, data.decode(), headers))

    ctx.senders = {"influx": post}
    return sent


def test_set_influx_v2_tests_first(ctx):
    sent = capture(ctx)
    res = run(ctx, "history.set_influx", {"enabled": True, "url": "http://nas.local:8086/",
                                          "org": "home", "bucket": "dahlia", "token": "s3cret"})
    assert res["ok"], res
    url, body, headers = sent[0]
    parsed = urllib.parse.urlsplit(url)
    assert parsed.path == "/api/v2/write"
    assert urllib.parse.parse_qs(parsed.query) == {"org": ["home"], "bucket": ["dahlia"],
                                                   "precision": ["s"]}
    assert headers["Authorization"] == "Token s3cret"
    assert body == f"dahlia,host=dahlia up=1i {T0}\n"
    saved = history.config_file(ctx)
    assert saved.stat().st_mode & 0o777 == 0o600
    got = run(ctx, "history.influx")["result"]["settings"]
    assert got["has_token"] is True and "token" not in got and got["url"] == "http://nas.local:8086/"
    # Token left out: the saved one stays.
    assert run(ctx, "history.set_influx", {"enabled": True, "url": "http://nas.local:8086",
                                           "org": "home", "bucket": "b2"})["ok"]
    assert sent[1][2]["Authorization"] == "Token s3cret"


def test_set_influx_v1_with_login(ctx):
    sent = capture(ctx)
    assert run(ctx, "history.set_influx", {"enabled": True, "version": 1,
                                           "url": "https://influx.local", "database": "dahlia",
                                           "username": "anna", "password": "pw"})["ok"]
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(sent[0][0]).query)
    assert q == {"db": ["dahlia"], "precision": ["s"], "u": ["anna"], "p": ["pw"]}


def test_failed_test_write_saves_nothing(ctx):
    capture(ctx, fail="connection refused")
    reply = run(ctx, "history.set_influx", {"enabled": True, "url": "http://x:8086",
                                            "org": "o", "bucket": "b", "token": "t"})
    assert err_code(reply) == protocol.FAILED and "connection refused" in reply["error"]["message"]
    assert not history.config_file(ctx).exists()


@pytest.mark.parametrize("params", [
    {"url": "ftp://x"}, {"url": "http://x/a b"}, {"org": ""}, {"token": ""},
    {"bucket": "a,b"}, {"token": "a\nb"},
])
def test_set_influx_rejects(ctx, params):
    sent = capture(ctx)
    base = {"enabled": True, "url": "http://x:8086", "org": "o", "bucket": "b", "token": "t"}
    assert err_code(run(ctx, "history.set_influx", {**base, **params})) == protocol.INVALID_PARAMS
    assert sent == []


def test_disable_needs_no_test(ctx):
    sent = capture(ctx, fail="down")
    assert run(ctx, "history.set_influx", {"enabled": False})["ok"]
    assert json.loads(history.config_file(ctx).read_text())["enabled"] is False
    assert sent == []


def test_export_records_result(ctx):
    sent = capture(ctx)
    run(ctx, "history.set_influx", {"enabled": True, "url": "http://x:8086", "org": "o",
                                    "bucket": "b", "token": "t"})
    history.export(ctx, T0 + 60, {"cpu": 5.0}, post=ctx.senders["influx"])
    assert sent[-1][1] == f"dahlia,host=dahlia cpu=5.0 {T0 + 60}\n"
    assert run(ctx, "history.influx")["result"]["last"] == {"time": T0 + 60, "ok": True,
                                                            "error": ""}

    def down(url, data, headers):
        raise OSError("timed out")

    history.export(ctx, T0 + 120, {"cpu": 5.0}, post=down)
    last = run(ctx, "history.influx")["result"]["last"]
    assert last["ok"] is False and "timed out" in last["error"]


def test_run_once_stores_a_sample(ctx, monkeypatch):
    def sleep(seconds):
        ctx.clock.t += seconds
        set_counters(ctx, cpu=(200, 100), net=(1600, 2000))

    monkeypatch.setattr(history.time, "sleep", sleep)
    ctx.clock.t = T0 + 30
    history.run(ctx, once=True)
    db = history.connect(history.db_file(ctx))
    rows = dict(db.execute("SELECT key, value FROM samples WHERE res = 60 AND ts = ?",
                           (T0 + 60,)).fetchall())
    assert rows["cpu"] == 50.0 and rows["net_rx:eth0"] == 20 and rows["mem"] == 75.0
