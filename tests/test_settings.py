"""settings.* actions of dahlia-agent: time, network, spindown, schedules."""

import json

import pytest
from dahlia_agent import actions, network, protocol, settings
from test_agent import FakeRunner, err_code, run


@pytest.fixture
def ctx(tmp_path):
    return actions.Context(runner=FakeRunner(), proc=tmp_path / "proc", sys=tmp_path / "sys",
                           etc=tmp_path / "etc", run_dir=tmp_path / "run")


# --- time ---------------------------------------------------------------------

ZONES = "Europe/Berlin\nEurope/Vienna\nUTC\nAmerica/Argentina/Buenos_Aires\n"


def test_time(ctx):
    ctx.runner.answers[("timedatectl", "show")] = (
        0, "Timezone=Europe/Berlin\nNTP=yes\nNTPSynchronized=no\nTimeUSec=Tue 2026-11-10\n", "")
    ctx.runner.answers[("timedatectl", "list-timezones")] = (0, ZONES, "")
    t = run(ctx, "settings.time")["result"]
    assert t["timezone"] == "Europe/Berlin" and t["ntp"] is True and t["synchronized"] is False
    assert "America/Argentina/Buenos_Aires" in t["timezones"]


def test_set_time(ctx):
    ctx.runner.answers[("timedatectl", "list-timezones")] = (0, ZONES, "")
    assert run(ctx, "settings.set_time", {"timezone": "Europe/Vienna", "ntp": False})["ok"]
    assert ["timedatectl", "set-timezone", "Europe/Vienna"] in ctx.runner.calls
    assert ctx.runner.calls[-1] == ["timedatectl", "set-ntp", "false"]


@pytest.mark.parametrize("tz", ["Mars/Base", "../etc/passwd", "-u", "Europe/Berlin x"])
def test_set_time_rejects(ctx, tz):
    ctx.runner.answers[("timedatectl", "list-timezones")] = (0, ZONES, "")
    assert err_code(run(ctx, "settings.set_time", {"timezone": tz, "ntp": True})) == \
        protocol.INVALID_PARAMS
    assert not any(c[:2] == ["timedatectl", "set-timezone"] for c in ctx.runner.calls)


# --- spindown -----------------------------------------------------------------

LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "type": "disk", "rota": True, "serial": "WD-1", "model": "WDC", "size": 8},
    {"name": "sdb", "type": "disk", "rota": True, "serial": "ST-2", "model": "ST", "size": 4},
    {"name": "nvme0n1", "type": "disk", "rota": False, "serial": "S64", "size": 2},
    {"name": "loop0", "type": "loop", "rota": True, "size": 1},
]})


@pytest.mark.parametrize("minutes,value", [(0, 0), (10, 120), (20, 240), (30, 241),
                                           (60, 242), (300, 250)])
def test_hdparm_value(minutes, value):
    assert settings.hdparm_value(minutes) == value


def test_spindown_unconfigured(ctx):
    ctx.runner.answers[("lsblk",)] = (0, LSBLK, "")
    out = run(ctx, "settings.spindown")["result"]
    assert out["default"] is None
    assert [d["path"] for d in out["disks"]] == ["/dev/sda", "/dev/sdb"]
    assert settings.apply_spindown(ctx) == {"applied": [], "failed": []}


def test_set_spindown(ctx):
    ctx.runner.answers[("lsblk",)] = (0, LSBLK, "")
    ctx.runner.answers[("hdparm", "-S", "120", "/dev/sdb")] = (5, "", "HDIO_DRIVE_CMD failed\n")
    res = run(ctx, "settings.set_spindown",
              {"default": 30, "disks": {"ST-2": 10, "WD-1": -1}})["result"]
    assert res["applied"] == [{"path": "/dev/sda", "minutes": 30}]
    assert res["failed"] == [{"path": "/dev/sdb", "error": "HDIO_DRIVE_CMD failed"}]
    assert ["hdparm", "-S", "241", "/dev/sda"] in ctx.runner.calls
    saved = json.loads((ctx.etc / "dahlia/spindown.json").read_text())
    assert saved == {"default": 30, "disks": {"ST-2": 10}}
    disks = {d["serial"]: d["minutes"] for d in run(ctx, "settings.spindown")["result"]["disks"]}
    assert disks == {"WD-1": -1, "ST-2": 10}


@pytest.mark.parametrize("params", [
    {"default": 7},
    {"default": 30, "disks": {"ST-2": 7}},
    {"default": 30, "disks": {"bad serial": 10}},
    {"default": 30, "disks": {"ST-2": "10"}},
    {"default": 30, "disks": []},
])
def test_set_spindown_rejects(ctx, params):
    assert err_code(run(ctx, "settings.set_spindown", params)) == protocol.INVALID_PARAMS
    assert not (ctx.etc / "dahlia/spindown.json").exists()


# --- schedules ----------------------------------------------------------------


