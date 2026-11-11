import json
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest
from dahlia_agent import actions, client, protocol, server
from dahlia_agent.protocol import AgentError


class FakeRunner:
    """Records commands and answers from a table keyed by the command prefix."""

    def __init__(self, answers=None):
        self.calls = []
        self.inputs = []
        self.answers = answers or {}

    def run(self, cmd, *, timeout=120, check=True, env=None, input=None):
        self.calls.append(cmd)
        self.inputs.append(input)
        for prefix, (code, out, err) in self.answers.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                proc = subprocess.CompletedProcess(cmd, code, out, err)
                if check and code != 0:
                    raise AgentError(protocol.FAILED, actions.command_error(cmd, proc))
                return proc
        return subprocess.CompletedProcess(cmd, 0, "", "")


INACTIVE = ("ActiveState=inactive\nSubState=dead\nResult=success\nExecMainStatus=0\n"
            "ExecMainStartTimestamp=Tue 2026-11-10 19:33:40 CET\n")
NEVER_RAN = "ActiveState=inactive\nSubState=dead\nResult=success\nExecMainStartTimestamp=\n"
RUNNING = "ActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0\n"


@pytest.fixture
def ctx(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("12345.67 100.00\n")
    (proc / "loadavg").write_text("0.10 0.20 0.30 1/100 4242\n")
    (proc / "meminfo").write_text("MemTotal:  2048 kB\nMemFree: 100 kB\nMemAvailable: 1024 kB\n")
    release = tmp_path / "release"
    release.write_text("DAHLIA_VERSION=0.2.0\n")
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\tdahlia\n::1\tlocalhost\n")
    runner = FakeRunner({("systemctl", "show"): (0, INACTIVE, "")})
    return actions.Context(runner=runner, proc=proc, release_file=release, hosts_file=hosts,
                           run_dir=tmp_path / "run")


def run(ctx, action, params=None):
    _, reply = server.dispatch(
        ctx, json.dumps({"action": action, "params": params or {}}).encode(), threading.Lock()
    )
    return reply


def err_code(reply):
    assert reply["ok"] is False
    return reply["error"]["code"]


# --- protocol -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"[1]", b'{"action": 1}', b'{"action": "x", "params": []}',
     b'{"action": "x", "extra": 1}', b"\xff"],
)
def test_bad_requests(ctx, raw):
    _, reply = server.dispatch(ctx, raw, threading.Lock())
    assert err_code(reply) == protocol.BAD_REQUEST


def test_unknown_action(ctx):
    assert err_code(run(ctx, "system.shell", {"cmd": "id"})) == protocol.UNKNOWN_ACTION


def test_parse_response():
    assert protocol.parse_response(b'{"ok": true, "result": 5}') == 5
    with pytest.raises(AgentError) as e:
        protocol.parse_response(b'{"ok": false, "error": {"code": "busy", "message": "m"}}')
    assert e.value.code == "busy"
    with pytest.raises(AgentError):
        protocol.parse_response(b'{"result": 5}')


# --- parameter checks ---------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"uuid": "1234-ABCD"},
        {"uuid": "../../etc", "role": "data"},
        {"uuid": "1234-ABCD; rm -rf /", "role": "data"},
        {"uuid": "-rf", "role": "data"},
        {"uuid": "1234-ABCD", "role": "boot"},
        {"uuid": 1234, "role": "data"},
        {"uuid": "1234-ABCD", "role": "data", "format": True},
    ],
)
def test_storage_add_rejects(ctx, params):
    assert err_code(run(ctx, "storage.add", params)) == protocol.INVALID_PARAMS
    assert ctx.runner.calls == []


def test_storage_add_runs_dahlia_disk(ctx):
    uuid = "0b9f3c4e-1a2b-4c5d-8e9f-0123456789ab"
    reply = run(ctx, "storage.add", {"uuid": uuid, "role": "data"})
    assert reply == {"ok": True, "result": {"uuid": uuid, "role": "data"}}
    assert ctx.runner.calls == [["dahlia-disk", "add", uuid, "--role", "data"]]


def test_storage_add_accepts_ntfs_serial(ctx):
    assert run(ctx, "storage.add", {"uuid": "1A2B3C4D5E6F7A8B", "role": "external"})["ok"]


@pytest.mark.parametrize("params", [
    {"device": "/dev/sdb", "filesystem": "xfs"},
    {"device": "/dev/sdb", "filesystem": "xfs", "confirm": "sdc"},
    {"device": "/dev/sdb1", "filesystem": "xfs", "confirm": "sdb1"},
    {"device": "/dev/../sdb", "filesystem": "xfs", "confirm": "../sdb"},
    {"device": "/dev/sdb", "filesystem": "ntfs", "confirm": "sdb"},
    {"device": "/dev/nvme0n1p1", "filesystem": "ext4", "confirm": "nvme0n1p1"},
    {"device": "sdb", "filesystem": "ext4", "confirm": "sdb"},
])
def test_storage_format_rejects(ctx, params):
    assert err_code(run(ctx, "storage.format", params)) == protocol.INVALID_PARAMS
    assert ctx.runner.calls == []


