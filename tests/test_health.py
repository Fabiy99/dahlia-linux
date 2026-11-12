"""system.health of agent: every check against fake files and commands."""

import json
import os

import pytest
from dahlia_agent import actions, health
from test_agent import FakeRunner, run

NOW = 1_800_000_000
DAY = 86400
SMART = ("smartctl", "-j", "-a", "-n", "standby,99")


@pytest.fixture
def ctx(tmp_path):
    c = actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                        var=tmp_path / "var", sys=tmp_path / "sys", proc=tmp_path / "proc",
                        pool=tmp_path / "user", system=tmp_path / "srv",
                        remotes=tmp_path / "remotes", reboot_flag=tmp_path / "reboot-required",
                        clock=lambda: NOW)
    (c.proc / "self").mkdir(parents=True)
    (c.proc / "self/mounts").write_text("")
    c.runner.answers[("dahlia-snapraid",)] = (1, "", "not installed")
    return c


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def healthy(ctx):
    """A server with nothing to report."""
    write(ctx.etc / "apt/apt.conf.d/20auto-upgrades",
          'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n')
    write(ctx.etc / "dahlia/notify.json", json.dumps({"ntfy": {"enabled": True, "topic": "x"}}))
    write(ctx.etc / "dahlia/backup.json", json.dumps({"target": {"type": "folder",
                                                                  "path": "/mnt/disk1/backup"}}))
    write(ctx.var / "backup-status.json", json.dumps({
        "started": "2027-01-15T07:00:00Z", "finished": "2027-01-15T07:10:00Z",
        "result": "success"}))
    ctx.runner.answers[("timedatectl",)] = (0, "NTP=yes\nNTPSynchronized=yes\n", "")


def ids(result):
    return [f["id"] for f in result["findings"]]


def test_all_good(ctx):
    healthy(ctx)
    out = run(ctx, "system.health")["result"]
    assert out["findings"] == [], out
    assert out["passed"] == list(health.CHECKS)
    assert out["skipped"] == []
    assert out["checked"] == NOW


def test_defaults_give_hints(ctx):
    out = run(ctx, "system.health")["result"]
    assert set(ids(out)) == {"auto_updates_off", "backup_none", "notify_none"}
    assert all(f["level"] == "info" for f in out["findings"])


LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "type": "disk", "tran": "sata", "rota": True, "size": 4000787030016},
    {"name": "nvme0n1", "type": "disk", "tran": "nvme", "rota": False, "size": 500107862016},
]})


def test_disks(ctx):
    healthy(ctx)
    ctx.runner.answers[("lsblk",)] = (0, LSBLK, "")
    ctx.runner.answers[(*SMART, "/dev/sda")] = (8, json.dumps({
        "model_name": "WDC WD40", "smart_status": {"passed": False},
        "temperature": {"current": 58}}), "")
    ctx.runner.answers[(*SMART, "/dev/nvme0n1")] = (0, json.dumps({
        "model_name": "Samsung 980", "smart_status": {"passed": True},
        "nvme_smart_health_information_log": {"media_errors": 3}}), "")
    out = run(ctx, "system.health")["result"]
    # critical first, then in the order of the disks
    assert ids(out) == ["smart_failed", "disk_hot", "smart_warning"]
    assert out["findings"][0]["params"] == {"disk": "/dev/sda", "model": "WDC WD40"}
    assert out["findings"][1]["params"] == {"disk": "/dev/sda", "temp": 58, "limit": 55}
    assert out["findings"][2]["params"]["detail"] == "media_errors=3"
    assert "disks" not in out["passed"]


def test_cpu_hot(ctx):
    healthy(ctx)
    write(ctx.sys / "class/hwmon/hwmon0/name", "coretemp\n")
    write(ctx.sys / "class/hwmon/hwmon0/temp1_input", "95000\n")
    out = run(ctx, "system.health")["result"]
    assert out["findings"] == [{"check": "cpu", "id": "cpu_hot", "level": "warning",
                                "params": {"temp": 95, "limit": 90}}]


def test_usage(ctx, monkeypatch):
    healthy(ctx)
    write(ctx.proc / "self/mounts", "/dev/sdb1 /mnt/disk1 xfs rw 0 0\n"
                                    "/dev/sdc1 /mnt/disk2 xfs rw 0 0\n")

    def fake_statvfs(path):
        free = {"/mnt/disk1": 20, "/mnt/disk2": 80}[path]  # of 1000 blocks
        return os.statvfs_result((4096, 4096, 1000, free, free, 0, 0, 0, 0, 255))

    monkeypatch.setattr(actions.os, "statvfs", fake_statvfs)
    out = run(ctx, "system.health")["result"]
    assert [(f["level"], f["params"]["mountpoint"], f["params"]["percent"])
            for f in out["findings"]] == [("critical", "/mnt/disk1", 98),
                                          ("warning", "/mnt/disk2", 92)]