@pytest.mark.parametrize("s,cal", [
    ({"frequency": "hourly", "time": "00:15"}, "*-*-* *:15:00"),
    ({"frequency": "daily", "time": "03:00"}, "*-*-* 03:00:00"),
    ({"frequency": "weekly", "time": "06:30", "weekday": "sun"}, "Sun *-*-* 06:30:00"),
    ({"frequency": "monthly", "time": "01:05", "monthday": 7}, "*-*-07 01:05:00"),
])
def test_on_calendar(s, cal):
    assert settings.on_calendar({"weekday": "mon", "monthday": 1, **s}) == cal


def test_schedules_defaults(ctx):
    ctx.runner.answers[("systemctl", "show")] = (
        0, "NextElapseUSecRealtime=Wed 2026-11-11 03:00:00 CET\nUnitFileState=enabled\n", "")
    s = run(ctx, "settings.schedules")["result"]["schedules"]
    assert s["mover"]["time"] == "03:00" and s["mover"]["enabled"] is True
    assert s["snapraid-scrub"]["frequency"] == "weekly"
    assert s["snapraid-sync"]["next"].startswith("Wed")


def test_set_schedule(ctx):
    assert run(ctx, "settings.set_schedule", {"job": "snapraid-sync", "frequency": "weekly",
                                              "time": "05:45", "weekday": "sat"})["ok"]
    dropin = ctx.etc / "systemd/system/dahlia-snapraid-sync.timer.d/dahlia-schedule.conf"
    assert dropin.read_text() == "[Timer]\nOnCalendar=\nOnCalendar=Sat *-*-* 05:45:00\n"
    assert ctx.runner.calls[-1] == ["systemctl", "restart", "dahlia-snapraid-sync.timer"]
    assert settings.saved_schedules(ctx)["snapraid-sync"]["weekday"] == "sat"
    assert settings.saved_schedules(ctx)["mover"]["time"] == "03:00"


def test_set_schedule_off(ctx):
    assert run(ctx, "settings.set_schedule", {"job": "mover", "frequency": "off"})["ok"]
    assert ctx.runner.calls == [["systemctl", "disable", "--now", "dahlia-mover.timer"]]


@pytest.mark.parametrize("params", [
    {"job": "update", "frequency": "daily"},
    {"job": "mover", "frequency": "yearly"},
    {"job": "mover", "frequency": "daily", "time": "24:00"},
    {"job": "mover", "frequency": "daily", "time": "3:00"},
    {"job": "mover", "frequency": "monthly", "monthday": 31},
    {"job": "mover", "frequency": "weekly", "weekday": "sunday"},
])
def test_set_schedule_rejects(ctx, params):
    assert err_code(run(ctx, "settings.set_schedule", params)) == protocol.INVALID_PARAMS
    assert ctx.runner.calls == []


# --- network ------------------------------------------------------------------

INTERFACES = """\
source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

# The primary network interface
allow-hotplug ens18
iface ens18 inet dhcp
# This is an autoconfigured IPv6 interface
iface ens18 inet6 auto
"""

STATIC = """\
auto eth0
iface eth0 inet static
    address 192.168.1.20
    netmask 255.255.255.0
    gateway 192.168.1.1
"""


@pytest.fixture
def net(ctx):
    (ctx.etc / "network").mkdir(parents=True)
    (ctx.etc / "network/interfaces").write_text(INTERFACES)
    (ctx.etc / "resolv.conf").write_text("domain lan\nnameserver 192.168.1.1\n")
    return ctx