def test_storage_format_runs_dahlia_disk(ctx):
    out = '{"device": "/dev/sdb", "partition": "/dev/sdb1", "fstype": "xfs", "uuid": "1234-ABCD"}'
    ctx.runner.answers[("dahlia-disk", "format")] = (0, out, "")
    params = {"device": "/dev/sdb", "filesystem": "xfs", "confirm": "sdb"}
    reply = run(ctx, "storage.format", params)
    assert reply["result"]["uuid"] == "1234-ABCD"
    assert ctx.runner.calls == [["dahlia-disk", "format", "/dev/sdb", "--fs", "xfs", "--json"]]
    assert run(ctx, "storage.format", {"device": "/dev/nvme0n1", "filesystem": "btrfs",
                                       "confirm": "nvme0n1"})["ok"]


def test_storage_import_starts_background_job(ctx):
    for bad in [{"uuid": "1234-ABCD", "target": "../etc"}, {"uuid": "x;y", "target": "old"},
                {"uuid": "1234-ABCD", "target": "-rf"}]:
        assert err_code(run(ctx, "storage.import", bad)) == protocol.INVALID_PARAMS
    assert ctx.runner.calls == []
    res = run(ctx, "storage.import", {"uuid": "1234-ABCD", "target": "old-sdb"})["result"]
    assert res == {"uuid": "1234-ABCD", "target": "/mnt/user/old-sdb"}
    assert ["dahlia-disk", "import", "1234-ABCD", "--to", "old-sdb", "--check"] in ctx.runner.calls
    job = ctx.runner.calls[-1]
    assert job[:2] == ["systemd-run", "--unit=dahlia-import"]
    assert job[-5:] == ["dahlia-disk", "import", "1234-ABCD", "--to", "old-sdb"]


def test_storage_import_refuses_second_job_and_bad_source(ctx):
    ctx.runner.answers[("systemctl", "is-active")] = (0, "active\n", "")
    assert err_code(run(ctx, "storage.import", {"uuid": "1234-ABCD", "target": "a"})) == \
        protocol.BUSY
    ctx.runner.answers[("systemctl", "is-active")] = (3, "inactive\n", "")
    ctx.runner.answers[("dahlia-disk", "import")] = (1, "", "dahlia-disk: not mounted\n")
    reply = run(ctx, "storage.import", {"uuid": "1234-ABCD", "target": "a"})
    assert err_code(reply) == protocol.FAILED and "not mounted" in reply["error"]["message"]
    assert not any(c[0] == "systemd-run" for c in ctx.runner.calls)


def test_storage_import_status(ctx, tmp_path):
    ctx.run_dir = tmp_path
    assert run(ctx, "storage.import_status")["result"] == {"import": None}
    (tmp_path / "import.json").write_text('{"phase": "copy", "percent": 40, "error": ""}')
    ctx.runner.answers[("systemctl", "is-active")] = (0, "active\n", "")
    assert run(ctx, "storage.import_status")["result"]["import"]["phase"] == "copy"
    ctx.runner.answers[("systemctl", "is-active")] = (3, "inactive\n", "")
    got = run(ctx, "storage.import_status")["result"]["import"]
    assert got["phase"] == "failed" and got["error"] == "import stopped"


def test_storage_remove_force_must_be_bool(ctx):
    params = {"uuid": "1234-ABCD", "force": "yes"}
    assert err_code(run(ctx, "storage.remove", params)) == protocol.INVALID_PARAMS
    assert run(ctx, "storage.remove", {"uuid": "1234-ABCD", "force": True})["ok"]
    assert ctx.runner.calls[-1] == ["dahlia-disk", "remove", "1234-ABCD", "--force"]
    assert run(ctx, "storage.remove", {"uuid": "1234-ABCD"})["ok"]
    assert ctx.runner.calls[-1] == ["dahlia-disk", "remove", "1234-ABCD"]


def test_storage_set_option(ctx):
    bad = [
        {"name": "minfreespace", "value": 1},
        {"name": "scrub_percent", "value": "8"},
        {"name": "scrub_percent", "value": True},
        {"name": "scrub_percent", "value": -1},
    ]
    for params in bad:
        assert err_code(run(ctx, "storage.set_option", params)) == protocol.INVALID_PARAMS
    assert run(ctx, "storage.set_option", {"name": "scrub_percent", "value": 12})["ok"]
    assert ctx.runner.calls == [["dahlia-disk", "set-option", "scrub_percent", "12"]]


def test_storage_settings(ctx):
    ctx.runner.answers[("dahlia-disk", "settings")] = (0, '{"minfreespace": "50G"}', "")
    assert run(ctx, "storage.settings")["result"] == {"settings": {"minfreespace": "50G"}}


def test_storage_set_minfreespace(ctx):
    for size in ("50", "0G", "50g", "1P", "50G; rm", "1234567G", 50):
        reply = run(ctx, "storage.set_minfreespace", {"size": size})
        assert err_code(reply) == protocol.INVALID_PARAMS, size
    assert run(ctx, "storage.set_minfreespace", {"size": "100G"})["ok"]
    assert ctx.runner.calls == [["dahlia-disk", "set-minfreespace", "100G"]]


