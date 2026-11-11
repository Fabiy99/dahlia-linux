"""notify.* and ups.* actions of dahlia-agent and the dahlia-notify checks."""

import json

import pytest
from dahlia_agent import actions, notify, protocol, ups
from test_agent import FakeRunner, err_code, run


class Sent:
    """Records what the channels would send; 'fail' makes a channel raise."""

    def __init__(self, fail=()):
        self.items = []
        self.fail = set(fail)

    def senders(self):
        def make(channel):
            def send(c, hostname, level, title, message):
                if channel in self.fail:
                    raise OSError("connection refused")
                self.items.append((channel, level, title, message))
            return send
        return {ch: make(ch) for ch in notify.CHANNELS}


@pytest.fixture
def sent():
    return Sent()


@pytest.fixture
def ctx(tmp_path, sent):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self/mounts").write_text("")
    return actions.Context(runner=FakeRunner(), proc=proc, sys=tmp_path / "sys",
                           etc=tmp_path / "etc", run_dir=tmp_path / "run",
                           var=tmp_path / "var", hostname=lambda: "nas", clock=lambda: 1000.0,
                           log_dir=tmp_path / "log", reboot_flag=tmp_path / "reboot-required",
                           senders=sent.senders())


NTFY = {"enabled": True, "url": "https://ntfy.sh", "topic": "dahlia-home", "token": "tk_1"}


# --- channels -----------------------------------------------------------------


def test_set_channel_hides_secret(ctx):
    res = run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})["result"]
    assert res == {"enabled": True, "url": "https://ntfy.sh", "topic": "dahlia-home",
                   "token_set": True}
    assert (ctx.etc / "dahlia/notify.json").stat().st_mode & 0o777 == 0o600
    got = run(ctx, "notify.get")["result"]
    assert "token" not in got["ntfy"] and got["ntfy"]["token_set"] is True
    assert "password" not in got["email"]
    # An empty secret keeps the saved one.
    run(ctx, "notify.set", {"channel": "ntfy", "settings": {**NTFY, "token": ""}})
    assert notify.load_config(ctx)["ntfy"]["token"] == "tk_1"


@pytest.mark.parametrize("channel,settings", [
    ("ntfy", {**NTFY, "topic": "a b"}),
    ("ntfy", {**NTFY, "url": "ftp://x"}),
    ("ntfy", {**NTFY, "url": "https://x.y/a b"}),
    ("ntfy", {**NTFY, "extra": 1}),
    ("ntfy", {"enabled": True, "url": "https://ntfy.sh", "topic": ""}),
    ("email", {"enabled": True, "host": "smtp.example.org", "sender": "nas@x.de", "to": ""}),
    ("email", {"host": "smtp.example.org", "port": 70000}),
    ("email", {"host": "smtp.example.org", "security": "ssl"}),
    ("email", {"host": "-oProxyCommand=x"}),
    ("email", {"to": "a@b.de, c@d.de"}),
    ("email", {"port": "587"}),
    ("gotify", {"enabled": True, "url": "https://gotify.lan", "token": ""}),
])
def test_set_channel_rejects(ctx, channel, settings):
    assert err_code(run(ctx, "notify.set", {"channel": channel, "settings": settings})) == \
        protocol.INVALID_PARAMS
    assert not (ctx.etc / "dahlia/notify.json").exists()


def test_test_message_and_history(ctx, sent):
    assert err_code(run(ctx, "notify.test")) == protocol.FAILED
    run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})
    assert run(ctx, "notify.test")["result"] == {"results": {"ntfy": "ok"}}
    assert sent.items == [("ntfy", "info", "Test", "Test message from the Dahlia dashboard.")]
    hist = run(ctx, "notify.get")["result"]["history"]
    assert hist[0]["title"] == "Test" and hist[0]["results"] == {"ntfy": "ok"}


def test_failing_channel_is_reported(ctx, tmp_path):
    bad = Sent(fail={"gotify"})
    ctx.senders = bad.senders()
    run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})
    run(ctx, "notify.set", {"channel": "gotify", "settings": {
        "enabled": True, "url": "https://gotify.lan", "token": "abc"}})
    res = run(ctx, "notify.test")["result"]["results"]
    assert res["ntfy"] == "ok" and res["gotify"].startswith("OSError")


def test_events_switch(ctx, sent):
    run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})
    assert run(ctx, "notify.set_events", {"events": {"usage": False}})["ok"]
    assert notify.send(ctx, "warning", "x", "y", "usage") == {}
    assert sent.items == []
    assert err_code(run(ctx, "notify.set_events", {"events": {"bogus": True}})) == \
        protocol.INVALID_PARAMS


# --- checks -------------------------------------------------------------------

LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "type": "disk", "tran": "sata", "rota": True, "size": 8},
]})


def smart(health_ok=True, temp=40):
    return json.dumps({"model_name": "WDC", "smart_status": {"passed": True},
                       "temperature": {"current": temp},
                       "ata_smart_attributes": {"table": [] if health_ok else [
                           {"id": 5, "raw": {"value": 8}}]}})


def test_check_sends_once_and_resolves(ctx, sent):
    run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})
    ctx.runner.answers[("lsblk",)] = (0, LSBLK, "")
    ctx.runner.answers[("smartctl",)] = (0, smart(False, 58), "")
    assert sorted(notify.check(ctx)) == ["smart:/dev/sda", "temp:/dev/sda"]
    assert {i[1] for i in sent.items} == {"warning"}
    assert notify.check(ctx) == []  # nothing new
    ctx.runner.answers[("smartctl",)] = (0, smart(True, 40), "")
    assert sorted(notify.check(ctx)) == ["smart:/dev/sda", "temp:/dev/sda"]
    assert sent.items[-1][1] == "info" and sent.items[-1][2].startswith("Resolved")