def parity(ctx, **info):
    base = {"configured": True, "problems": [],
            "parity": [{"mountpoint": "/mnt/parity1", "mounted": True}],
            "data": [{"mountpoint": "/mnt/disk1", "mounted": True}],
            "status": {"bad": 0}, "runs": {"sync": {"end": NOW - DAY, "result": "ok"}}}
    base.update(info)
    ctx.runner.answers[("dahlia-snapraid",)] = (0, json.dumps(base), "")


def test_parity_good(ctx):
    healthy(ctx)
    parity(ctx)
    out = run(ctx, "system.health")["result"]
    assert out["findings"] == []
    assert ["dahlia-snapraid", "status", "--json"] in ctx.runner.calls


def test_parity_problems(ctx):
    healthy(ctx)
    parity(ctx, data=[{"mountpoint": "/mnt/disk1", "mounted": False}], status={"bad": 12},
           runs={"sync": {"end": NOW - 10 * DAY, "result": "blocked", "message": "too many"}})
    out = run(ctx, "system.health")["result"]
    assert ids(out) == ["disk_missing", "parity_bad", "parity_failed", "parity_old"]
    assert out["findings"][0]["params"] == {"mountpoint": "/mnt/disk1"}
    assert out["findings"][3]["params"] == {"days": 10}


def test_parity_not_set_up(ctx):
    healthy(ctx)
    parity(ctx, parity=[])
    assert ids(run(ctx, "system.health")["result"]) == ["parity_none"]
    parity(ctx, configured=False, problems=["parity disk smaller than disk1"])
    out = run(ctx, "system.health")["result"]
    assert ids(out) == ["parity_problem"]
    assert out["findings"][0]["params"]["detail"] == "parity disk smaller than disk1"
    parity(ctx, runs={})
    assert ids(run(ctx, "system.health")["result"]) == ["parity_never"]
    parity(ctx, data=[], parity=[])
    assert ids(run(ctx, "system.health")["result"]) == []


def test_services_and_time(ctx):
    healthy(ctx)
    ctx.runner.answers[("systemctl", "list-units")] = (
        0, "mover.service loaded failed failed Dahlia mover\n"
           "smbd.service loaded failed failed Samba\n", "")
    ctx.runner.answers[("timedatectl",)] = (0, "NTP=yes\nNTPSynchronized=no\n", "")
    out = run(ctx, "system.health")["result"]
    assert ids(out) == ["units_failed", "time_unsynced"]
    assert out["findings"][0]["params"] == {"units": "mover.service, smbd.service",
                                            "count": 2}


def test_manual_time_is_fine(ctx):
    healthy(ctx)
    ctx.runner.answers[("timedatectl",)] = (0, "NTP=no\nNTPSynchronized=no\n", "")
    assert ids(run(ctx, "system.health")["result"]) == []


def test_reboot_required(ctx):
    healthy(ctx)
    ctx.reboot_flag.write_text("")
    ctx.reboot_flag.with_name("reboot-required.pkgs").write_text("linux-image-amd64\n")
    out = run(ctx, "system.health")["result"]
    assert out["findings"][0]["id"] == "reboot_required"
    assert out["findings"][0]["params"] == {"packages": "linux-image-amd64"}
    # automatic reboot on: the server restarts itself
    write(ctx.etc / "dahlia/updates.json", json.dumps({"reboot": True}))
    assert ids(run(ctx, "system.health")["result"]) == []


def test_backup(ctx):
    healthy(ctx)
    write(ctx.var / "backup-status.json", json.dumps({
        "started": "2027-01-01T07:00:00Z", "finished": "2027-01-01T07:01:00Z",
        "result": "failed", "error": "target not reachable"}))
    out = run(ctx, "system.health")["result"]
    assert ids(out) == ["backup_failed", "backup_old"]
    assert out["findings"][0]["params"] == {"error": "target not reachable"}
    (ctx.var / "backup-status.json").unlink()
    assert ids(run(ctx, "system.health")["result"]) == ["backup_never"]


def test_firewall_off(ctx):
    healthy(ctx)
    write(ctx.etc / "dahlia/firewall.json", json.dumps({"enabled": False}))
    assert ids(run(ctx, "system.health")["result"]) == ["firewall_off"]


def test_broken_check_is_skipped(ctx, monkeypatch):
    healthy(ctx)

    def boom(_ctx):
        raise RuntimeError("broken")

    monkeypatch.setattr(health, "check_time", boom)
    out = run(ctx, "system.health")["result"]
    assert out["skipped"] == ["time"]
    assert "time" not in out["passed"]