def test_storage_list_parses_json(ctx):
    ctx.runner.answers[("dahlia-disk", "list")] = (0, '[{"path": "/dev/sdb"}]', "")
    assert run(ctx, "storage.list")["result"] == {"disks": [{"path": "/dev/sdb"}]}


def test_command_failure_is_reported(ctx):
    ctx.runner.answers[("dahlia-disk", "add")] = (1, "", "dahlia-disk: no filesystem with UUID x\n")
    reply = run(ctx, "storage.add", {"uuid": "1234-ABCD", "role": "data"})
    assert err_code(reply) == protocol.FAILED
    assert "no filesystem with UUID" in reply["error"]["message"]


# --- system -------------------------------------------------------------------


def test_system_info(ctx):
    info = run(ctx, "system.info")["result"]
    assert info["version"] == "0.2.0"
    assert info["uptime_seconds"] == 12345
    assert info["load"] == [0.1, 0.2, 0.3]
    assert info["memory"] == {"total": 2048 * 1024, "available": 1024 * 1024}
    assert info["hostname"] == socket.gethostname()


def test_system_info_without_release(ctx):
    ctx.release_file.unlink()
    assert run(ctx, "system.info")["result"]["version"] is None


CPUINFO = """processor\t: 0
model name\t: AMD Ryzen 5 5600G with Radeon Graphics
processor\t: 1
model name\t: AMD Ryzen 5 5600G with Radeon Graphics
"""

NETDEV = """Inter-|   Receive                            |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets ...
    lo:    1000      10    0    0    0     0          0         0     1000      10 0 0 0 0 0 0
enp1s0: 5000000    4000    0    0    0     0          0         0   700000    3000 0 0 0 0 0 0
docker0:    10       1    0    0    0     0          0         0       20       1 0 0 0 0 0 0
veth12ab:    10       1    0    0    0     0          0         0       20       1 0 0 0 0 0 0
"""

IP_ADDR = json.dumps([
    {"ifname": "enp1s0", "addr_info": [{"family": "inet", "local": "192.168.1.10",
                                        "prefixlen": 24}]},
    {"ifname": "docker0", "addr_info": [{"family": "inet", "local": "172.17.0.1",
                                         "prefixlen": 16}]},
])


def test_system_stats(ctx, monkeypatch):
    (ctx.proc / "cpuinfo").write_text(CPUINFO)
    (ctx.proc / "stat").write_text("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8\n")
    with (ctx.proc / "meminfo").open("a") as f:
        f.write("SwapTotal: 4096 kB\nSwapFree: 1024 kB\n")
    (ctx.proc / "net").mkdir()
    (ctx.proc / "net/dev").write_text(NETDEV)
    (ctx.proc / "self").mkdir()
    (ctx.proc / "self/mounts").write_text(
        "/dev/sda2 / btrfs rw,subvol=/@rootfs 0 0\n"
        "proc /proc proc rw 0 0\n"
        "/dev/sdb1 /mnt/disk1 xfs rw 0 0\n"
        "disk1 /mnt/storage fuse.mergerfs rw 0 0\n"
        "/dev/sdc1 /mnt/external/My\\040Disk ntfs3 rw 0 0\n"
        "/dev/sda2 / btrfs rw,subvol=/@snap 0 0\n"
        "/dev/sdd1 /mnt/gone ext4 rw 0 0\n"
    )
    real_statvfs = os.statvfs

    def fake_statvfs(path):
        if path == "/mnt/gone":
            raise OSError("stale")
        if path.startswith("/mnt/"):
            return os.statvfs_result((4096, 4096, 1000, 400, 300, 0, 0, 0, 0, 255))
        return real_statvfs(path)

    monkeypatch.setattr(actions.os, "statvfs", fake_statvfs)
    ctx.runner.answers[("ip", "-j")] = (0, IP_ADDR, "")
    s = run(ctx, "system.stats")["result"]
    assert s["uptime_seconds"] == 12345
    assert s["cpu"] == {"model": "AMD Ryzen 5 5600G with Radeon Graphics", "cores": 2,
                        "total": 1000, "idle": 850}
    assert s["swap"] == {"total": 4096 * 1024, "used": 3072 * 1024}
    assert s["network"] == [{"name": "enp1s0", "rx_bytes": 5000000, "tx_bytes": 700000,
                             "addresses": ["192.168.1.10/24"]}]
    fs = {f["mountpoint"]: f for f in s["filesystems"]}
    assert list(fs) == ["/", "/mnt/disk1", "/mnt/storage", "/mnt/external/My Disk"]
    assert fs["/mnt/disk1"] == {"mountpoint": "/mnt/disk1", "source": "/dev/sdb1",
                                "fstype": "xfs", "size": 4096000, "used": 2457600,
                                "free": 1228800}
    assert fs["/"]["source"] == "/dev/sda2"


def test_system_stats_without_ip_output(ctx):
    (ctx.proc / "cpuinfo").write_text("")
    (ctx.proc / "stat").write_text("cpu  1 2 3 4 5 6 7 8\n")
    (ctx.proc / "net").mkdir()
    (ctx.proc / "net/dev").write_text(NETDEV)
    (ctx.proc / "self").mkdir()
    (ctx.proc / "self/mounts").write_text("")
    ctx.runner.answers[("ip", "-j")] = (1, "", "")
    s = run(ctx, "system.stats")["result"]
    assert s["cpu"]["model"] is None
    assert s["network"][0]["addresses"] == []
    assert s["filesystems"] == []
    assert s["swap"] == {"total": 0, "used": 0}


