"""Milestone 30 in dahlia-agent: remote syslog, find a disk by its LED, start/stop all apps."""

import json

import pytest
from dahlia_agent import actions, protocol, remotelog
from test_agent import RUNNING, FakeRunner, err_code, run


@pytest.fixture
def ctx(tmp_path):
    return actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                           var=tmp_path / "var", sys=tmp_path / "sys", proc=tmp_path / "proc")


# --- remote syslog --------------------------------------------------------------


def test_syslog_defaults(ctx):
    ctx.runner.answers[("systemctl", "is-active")] = (3, "inactive\n", "")
    assert run(ctx, "settings.syslog")["result"] == {
        "enabled": False, "host": "", "port": 514, "protocol": "udp", "severity": "info",
        "running": False}


def test_syslog_enable(ctx):
    ctx.runner.answers[("systemctl", "is-active")] = (0, "active\n", "")
    out = run(ctx, "settings.set_syslog", {"enabled": True, "host": "192.168.1.10",
                                            "port": 1514, "protocol": "tcp",
                                            "severity": "warning"})["result"]
    assert out["enabled"] and out["running"] and out["host"] == "192.168.1.10"
    conf = remotelog.rsyslog_file(ctx).read_text()
    assert ('*.warning action(type="omfwd" target="192.168.1.10" port="1514" protocol="tcp"'
            in conf)
    assert conf.rstrip().endswith("stop")
    assert "ForwardToSyslog=yes" in remotelog.journald_file(ctx).read_text()
    assert ["systemctl", "enable", "rsyslog.service"] in ctx.runner.calls
    assert ["systemctl", "restart", "rsyslog.service"] in ctx.runner.calls
    assert ["systemctl", "restart", "systemd-journald.service"] in ctx.runner.calls
    assert any(c[0] == "logger" for c in ctx.runner.calls)
    saved = json.loads(remotelog.config_file(ctx).read_text())
    assert saved == {"enabled": True, "host": "192.168.1.10", "port": 1514, "protocol": "tcp",
                     "severity": "warning"}


def test_syslog_disable(ctx):
    run(ctx, "settings.set_syslog", {"enabled": True, "host": "nas.local"})
    ctx.runner.calls.clear()
    out = run(ctx, "settings.set_syslog", {"enabled": False, "host": "nas.local"})["result"]
    assert out["enabled"] is False and out["host"] == "nas.local"
    assert not remotelog.rsyslog_file(ctx).exists()
    assert not remotelog.journald_file(ctx).exists()
    assert ["systemctl", "disable", "--now", "rsyslog.service"] in ctx.runner.calls


@pytest.mark.parametrize("params", [
    {"enabled": True, "host": ""},
    {"enabled": True, "host": 'nas" target="evil'},
    {"enabled": True, "host": "nas\nstop"},
    {"enabled": True, "host": "nas", "port": 0},
    {"enabled": True, "host": "nas", "protocol": "relp"},
    {"enabled": True, "host": "nas", "severity": "emerg"},
])
def test_syslog_rejects(ctx, params):
    assert err_code(run(ctx, "settings.set_syslog", params)) == protocol.INVALID_PARAMS
    assert not remotelog.rsyslog_file(ctx).exists()


def test_syslog_rsyslog_fails(ctx):
    ctx.runner.answers[("systemctl", "restart", "rsyslog.service")] = (1, "", "bad config")
    reply = run(ctx, "settings.set_syslog", {"enabled": True, "host": "nas.local"})
    assert err_code(reply) == protocol.FAILED and "bad config" in reply["error"]["message"]


# --- find a disk ----------------------------------------------------------------

LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "type": "disk", "tran": "sata", "rota": True, "size": 4000787030016}]})


def test_locate(ctx):
    ctx.runner.answers[("lsblk",)] = (0, LSBLK, "")
    out = run(ctx, "disks.locate", {"device": "/dev/sda"})["result"]
    assert out == {"device": "/dev/sda", "seconds": 120}
    cmd = next(c for c in ctx.runner.calls if c[0] == "systemd-run")
    assert "--unit=dahlia-locate.service" in cmd
    assert cmd[-3:] == ["/usr/sbin/dahlia-locate", "/dev/sda", "120"]
    assert ["systemctl", "stop", "dahlia-locate.service"] in ctx.runner.calls
    run(ctx, "disks.locate_stop")
    assert ctx.runner.calls[-1] == ["systemctl", "stop", "dahlia-locate.service"]


def test_locate_rejects(ctx):
    ctx.runner.answers[("lsblk",)] = (0, LSBLK, "")
    assert err_code(run(ctx, "disks.locate", {"device": "/dev/sdb"})) == protocol.FAILED
    assert err_code(run(ctx, "disks.locate", {"device": "/etc/passwd"})) \
        == protocol.INVALID_PARAMS
    assert not any(c[0] == "systemd-run" for c in ctx.runner.calls)


# --- all apps -------------------------------------------------------------------


@pytest.mark.parametrize(("op", "command"), [("start", "start-enabled"), ("stop", "stop-all")])
def test_apps_all(ctx, op, command):
    out = run(ctx, "apps.all", {"op": op})["result"]
    assert out == {"op": op, "started": True}
    cmd = next(c for c in ctx.runner.calls if c[0] == "systemd-run")
    assert "--unit=dahlia-apps-all.service" in cmd
    assert cmd[-2:] == ["/usr/sbin/dahlia-apps", command]


def test_apps_all_busy(ctx):
    ctx.runner.answers[("systemctl", "show", "dahlia-apps-all.service")] = (0, RUNNING, "")
    assert err_code(run(ctx, "apps.all", {"op": "stop"})) == protocol.BUSY
    assert err_code(run(ctx, "apps.all", {"op": "restart"})) == protocol.INVALID_PARAMS


def test_apps_all_job_error(ctx):
    ctx.runner.answers[("systemctl", "show", "dahlia-apps-all.service")] = (
        0, "ActiveState=failed\nSubState=failed\nResult=exit-code\nExecMainStatus=1\n"
           "ExecMainStartTimestamp=Thu 2026-11-12 10:00:00 CET\n", "")
    ctx.runner.answers[("journalctl",)] = (
        0, "dahlia-apps: started kuma\ndahlia-apps: immich: /mnt/user not mounted, stays stopped\n",
        "")
    out = run(ctx, "apps.all_job")["result"]
    assert out["result"] == "exit-code"
    assert out["error"] == "immich: /mnt/user not mounted, stays stopped"
