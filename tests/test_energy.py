"""Power draw (energy) from the UPS or a smart plug, and kWh in the weekly letter."""

import datetime
import json

import pytest
from dahlia_agent import actions, energy, history, letters
from test_agent import FakeRunner, err_code, run


@pytest.fixture
def ctx(tmp_path):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self/mounts").write_text("/dev/sda1 / ext4 rw 0 0\n")
    (proc / "uptime").write_text("100.0 1.0\n")
    return actions.Context(runner=FakeRunner(), proc=proc, etc=tmp_path / "etc",
                           var=tmp_path / "var", log_dir=tmp_path / "log", pool=tmp_path,
                           system=tmp_path / "srv")


class Plug:
    def __init__(self, answers):
        self.answers = answers
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if url not in self.answers:
            raise OSError("no route")
        return self.answers[url]


def set_ups(ctx, upsc):
    (ctx.etc / "dahlia").mkdir(parents=True, exist_ok=True)
    (ctx.etc / "dahlia/ups.json").write_text(json.dumps({"mode": "usb"}))
    ctx.runner.answers[("upsc",)] = (0, upsc, "")


def test_defaults_without_config(ctx):
    got = run(ctx, "energy.get")["result"]
    assert got == {"source": "off", "plug": "shelly", "host": "", "price": 35,
                   "currency": "EUR", "watts": None}


def test_ups_realpower_or_load_times_nominal(ctx):
    set_ups(ctx, "ups.status: OL\nups.load: 20\nups.realpower.nominal: 600\n")
    assert energy.ups_watts(ctx) == 120.0
    set_ups(ctx, "ups.status: OL\nups.load: 20\nups.realpower: 87\nups.realpower.nominal: 600\n")
    assert energy.ups_watts(ctx) == 87.0
    set_ups(ctx, "ups.status: OL\nups.load: 20\n")
    assert energy.ups_watts(ctx) is None


def test_shelly_gen2_then_gen1_and_tasmota():
    gen2 = Plug({"http://plug/rpc/Switch.GetStatus?id=0": {"apower": 41.27}})
    assert energy.plug_watts("shelly", "plug", gen2) == 41.3
    gen1 = Plug({"http://plug/meter/0": {"power": 12}})
    assert energy.plug_watts("shelly", "plug", gen1) == 12.0
    assert gen1.urls[0].endswith("Switch.GetStatus?id=0")
    tasmota = Plug({"http://plug/cm?cmnd=Status%2010": {"StatusSNS": {"ENERGY": {"Power": 33}}}})
    assert energy.plug_watts("tasmota", "plug", tasmota) == 33.0
    assert energy.plug_watts("shelly", "plug", Plug({})) is None
    # Nonsense values are no reading.
    bad = Plug({"http://plug/meter/0": {"power": -5}})
    assert energy.plug_watts("shelly", "plug", bad) is None


def test_set_plug_must_answer(ctx):
    ctx.senders = {"energy": Plug({})}
    r = run(ctx, "energy.set", {"source": "plug", "host": "192.168.1.50"})
    assert err_code(r) == "failed"
    ctx.senders = {"energy": Plug({"http://192.168.1.50/meter/0": {"power": 50}})}
    got = run(ctx, "energy.set", {"source": "plug", "host": "192.168.1.50", "price": 32})["result"]
    assert got["watts"] == 50.0 and got["price"] == 32
    assert energy.load_config(ctx)["host"] == "192.168.1.50"
    assert err_code(run(ctx, "energy.set", {"source": "plug", "host": "x/../y"})) == \
        "invalid_params"
    assert err_code(run(ctx, "energy.set", {"source": "plug"})) == "invalid_params"


def test_set_ups_needs_a_ups(ctx):
    assert err_code(run(ctx, "energy.set", {"source": "ups"})) == "failed"
    set_ups(ctx, "ups.load: 10\nups.realpower.nominal: 500\n")
    assert run(ctx, "energy.set", {"source": "ups", "currency": "CHF"})["result"]["watts"] == 50.0


def test_history_samples_power(ctx, tmp_path):
    (ctx.proc / "net").mkdir()
    (ctx.proc / "cpuinfo").write_text("processor : 0\n")
    (ctx.proc / "loadavg").write_text("0.1 0.1 0.1 1/1 1\n")
    (ctx.proc / "meminfo").write_text("MemTotal: 100 kB\nMemAvailable: 50 kB\n")
    (ctx.proc / "stat").write_text("cpu 1 0 0 1 0 0 0 0 0 0\n")
    (ctx.proc / "net/dev").write_text("a\nb\n")
    ctx.sys = tmp_path / "sys"
    s = history.Sampler(ctx)
    assert "power" not in s.sample()
    set_ups(ctx, "ups.realpower: 64\n")
    (ctx.etc / "dahlia/energy.json").write_text(json.dumps({"source": "ups"}))
    assert s.sample()["power"] == 64.0
    ctx.runner.answers[("upsc",)] = (1, "", "Error: Driver not connected")
    assert "power" not in s.sample()


def test_letter_kwh_and_cost(ctx):
    (ctx.etc / "dahlia").mkdir(parents=True)
    (ctx.etc / "dahlia/energy.json").write_text(json.dumps({"source": "ups", "price": 30}))
    day = datetime.date(2026, 11, 9)
    start = int(datetime.datetime.combine(day, datetime.time()).astimezone().timestamp())
    db = history.connect(history.db_file(ctx))
    with db:
        for h in range(100):
            db.execute("INSERT INTO samples VALUES (3600, 'power', ?, ?, 1)",
                       (start + h * 3600, 50.0))
    db.close()
    ctx.clock = lambda: start + 8 * 86400
    w = letters.build_week(ctx, day)
    assert w["energy"] == {"watts": 50.0, "hours": 100, "kwh": 5.0, "price": 30,
                           "currency": "EUR", "cost": 1.5}
    assert "Power: 50 W on average, 5.0 kWh, 1.50 EUR" in letters.summary(w)
    d = letters.letters_dir(ctx)
    d.mkdir(parents=True)
    for i in (1, 2):
        (d / f"2026-W0{i}.json").write_text(json.dumps({**w, "id": f"2026-W0{i}"}))
    y = letters.build_year(ctx, 2026)
    assert y["energy"] == {"kwh": 10.0, "cost": 3.0, "currency": "EUR", "weeks": 2, "watts": 50.0}