@pytest.mark.parametrize("name", ["", "Dahlia", "-x", "x-", "a.b", "a" * 64, "dahlia.local", "ä"])
def test_hostname_rejected(ctx, name):
    reply = run(ctx, "system.set_hostname", {"hostname": name})
    assert err_code(reply) == protocol.INVALID_PARAMS


def test_set_hostname(ctx):
    assert run(ctx, "system.set_hostname", {"hostname": "nas-01"})["ok"]
    assert ctx.runner.calls[0] == ["hostnamectl", "set-hostname", "nas-01"]
    assert ["systemctl", "try-restart", "avahi-daemon.service"] in ctx.runner.calls
    assert ctx.hosts_file.read_text() == "127.0.0.1\tlocalhost\n127.0.1.1\tnas-01\n::1\tlocalhost\n"
    # New certificate for nas-01.local.
    assert ctx.runner.calls[-1] == ["dahlia-domains", "apply"]


def test_rewrite_hosts():
    text = "127.0.0.1 localhost\n"
    assert actions.rewrite_hosts(text, "x") == "127.0.0.1 localhost\n127.0.1.1\tx\n"
    text = "127.0.1.1 a\n# 127.0.1.1 comment\n127.0.1.1 b\n"
    assert actions.rewrite_hosts(text, "x") == "127.0.1.1\tx\n# 127.0.1.1 comment\n"


def test_reboot_is_non_blocking(ctx):
    assert run(ctx, "system.reboot")["ok"]
    assert ctx.runner.calls == [["systemctl", "--no-block", "reboot"]]


# --- jobs ---------------------------------------------------------------------


def test_job_start(ctx):
    assert run(ctx, "job.start", {"job": "mover"})["ok"]
    assert ctx.runner.calls[-1] == ["systemctl", "start", "--no-block", "dahlia-mover.service"]


def test_job_start_refuses_update_and_unknown(ctx):
    for job in ("update", "sshd", "dahlia-mover.service"):
        assert err_code(run(ctx, "job.start", {"job": job})) == protocol.INVALID_PARAMS


def test_job_start_busy(ctx):
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    assert err_code(run(ctx, "job.start", {"job": "snapraid-sync"})) == protocol.BUSY


def test_parity_get(ctx):
    ctx.runner.answers[("dahlia-snapraid", "status", "--json")] = (
        0, json.dumps({"configured": True, "problems": [], "status": {"bad": 0}}), "")
    info = run(ctx, "parity.get")["result"]
    assert info["configured"] and info["status"] == {"bad": 0}
    assert set(info["jobs"]) == {"sync", "scrub", "mover", "repair"}
    assert ["systemctl", "show", "dahlia-parity.service"] in [c[:3] for c in ctx.runner.calls]


def test_parity_sync_plain_and_forced(ctx):
    assert run(ctx, "parity.sync")["ok"]
    assert ctx.runner.calls[-1] == ["systemctl", "start", "--no-block",
                                    "dahlia-snapraid-sync.service"]
    assert run(ctx, "parity.sync", {"force": True})["result"]["force"]
    cmd = ctx.runner.calls[-1]
    assert cmd[:3] == ["systemd-run", "--unit", "dahlia-parity.service"]
    assert cmd[-3:] == ["/usr/sbin/dahlia-snapraid", "sync", "--force"]
    assert "OnFailure=dahlia-notify-failure@dahlia-parity.service.service" in cmd


def test_parity_fix(ctx):
    assert run(ctx, "parity.fix", {"what": "disk2"})["ok"]
    assert ctx.runner.calls[-1][-4:] == ["/usr/sbin/dahlia-snapraid", "fix", "--disk", "2"]
    assert run(ctx, "parity.fix", {"what": "bad"})["ok"]
    assert ctx.runner.calls[-1][-3:] == ["/usr/sbin/dahlia-snapraid", "fix", "--bad"]
    for what in ("disk0", "disk", "d2", "disk2;reboot", "all"):
        assert err_code(run(ctx, "parity.fix", {"what": what})) == protocol.INVALID_PARAMS


def test_parity_refuses_while_a_job_runs(ctx):
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    assert err_code(run(ctx, "parity.fix", {"what": "bad"})) == protocol.BUSY
    assert err_code(run(ctx, "parity.sync", {"force": True})) == protocol.BUSY
    assert err_code(run(ctx, "parity.sync")) == protocol.BUSY
    assert not any(c[0] == "systemd-run" for c in ctx.runner.calls)


def test_job_status(ctx):
    status = run(ctx, "job.status", {"job": "update"})["result"]
    assert status["job"] == "update"
    assert status["active"] == "inactive"
    assert status["result"] == "success"
    assert status["exit_status"] == 0
    assert ctx.runner.calls[0][:3] == ["systemctl", "show", "dahlia-update.service"]
    assert status["started"] == "Tue 2026-11-10 19:33:40 CET"