def test_check_reports_pending_restart_once(ctx, sent):
    run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})
    ctx.reboot_flag.write_text("*** System restart required ***\n")
    (ctx.reboot_flag.parent / "reboot-required.pkgs").write_text("linux-image-amd64\nlibc6\n")
    assert notify.check(ctx) == ["reboot:required"]
    assert sent.items[-1][2] == "Restart needed" and "libc6, linux-image-amd64" in sent.items[-1][3]
    assert notify.check(ctx) == []
    ctx.reboot_flag.unlink()
    assert notify.check(ctx) == [] and len(sent.items) == 1


def test_no_restart_notice_with_automatic_reboot(ctx, sent):
    run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})
    run(ctx, "updates.set_auto", {"enabled": True, "reboot": True, "reboot_time": "03:30"})
    ctx.reboot_flag.write_text("")
    assert notify.check(ctx) == []


def test_job_failed(ctx, sent):
    run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})
    ctx.runner.answers[("journalctl",)] = (0, "snapraid: disk1 missing\n", "")
    notify.job_failed(ctx, "dahlia-snapraid-sync.service")
    assert sent.items[0][2] == "SnapRAID sync failed"
    assert "disk1 missing" in sent.items[0][3]


# --- UPS ----------------------------------------------------------------------

UPSC = """battery.charge: 87
battery.runtime: 1520
device.mfr: APC
device.model: Back-UPS ES 700
ups.load: 12
ups.status: OL CHRG
"""


def test_ups_usb(ctx):
    assert run(ctx, "ups.set", {"mode": "usb", "shutdown_minutes": 5})["ok"]
    nut = ctx.etc / "nut"
    assert (nut / "nut.conf").read_text().endswith("MODE=standalone\n")
    assert "driver = usbhid-ups" in (nut / "ups.conf").read_text()
    assert "LISTEN 127.0.0.1 3493" in (nut / "upsd.conf").read_text()
    users = (nut / "upsd.users").read_text()
    mon = (nut / "upsmon.conf").read_text()
    pw = users.split("password = ")[1].split()[0]
    assert f"MONITOR ups@localhost 1 dahlia-mon {pw} primary" in mon
    assert "NOTIFYCMD /usr/lib/dahlia/ups-notify.sh" in mon and "NOTIFYFLAG ONBATT" in mon
    # The monitor password stays the same on the next save.
    run(ctx, "ups.set", {"mode": "usb", "shutdown_minutes": 10})
    assert pw in (nut / "upsd.users").read_text()
    assert ["systemctl", "restart", "nut-monitor.service"] in ctx.runner.calls


def test_ups_remote_and_status(ctx):
    params = {"mode": "remote", "host": "192.168.1.5", "name": "ups", "user": "monuser",
              "password": "secret"}
    assert run(ctx, "ups.set", params)["result"]["password_set"] is True
    mon = (ctx.etc / "nut/upsmon.conf").read_text()
    assert "MONITOR ups@192.168.1.5:3493 1 monuser secret secondary" in mon
    assert (ctx.etc / "nut/nut.conf").read_text().endswith("MODE=netclient\n")
    ctx.runner.answers[("upsc",)] = (0, UPSC, "")
    got = run(ctx, "ups.get")["result"]
    assert "password" not in got
    assert got["status"]["model"] == "APC Back-UPS ES 700"
    assert got["status"]["charge"] == 87 and got["status"]["on_battery"] is False
    # Saving again without password keeps it.
    run(ctx, "ups.set", {**params, "password": ""})
    assert ups.load_config(ctx)["password"] == "secret"


@pytest.mark.parametrize("params", [
    {"mode": "remote", "host": "1.2.3.4", "user": "u"},
    {"mode": "remote", "host": "1.2.3.4", "user": "u", "password": "a b"},
    {"mode": "remote", "host": "x;reboot", "user": "u", "password": "p"},
    {"mode": "usb", "name": "u p s"},
    {"mode": "usb", "shutdown_minutes": 500},
    {"mode": "snmp"},
])
def test_ups_rejects(ctx, params):
    assert err_code(run(ctx, "ups.set", params)) == protocol.INVALID_PARAMS
    assert not (ctx.etc / "nut").exists()


def test_ups_off(ctx):
    run(ctx, "ups.set", {"mode": "off"})
    assert (ctx.etc / "nut/nut.conf").read_text().endswith("MODE=none\n")
    assert run(ctx, "ups.get")["result"]["status"] is None


def test_ups_events(ctx, sent):
    run(ctx, "notify.set", {"channel": "ntfy", "settings": NTFY})
    run(ctx, "ups.set", {"mode": "usb", "shutdown_minutes": 5})
    ups.handle_event(ctx, "ONBATT", "UPS ups@localhost on battery")
    timer = next(c for c in ctx.runner.calls if c[0] == "systemd-run")
    assert "--on-active=5min" in timer and timer[-2:] == ["systemctl", "poweroff"]
    assert sent.items[-1][1] == "warning" and "5 minutes" in sent.items[-1][3]
    ups.handle_event(ctx, "ONLINE", "")
    assert ["systemctl", "stop", "dahlia-ups-shutdown.timer"] in ctx.runner.calls
    assert sent.items[-1][2] == "UPS back on mains power"