def test_parse_interfaces():
    assert network.parse_interfaces(INTERFACES) == {
        "iface": "ens18", "mode": "dhcp", "address": "", "gateway": ""}
    assert network.parse_interfaces(STATIC) == {
        "iface": "eth0", "mode": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.1"}
    assert network.parse_interfaces("auto lo\niface lo inet loopback\n") is None


def test_render_interfaces_static_and_back():
    text = network.render_interfaces(INTERFACES, "ens18", "static", "10.0.0.5/24", "10.0.0.1")
    assert "iface ens18 inet static\n    address 10.0.0.5/24\n    gateway 10.0.0.1\n" \
        "# This is an autoconfigured IPv6 interface\niface ens18 inet6 auto\n" in text
    assert "allow-hotplug ens18" in text and "iface lo inet loopback" in text
    assert network.render_interfaces(text, "ens18", "dhcp", "", "") == INTERFACES
    back = network.render_interfaces(STATIC, "eth0", "dhcp", "", "")
    assert back == "auto eth0\niface eth0 inet dhcp\n"


def test_network_read(net):
    net.runner.answers[("ip", "-j", "-4", "addr")] = (
        0, json.dumps([{"addr_info": [{"local": "192.168.1.50", "prefixlen": 24}]}]), "")
    net.runner.answers[("ip", "-j", "-4", "route")] = (
        0, json.dumps([{"dst": "default", "gateway": "192.168.1.1"}]), "")
    out = run(net, "settings.network")["result"]
    assert out["iface"] == "ens18" and out["mode"] == "dhcp"
    assert out["current"] == ["192.168.1.50/24"] and out["current_gateway"] == "192.168.1.1"
    assert out["dns"] == ["192.168.1.1"] and out["pending"] is None


def test_set_network_static(net):
    params = {"mode": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.1",
              "dns": "192.168.1.1, 9.9.9.9"}
    res = run(net, "settings.set_network", params)["result"]
    assert res["rollback_seconds"] == network.ROLLBACK_SECONDS
    staged = (net.etc / "network/interfaces.dahlia-new").read_text()
    assert "iface ens18 inet static\n    address 192.168.1.20/24\n" in staged
    assert (net.etc / "resolv.conf.dahlia-new").read_text() == \
        "nameserver 192.168.1.1\nnameserver 9.9.9.9\n"
    # The live file is untouched until dahlia-network applies it.
    assert (net.etc / "network/interfaces").read_text() == INTERFACES
    assert net.runner.calls[-1] == ["systemd-run", "--unit=dahlia-network-apply", "--collect",
                                    "dahlia-network", "apply", "--iface", "ens18"]


@pytest.mark.parametrize("params", [
    {"mode": "static", "address": "192.168.1.20", "gateway": "192.168.1.1", "dns": "1.1.1.1"},
    {"mode": "static", "address": "192.168.1.20/24", "gateway": "10.0.0.1", "dns": "1.1.1.1"},
    {"mode": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.20",
     "dns": "1.1.1.1"},
    {"mode": "static", "address": "192.168.1.0/24", "gateway": "192.168.1.1", "dns": "1.1.1.1"},
    {"mode": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.1", "dns": ""},
    {"mode": "static", "address": "300.1.1.1/24", "gateway": "192.168.1.1", "dns": "1.1.1.1"},
    {"mode": "static", "address": "10.0.0.5/31", "gateway": "10.0.0.4", "dns": "1.1.1.1"},
    {"mode": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.1",
     "dns": "1.1.1.1; rm -rf /"},
    {"mode": "bridge"},
])
def test_set_network_rejects(net, params):
    assert err_code(run(net, "settings.set_network", params)) == protocol.INVALID_PARAMS
    assert net.runner.calls == []
    assert not (net.etc / "network/interfaces.dahlia-new").exists()


def test_set_network_busy_and_confirm(net):
    assert err_code(run(net, "settings.confirm_network")) == protocol.FAILED
    net.run_dir.mkdir()
    (net.run_dir / "network-pending").write_text("9999999999")
    assert err_code(run(net, "settings.set_network", {"mode": "dhcp"})) == protocol.BUSY
    assert run(net, "settings.confirm_network")["ok"]
    assert (net.run_dir / "network-confirmed").exists()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def staged(net, mode="static"):
    run(net, "settings.set_network", {"mode": mode, "address": "192.168.1.20/24",
                                      "gateway": "192.168.1.1", "dns": "9.9.9.9"})
    return network.Paths.of(net)


def test_apply_confirmed(net):
    paths, clock, runner = staged(net), Clock(), FakeRunner()

    def sleep(s):
        clock.sleep(s)
        if clock.now > 1010:
            paths.confirmed.touch()

    assert network.apply(paths, runner, "ens18", timeout=60, sleep=sleep, clock=clock)
    assert "inet static" in paths.interfaces.read_text()
    assert paths.resolv.read_text() == "nameserver 9.9.9.9\n"
    assert not paths.pending.exists() and not paths.confirmed.exists()
    assert runner.calls == [["ifdown", "--force", "ens18"], ["ip", "addr", "flush", "dev", "ens18"],
                            ["ifup", "ens18"]]


def test_apply_rolls_back(net):
    paths, clock, runner = staged(net), Clock(), FakeRunner()
    assert not network.apply(paths, runner, "ens18", timeout=60, sleep=clock.sleep, clock=clock)
    assert paths.interfaces.read_text() == INTERFACES
    assert paths.resolv.read_text() == "domain lan\nnameserver 192.168.1.1\n"
    assert paths.reverted.exists() and not paths.pending.exists()
    assert [c[0] for c in runner.calls].count("ifup") == 2
    assert run(net, "settings.network")["result"]["reverted"] is True


def test_apply_dhcp_keeps_resolv(net):
    paths, clock = staged(net, "dhcp"), Clock()
    clock_sleep = clock.sleep

    def sleep(s):
        clock_sleep(s)
        paths.confirmed.touch()

    assert network.apply(paths, FakeRunner(), "ens18", timeout=60, sleep=sleep, clock=clock)
    assert paths.interfaces.read_text() == INTERFACES
    assert paths.resolv.read_text() == "domain lan\nnameserver 192.168.1.1\n"