def test_job_status_never_ran(ctx):
    ctx.runner.answers[("systemctl", "show")] = (0, NEVER_RAN, "")
    status = run(ctx, "job.status", {"job": "mover"})["result"]
    assert status["result"] == ""
    assert status["started"] == ""


def test_app_job_keeps_result_of_collected_unit(ctx):
    # Transient app units are gone after they finish; their result stays as
    # systemd reports it.
    ctx.runner.answers[("systemctl", "show")] = (0, NEVER_RAN, "")
    assert run(ctx, "apps.job", {"id": "whoami"})["result"]["result"] == "success"


# --- updates ------------------------------------------------------------------

SIMULATION = """\
NOTE: This is only a simulation!
Inst libc6 [2.41-12] (2.41-12+deb13u1 Debian:13.2/stable [amd64])
Inst dahlia-base [0.1.2] (0.1.3 fabiy99.github.io [all])
Inst newpkg (1.0 Debian:13.2/stable [amd64])
Conf libc6 (2.41-12+deb13u1 Debian:13.2/stable [amd64])
"""


def test_parse_simulation():
    pkgs = actions.parse_simulation(SIMULATION)
    assert pkgs == [
        {"name": "libc6", "current": "2.41-12", "new": "2.41-12+deb13u1",
         "origin": "Debian:13.2/stable"},
        {"name": "dahlia-base", "current": "0.1.2", "new": "0.1.3", "origin": "fabiy99.github.io"},
        {"name": "newpkg", "current": None, "new": "1.0", "origin": "Debian:13.2/stable"},
    ]


def test_updates_check(ctx):
    ctx.runner.answers[("apt-get", "-s")] = (0, SIMULATION, "")
    result = run(ctx, "updates.check")["result"]
    assert [p["name"] for p in result["packages"]] == ["libc6", "dahlia-base", "newpkg"]
    assert ["apt-get", "update", "-q"] in ctx.runner.calls


OLD_RUN = "0" * 31 + "1"
NEW_RUN = "0" * 31 + "2"
UPDATE_LOG = """\
Reading package lists...
Get:1 http://deb.debian.org/debian trixie/main amd64 libc6 amd64 2.41-12+deb13u1 [2,800 kB]
Get:2 https://fabiy99.github.io/dahlia-linux trixie/main amd64 dahlia-base all 0.1.3 [4 kB]
Unpacking libc6:amd64 (2.41-12+deb13u1) over (2.41-12) ...
Setting up libc6:amd64 (2.41-12+deb13u1) ...
Unpacking dahlia-base (0.1.3) over (0.1.2) ...
"""


def answer_first(ctx, prefix, answer):
    # FakeRunner takes the first matching prefix; a specific one goes before ("systemctl", "show").
    rest = {k: v for k, v in ctx.runner.answers.items() if k != prefix}
    ctx.runner.answers = {prefix: answer, **rest}


def set_invocation(ctx, run_id):
    answer_first(ctx, ("systemctl", "show", "-p", "InvocationID"), (0, run_id + "\n", ""))


def started_update(ctx, clock=1000.0):
    ctx.clock = lambda: clock
    ctx.runner.answers[("apt-get", "-s")] = (0, SIMULATION, "")
    set_invocation(ctx, OLD_RUN)
    assert run(ctx, "updates.apply")["result"]["packages"] == 3


def test_updates_apply(ctx):
    started_update(ctx)
    assert ["systemctl", "start", "--no-block", "dahlia-update.service"] in ctx.runner.calls
    plan = json.loads((ctx.run_dir / "update.json").read_text())
    assert plan == {"packages": ["libc6", "dahlia-base", "newpkg"], "started": 1000,
                    "previous": OLD_RUN}


def test_updates_progress_none(ctx):
    assert run(ctx, "updates.progress")["result"] == {"state": "none"}


def test_updates_progress_starting(ctx):
    started_update(ctx)
    res = run(ctx, "updates.progress")["result"]
    assert res["state"] == "running" and res["percent"] == 0
    assert not any(c[0] == "journalctl" for c in ctx.runner.calls)
    # systemd never started it.
    ctx.clock = lambda: 1000.0 + actions.START_GRACE + 1
    assert run(ctx, "updates.progress")["result"]["state"] == "failed"


def test_updates_progress_running(ctx):
    started_update(ctx)
    set_invocation(ctx, NEW_RUN)
    answer_first(ctx, ("systemctl", "show", "dahlia-update.service"), (0, RUNNING, ""))
    ctx.runner.answers[("journalctl",)] = (0, UPDATE_LOG, "")
    res = run(ctx, "updates.progress")["result"]
    assert res["state"] == "running"
    assert res["packages"] == [{"name": "libc6", "step": "done"},
                               {"name": "dahlia-base", "step": "unpacked"},
                               {"name": "newpkg", "step": "waiting"}]
    assert res["percent"] == (3 + 2) * 100 // 9
    assert res["last"].startswith("Unpacking dahlia-base")
    assert ["journalctl", f"_SYSTEMD_INVOCATION_ID={NEW_RUN}", "-o", "cat",
            "--no-pager"] in ctx.runner.calls


