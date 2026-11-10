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
        self.answers = answers or {}

    def run(self, cmd, *, timeout=120, check=True, env=None):
        self.calls.append(cmd)
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
    return actions.Context(runner=runner, proc=proc, release_file=release, hosts_file=hosts)


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


def test_updates_apply(ctx):
    assert run(ctx, "updates.apply")["ok"]
    cmd = ctx.runner.calls[-1]
    assert cmd == ["systemctl", "start", "--no-block", "dahlia-update.service"]


def test_updates_apply_busy(ctx):
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    assert err_code(run(ctx, "updates.apply")) == protocol.BUSY
    assert not any(c[:2] == ["systemctl", "start"] for c in ctx.runner.calls)


# --- domains ------------------------------------------------------------------


def test_domains_get(ctx):
    ctx.runner.answers[("dahlia-domains", "show")] = (0, '{"domain": "dahlia.home.arpa"}', "")
    assert run(ctx, "domains.get")["result"] == {"domain": "dahlia.home.arpa"}


def test_domains_set(ctx):
    ctx.runner.answers[("dahlia-domains", "set")] = (0, '{"ca_renewed": false}', "")
    params = {"domain": "lab.home.arpa", "mdns_aliases": False}
    assert run(ctx, "domains.set", params)["ok"]
    assert ctx.runner.calls[-1] == [
        "dahlia-domains", "set", "--domain", "lab.home.arpa", "--mdns", "off",
    ]


@pytest.mark.parametrize("domain", ["", "home", "Dahlia.home.arpa", "a..b", "-a.b", "a b.c",
                                    "x.home.arpa.", "a.b.c.d.e.f.g"])
def test_domains_set_rejects(ctx, domain):
    reply = run(ctx, "domains.set", {"domain": domain, "mdns_aliases": True})
    assert err_code(reply) == protocol.INVALID_PARAMS
    assert not ctx.runner.calls


def test_domains_set_error_passes_through(ctx):
    ctx.runner.answers[("dahlia-domains", "set")] = (1, "", "dahlia-domains: use home.arpa\n")
    reply = run(ctx, "domains.set", {"domain": "x.local", "mdns_aliases": True})
    assert err_code(reply) == protocol.FAILED
    assert "use home.arpa" in reply["error"]["message"]


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
        "domains.get",
        "domains.set",
        "job.start",
        "job.status",
        "storage.add",
        "storage.list",
        "storage.remove",
        "storage.set_option",
        "system.info",
        "system.poweroff",
        "system.reboot",
        "system.set_hostname",
        "system.stats",
        "updates.apply",
        "updates.check",
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