def test_updates_progress_finished(ctx):
    started_update(ctx)
    set_invocation(ctx, NEW_RUN)
    answer_first(ctx, ("systemctl", "show", "dahlia-update.service"), (0, INACTIVE, ""))
    assert run(ctx, "updates.progress")["result"]["state"] == "done"
    assert run(ctx, "updates.progress")["result"]["percent"] == 100
    failed = INACTIVE.replace("Result=success", "Result=exit-code")
    answer_first(ctx, ("systemctl", "show", "dahlia-update.service"), (0, failed, ""))
    assert run(ctx, "updates.progress")["result"]["state"] == "failed"


def test_updates_apply_busy(ctx):
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    assert err_code(run(ctx, "updates.apply")) == protocol.BUSY
    assert not any(c[:2] == ["systemctl", "start"] for c in ctx.runner.calls)


# --- domains ------------------------------------------------------------------


def test_domains_get(ctx):
    ctx.runner.answers[("dahlia-domains", "show")] = (0, '{"mdns_aliases": true}', "")
    assert run(ctx, "domains.get")["result"] == {"mdns_aliases": True}


def test_domains_set(ctx):
    ctx.runner.answers[("dahlia-domains", "set")] = (0, '{"ca_renewed": false}', "")
    assert run(ctx, "domains.set", {"mdns_aliases": False})["ok"]
    assert ctx.runner.calls[-1] == ["dahlia-domains", "set", "--mdns", "off"]


def test_domains_set_rejects_domain(ctx):
    # The app domain is gone; only the mDNS switch is left.
    reply = run(ctx, "domains.set", {"domain": "lab.home.arpa", "mdns_aliases": True})
    assert err_code(reply) == protocol.INVALID_PARAMS
    assert err_code(run(ctx, "domains.set", {"mdns_aliases": "yes"})) == protocol.INVALID_PARAMS
    assert not ctx.runner.calls


def test_domains_set_error_passes_through(ctx):
    ctx.runner.answers[("dahlia-domains", "set")] = (1, "", "dahlia-domains: nginx broken\n")
    reply = run(ctx, "domains.set", {"mdns_aliases": True})
    assert err_code(reply) == protocol.FAILED
    assert "nginx broken" in reply["error"]["message"]


# --- action list --------------------------------------------------------------


def test_action_list_is_fixed():
    assert sorted(actions.ACTIONS) == [
        "agent.actions",
        "apps.catalog",
        "apps.install",
        "apps.job",
        "apps.list",
        "apps.remove",
        "apps.start",
        "apps.stop",
        "apps.update",
        "backup.get",
        "backup.key",
        "backup.restore",
        "backup.set",
        "backup.snapshots",
        "docker.container",
        "docker.disk",
        "docker.list",
        "docker.logs",
        "docker.prune",
        "docker.stats",
        "domains.get",
        "domains.set",
        "files.browse",
        "files.mkdir",
        "job.start",
        "job.status",
        "notify.get",
        "notify.set",
        "notify.set_events",
        "notify.test",
        "parity.fix",
        "parity.get",
        "parity.sync",
        "settings.confirm_network",
        "settings.firewall",
        "settings.network",
        "settings.schedules",
        "settings.set_firewall",
        "settings.set_network",
        "settings.set_schedule",
        "settings.set_spindown",
        "settings.set_time",
        "settings.spindown",
        "settings.time",
        "shares.list",
        "shares.remove",
        "shares.set",
        "shares.user_add",
        "shares.user_password",
        "shares.user_remove",
        "storage.add",
        "storage.format",
        "storage.import",
        "storage.import_status",
        "storage.list",
        "storage.remove",
        "storage.set_minfreespace",
        "storage.set_option",
        "storage.settings",
        "system.diagnostics",
        "system.hardware",
        "system.info",
        "system.log",
        "system.poweroff",
        "system.reboot",
        "system.set_hostname",
        "system.stats",
        "updates.apply",
        "updates.auto",
        "updates.check",
        "updates.progress",
        "updates.set_auto",
        "ups.get",
        "ups.set",
    ]
    assert all(a.description for a in actions.ACTIONS.values())


def test_agent_actions(ctx):
    listed = {a["name"]: a for a in run(ctx, "agent.actions")["result"]}
    assert listed["storage.add"]["params"]["role"]["choices"] == list(actions.DISK_ROLES)
    assert listed["storage.add"]["mutating"] is True


def test_internal_error_hides_details(ctx, capsys):
    (ctx.proc / "uptime").unlink()
    reply = run(ctx, "system.info")
    assert err_code(reply) == protocol.INTERNAL
    assert "uptime" not in reply["error"]["message"]
    assert "FileNotFoundError" in capsys.readouterr().err


# --- socket server ------------------------------------------------------------


def start_server(tmp_path, ctx, authorize):
    path = str(tmp_path / "agent.sock")
    sock = server.bind_socket(path, None)
    srv = server.Server(sock, ctx, authorize)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return path


def test_socket_roundtrip(tmp_path, ctx):
    path = start_server(tmp_path, ctx, lambda pid, uid, gid: uid == os.getuid())
    assert oct(os.stat(path).st_mode & 0o777) == "0o660"
    info = client.call("system.info", path=path, timeout=5)
    assert info["version"] == "0.2.0"
    with pytest.raises(AgentError) as e:
        client.call("storage.add", {"uuid": "x;y", "role": "data"}, path=path, timeout=5)
    assert e.value.code == protocol.INVALID_PARAMS


def test_socket_forbidden(tmp_path, ctx):
    seen = []

    def deny(pid, uid, gid):
        seen.append((pid, uid))
        return False

    path = start_server(tmp_path, ctx, deny)
    with pytest.raises(AgentError) as e:
        client.call("system.info", path=path, timeout=5)
    assert e.value.code == protocol.FORBIDDEN
    assert seen == [(os.getpid(), os.getuid())]


def test_socket_oversized_request(tmp_path, ctx):
    path = start_server(tmp_path, ctx, lambda *a: True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(path)
        s.sendall(b"x" * (protocol.MAX_REQUEST + 10))
        reply = s.recv(4096)
    assert json.loads(reply)["error"]["code"] == protocol.BAD_REQUEST


def test_client_without_agent(tmp_path):
    with pytest.raises(AgentError) as e:
        client.call("system.info", path=str(tmp_path / "missing.sock"), timeout=1)
    assert e.value.code == protocol.UNAVAILABLE


def test_group_authorizer(tmp_path):
    proc = tmp_path / "proc"
    (proc / "10").mkdir(parents=True)
    (proc / "10" / "status").write_text("Name:\tx\nGroups:\t27 999 \n")
    (proc / "11").mkdir()
    (proc / "11" / "status").write_text("Name:\tx\nGroups:\t27\n")
    auth = server.group_authorizer(999, proc)
    assert auth(1, 0, 0)  # root
    assert auth(11, 1000, 999)  # primary group
    assert auth(10, 1000, 1000)  # supplementary group
    assert not auth(11, 1000, 1000)
    assert not auth(12, 1000, 1000)  # process gone


def test_client_cli(tmp_path, ctx, capsys):
    path = start_server(tmp_path, ctx, lambda *a: True)
    assert client.main(["--socket", path, "job.status", '{"job": "mover"}']) == 0
    assert json.loads(capsys.readouterr().out)["active"] == "inactive"
    assert client.main(["--socket", path, "job.status", '{"job": "x"}']) == 1
    assert "invalid_params" in capsys.readouterr().err
    assert client.main(["--socket", path, "job.status", "[]"]) == 2


def test_entry_points_exist():
    root = Path(__file__).resolve().parent.parent / "packages" / "dahlia-agent"
    for exe in ("usr/sbin/dahlia-agent", "usr/bin/dahlia-agent-call"):
        assert os.access(root / exe, os.X_OK)


# --- apps ---------------------------------------------------------------------


def test_apps_install_runs_in_background(ctx):
    assert run(ctx, "apps.install", {"id": "jellyfin"})["ok"]
    cmd = ctx.runner.calls[-1]
    assert cmd[:2] == ["systemd-run", "--unit=dahlia-app-jellyfin.service"]
    assert cmd[-3:] == ["/usr/sbin/dahlia-apps", "install", "jellyfin"]


def test_apps_install_with_folders(ctx):
    params = {"id": "jellyfin", "location": "/srv",
              "folders": {"media": "/mnt/user/Filme und Serien", "photos": "/srv/Fotos/2024"}}
    assert run(ctx, "apps.install", params)["ok"]
    extra = ["--folder", "media=/mnt/user/Filme und Serien", "--folder", "photos=/srv/Fotos/2024",
             "--location", "/srv"]
    assert ["dahlia-apps", "precheck", "install", "jellyfin", *extra] in ctx.runner.calls
    assert ctx.runner.calls[-1][-7:] == ["jellyfin", *extra]
    ctx.runner.calls.clear()
    assert run(ctx, "apps.install", {"id": "jellyfin"})["ok"]
    assert ["dahlia-apps", "precheck", "install", "jellyfin"] in ctx.runner.calls
    reply = run(ctx, "apps.install", {"id": "jellyfin", "location": "/media/usb"})
    assert err_code(reply) == protocol.INVALID_PARAMS
    for bad in ({"media": "/mnt/user/../etc"}, {"media": "/etc"}, {"media": "/srv/a/../../b"},
                {"media": "Filme"}, {"media": "/mnt/user"}, {"media": "/srvx/Filme"},
                {"Media!": "/srv/x"}, {"media": 5}, {"media": "/srv/x\ny"}):
        ctx.runner.calls.clear()
        reply = run(ctx, "apps.install", {"id": "jellyfin", "folders": bad})
        assert err_code(reply) == protocol.INVALID_PARAMS, bad
        assert ctx.runner.calls == []


def test_files_browse_and_mkdir(ctx, tmp_path, monkeypatch):
    ctx.pool = tmp_path / "user"
    (ctx.pool / "Filme/Kino").mkdir(parents=True)
    (ctx.pool / "appdata").mkdir()
    (ctx.pool / ".hidden").mkdir()
    (ctx.pool / "file.txt").write_text("x")
    (ctx.pool / "out").symlink_to(tmp_path)
    assert run(ctx, "files.browse")["result"]["mounted"] is False
    monkeypatch.setattr(actions.os.path, "ismount", lambda p: True)
    got = run(ctx, "files.browse")["result"]
    assert got["dirs"] == ["appdata", "Filme"] and got["exists"]
    assert run(ctx, "files.browse", {"path": "Filme"})["result"]["dirs"] == ["Kino"]
    assert run(ctx, "files.browse", {"path": "nope"})["result"]["exists"] is False
    for bad in ("../etc", "/etc", "Filme/../.."):
        assert err_code(run(ctx, "files.browse", {"path": bad})) == protocol.INVALID_PARAMS
    assert err_code(run(ctx, "files.browse", {"path": "out"})) == protocol.INVALID_PARAMS
    assert run(ctx, "files.mkdir", {"path": "Filme/Serien"})["ok"]
    assert (ctx.pool / "Filme/Serien").is_dir()
    assert ["chgrp", "dahlia", str(ctx.pool / "Filme/Serien")] in ctx.runner.calls
    assert err_code(run(ctx, "files.mkdir", {"path": "Filme/Serien"})) == protocol.FAILED
    assert err_code(run(ctx, "files.mkdir", {"path": "a/b"})) == protocol.FAILED
    assert err_code(run(ctx, "files.mkdir", {"path": "appdata/x"})) == protocol.INVALID_PARAMS
    assert err_code(run(ctx, "files.mkdir", {"path": "out/x"})) == protocol.INVALID_PARAMS


def test_files_on_system_ssd(ctx, tmp_path, monkeypatch):
    ctx.pool = tmp_path / "user"
    ctx.system = tmp_path / "srv"
    got = run(ctx, "files.browse", {"root": "/srv"})["result"]
    assert got["mounted"] is False
    assert got["locations"] == [
        {"name": "array", "path": "/mnt/user", "available": False},
        {"name": "system", "path": "/srv", "available": False}]
    ctx.system.mkdir()
    assert run(ctx, "files.mkdir", {"root": "/srv", "path": "Medien"})["ok"]
    assert (ctx.system / "Medien").is_dir()
    got = run(ctx, "files.browse", {"root": "/srv"})["result"]
    assert got["dirs"] == ["Medien"] and got["root"] == "/srv"
    assert got["locations"][1]["available"] is True
    assert err_code(run(ctx, "files.mkdir", {"path": "x"})) == protocol.FAILED
    assert err_code(run(ctx, "files.browse", {"root": "/etc"})) == protocol.INVALID_PARAMS


def test_apps_install_checks_before_starting_job(ctx):
    run(ctx, "apps.install", {"id": "jellyfin"})
    assert ["dahlia-apps", "precheck", "install", "jellyfin"] in ctx.runner.calls
    ctx.runner.calls.clear()
    ctx.runner.answers[("dahlia-apps", "precheck")] = (
        1, "", "dahlia-apps: /mnt/user is not mounted; add a data disk first\n")
    reply = run(ctx, "apps.install", {"id": "jellyfin"})
    assert err_code(reply) == protocol.FAILED
    assert "add a data disk" in reply["error"]["message"]
    assert not any(c[0] == "systemd-run" for c in ctx.runner.calls)


def test_apps_job_reports_error_of_failed_job(ctx):
    ctx.runner.answers[("systemctl", "show")] = (
        0, "ActiveState=failed\nSubState=failed\nResult=exit-code\nExecMainStatus=1\n", "")
    ctx.runner.answers[("journalctl",)] = (
        0, "pulling\ndahlia-apps: old\ndahlia-apps: image pull failed: timeout\n", "")
    job = run(ctx, "apps.job", {"id": "jellyfin"})["result"]
    assert job["error"] == "image pull failed: timeout"


def test_apps_job_success_has_no_error(ctx):
    job = run(ctx, "apps.job", {"id": "jellyfin"})["result"]
    assert "error" not in job
    assert not any(c[0] == "journalctl" for c in ctx.runner.calls)


@pytest.mark.parametrize("app_id", ["../x", "Jelly", "a", "x" * 40, "-x", "a b"])
def test_apps_bad_id(ctx, app_id):
    for act in ("apps.install", "apps.start", "apps.remove", "apps.job"):
        assert err_code(run(ctx, act, {"id": app_id})) == protocol.INVALID_PARAMS
    assert ctx.runner.calls == []


def test_apps_busy(ctx):
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    assert err_code(run(ctx, "apps.install", {"id": "jellyfin"})) == protocol.BUSY
    assert err_code(run(ctx, "apps.remove", {"id": "jellyfin"})) == protocol.BUSY


def test_apps_remove_and_list(ctx):
    ctx.runner.answers[("dahlia-apps", "remove")] = (0, '{"id": "x1", "data_deleted": true}', "")
    ctx.runner.answers[("dahlia-apps", "list")] = (0, "[]", "")
    assert run(ctx, "apps.remove", {"id": "x1", "delete_data": True})["ok"]
    assert ["dahlia-apps", "remove", "x1", "--delete-data"] in ctx.runner.calls
    assert run(ctx, "apps.list")["result"] == {"apps": []}


def test_apps_catalog_refresh(ctx):
    ctx.runner.answers[("dahlia-apps", "catalog")] = (0, "[]", "")
    assert run(ctx, "apps.catalog", {"refresh": True})["ok"]
    assert ctx.runner.calls[-1] == ["dahlia-apps", "catalog", "--refresh"]
