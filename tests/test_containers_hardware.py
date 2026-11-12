"""docker.* and system.hardware / system.log actions of agent."""

import json

import pytest
from dahlia_agent import actions, containers, hardware, protocol
from test_agent import FakeRunner, err_code, run


@pytest.fixture
def ctx(tmp_path):
    return actions.Context(runner=FakeRunner(), proc=tmp_path / "proc", sys=tmp_path / "sys")


# --- docker -------------------------------------------------------------------

PS = "\n".join(json.dumps(c) for c in [
    {"ID": "a1b2c3d4e5f6a7b8", "Names": "portainer-like", "Image": "nginx:1.27",
     "State": "running", "Status": "Up 2 hours", "Ports": "0.0.0.0:8080->80/tcp",
     "CreatedAt": "2026-11-10 10:00:00 +0100 CET", "Labels": ""},
    {"ID": "ffeeddccbbaa0011", "Names": "dahlia-immich-app-1", "Image": "ghcr.io/immich:v2",
     "State": "exited", "Status": "Exited (0) 3 minutes ago", "Ports": "",
     "CreatedAt": "2026-11-10 11:00:00 +0100 CET",
     "Labels": "com.docker.compose.project=dahlia-immich,com.docker.compose.service=app"},
]) + "\nnot json\n"

STATS = json.dumps({"Name": "portainer-like", "CPUPerc": "1.25%",
                    "MemUsage": "45.3MiB / 15.5GiB", "NetIO": "1.2MB / 340kB",
                    "BlockIO": "0B / 4.1MB"})

DF = "\n".join(json.dumps(r) for r in [
    {"Type": "Images", "TotalCount": "5", "Active": "3", "Size": "2.5GB",
     "Reclaimable": "1.1GB (44%)"},
    {"Type": "Build Cache", "TotalCount": "0", "Active": "0", "Size": "0B", "Reclaimable": "0B"},
])


def test_docker_list(ctx):
    ctx.runner.answers[("docker", "ps")] = (0, PS, "")
    out = run(ctx, "docker.list")["result"]["containers"]
    assert [c["name"] for c in out] == ["dahlia-immich-app-1", "portainer-like"]
    assert out[0]["app"] == "immich" and out[0]["service"] == "app"
    assert out[0]["id"] == "ffeeddccbbaa"
    assert out[1]["app"] is None and out[1]["ports"] == "0.0.0.0:8080->80/tcp"


def test_docker_list_daemon_down(ctx):
    ctx.runner.answers[("docker", "ps")] = (1, "", "Cannot connect to the Docker daemon\n")
    reply = run(ctx, "docker.list")
    assert err_code(reply) == protocol.FAILED
    assert "Cannot connect" in reply["error"]["message"]


def test_docker_stats(ctx):
    ctx.runner.answers[("docker", "stats")] = (0, STATS + "\n", "")
    s = run(ctx, "docker.stats")["result"]["stats"]["portainer-like"]
    assert s["cpu_percent"] == 1.25
    assert s["memory"] == round(45.3 * 1024**2)
    assert s["memory_limit"] == round(15.5 * 1024**3)
    assert s["net_rx"] == 1_200_000 and s["net_tx"] == 340_000
    assert s["block_read"] == 0 and s["block_write"] == 4_100_000


def test_docker_disk(ctx):
    ctx.runner.answers[("docker", "system", "df")] = (0, DF, "")
    usage = run(ctx, "docker.disk")["result"]["usage"]
    assert usage[0] == {"type": "Images", "total": 5, "active": 3, "size": 2_500_000_000,
                        "reclaimable": 1_100_000_000}


@pytest.mark.parametrize("op", ["start", "stop", "restart"])
def test_docker_container_ops(ctx, op):
    assert run(ctx, "docker.container", {"name": "dahlia-immich-app-1", "op": op})["ok"]
    assert ctx.runner.calls[-1] == ["docker", op, "--", "dahlia-immich-app-1"]


@pytest.mark.parametrize("params", [
    {"name": "-rf", "op": "stop"},
    {"name": "x; rm -rf /", "op": "stop"},
    {"name": "web", "op": "rm"},
    {"name": "web", "op": "exec"},
])
def test_docker_container_rejects(ctx, params):
    assert err_code(run(ctx, "docker.container", params)) == protocol.INVALID_PARAMS
    assert ctx.runner.calls == []


def test_docker_logs_merges_streams(ctx):
    out = "2026-11-10T10:00:01.000000000Z started\n2026-11-10T10:00:03.000000000Z ready\n"
    err = "2026-11-10T10:00:02.000000000Z warning: x\n"
    ctx.runner.answers[("docker", "logs")] = (0, out, err)
    lines = run(ctx, "docker.logs", {"name": "web", "lines": 50})["result"]["lines"]
    assert [ln.split(" ", 1)[1] for ln in lines] == ["started", "warning: x", "ready"]
    assert ctx.runner.calls[-1] == ["docker", "logs", "--tail", "50", "--timestamps", "--", "web"]


def test_docker_logs_limits(ctx):
    assert err_code(run(ctx, "docker.logs", {"name": "web", "lines": 100000})) == \
        protocol.INVALID_PARAMS


def test_docker_prune(ctx):
    ctx.runner.answers[("docker", "image", "prune")] = (
        0, "Deleted Images:\nuntagged: x\n\nTotal reclaimed space: 1.5GB\n", "")
    assert run(ctx, "docker.prune")["result"] == {"reclaimed": 1_500_000_000}
    assert ["docker", "builder", "prune", "-f"] in ctx.runner.calls


def test_parse_size():
    assert containers.parse_size("0B") == 0
    assert containers.parse_size("1.5kB") == 1500
    assert containers.parse_size("2GiB") == 2 * 1024**3
    assert containers.parse_size("--") is None


# --- hardware -----------------------------------------------------------------

LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "type": "disk", "tran": "sata", "rota": True, "size": 8000000000000},
    {"name": "nvme0n1", "type": "disk", "tran": "nvme", "rota": False, "size": 250000000000},
    {"name": "sdb", "type": "disk", "tran": "usb", "rota": True, "size": 4000000000000},
    {"name": "sdc", "type": "disk", "tran": None, "rota": False, "size": 1000},
    {"name": "sr0", "type": "rom", "tran": "sata", "rota": True, "size": 1000},
    {"name": "zram0", "type": "disk", "tran": None, "rota": False, "size": 1000},
]})

SMART_ATA = json.dumps({
    "model_name": "WDC WD80EFZZ", "serial_number": "VK0ABC12",
    "smart_status": {"passed": True}, "temperature": {"current": 34},
    "power_on_time": {"hours": 12034},
    "ata_smart_attributes": {"table": [
        {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 8}},
        {"id": 9, "name": "Power_On_Hours", "raw": {"value": 12034}},
        {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": 0}},
    ]},
})

SMART_NVME = json.dumps({
    "model_name": "Samsung SSD 980", "serial_number": "S64D",
    "smart_status": {"passed": True}, "temperature": {"current": 41},
    "nvme_smart_health_information_log": {"percentage_used": 3, "media_errors": 0,
                                          "critical_warning": 0},
})


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_system_hardware(ctx):
    ctx.runner.answers[("lsblk",)] = (0, LSBLK, "")
    smart = ("smartctl", "-j", "-a", "-n", "standby,99")
    ctx.runner.answers[(*smart, "/dev/sda")] = (4, SMART_ATA, "")
    ctx.runner.answers[("smartctl", "-j", "-a", "-n", "standby,99", "/dev/nvme0n1")] = (
        0, SMART_NVME, "")
    ctx.runner.answers[("smartctl", "-j", "-a", "-n", "standby,99", "/dev/sdb")] = (99, "", "")
    ctx.runner.answers[("smartctl", "-j", "-a", "-n", "standby,99", "/dev/sdc")] = (
        2, json.dumps({"smartctl": {"exit_status": 2}}), "")
    write(ctx.sys / "class/hwmon/hwmon0/name", "acpitz\n")
    write(ctx.sys / "class/hwmon/hwmon0/temp1_input", "27800\n")
    write(ctx.sys / "class/hwmon/hwmon1/name", "k10temp\n")
    write(ctx.sys / "class/hwmon/hwmon1/temp1_input", "48250\n")
    write(ctx.sys / "class/dmi/id/board_vendor", "ASRock\n")
    write(ctx.sys / "class/dmi/id/board_name", "B550M Pro4\n")
    write(ctx.sys / "class/dmi/id/sys_vendor", "\n")

    write(ctx.sys / "class/drm/card0/device/vendor", "0x8086\n")
    write(ctx.sys / "class/drm/card0/device/drm/renderD128/dev", "226:128\n")
    (ctx.sys / "drivers/i915").mkdir(parents=True)
    (ctx.sys / "class/drm/card0/device/driver").symlink_to(ctx.sys / "drivers/i915")
    write(ctx.sys / "class/drm/card0-HDMI-A-1/status", "connected\n")
    write(ctx.sys / "class/drm/card1/device/vendor", "0x1234\n")

    hw = run(ctx, "system.hardware")["result"]
    assert hw["gpus"] == [
        {"card": "card0", "vendor": "Intel", "driver": "i915", "render": "renderD128"},
        {"card": "card1", "vendor": "0x1234", "driver": None, "render": None},
    ]
    assert hw["cpu_temperature"] == 48.2
    assert hw["board"]["board_vendor"] == "ASRock"
    assert hw["board"]["sys_vendor"] is None and hw["board"]["bios_version"] is None
    disks = {d["path"]: d for d in hw["disks"]}
    assert list(disks) == ["/dev/sda", "/dev/nvme0n1", "/dev/sdb", "/dev/sdc"]
    assert disks["/dev/sda"]["health"] == "warning"
    assert disks["/dev/sda"]["warnings"] == {"reallocated": 8}
    assert disks["/dev/sda"]["temperature"] == 34 and disks["/dev/sda"]["rotational"] is True
    assert disks["/dev/nvme0n1"]["health"] == "passed"
    assert disks["/dev/nvme0n1"]["wear_percent"] == 3
    assert disks["/dev/sdb"]["health"] == "standby"
    assert disks["/dev/sdc"]["health"] == "unavailable"


def test_smart_failed():
    s = hardware.smart_summary({"smart_status": {"passed": False}})
    assert s["health"] == "failed"
    assert hardware.smart_summary({})["health"] == "unknown"


def test_cpu_temperature_missing(tmp_path):
    assert hardware.cpu_temperature(tmp_path) is None


def test_system_log(ctx):
    ctx.runner.answers[("journalctl",)] = (0, "2026-11-10T19:00:00+0100 nas sshd[1]: hi\n", "")
    out = run(ctx, "system.log", {"lines": 100, "priority": "warning", "unit": "ssh.service"})
    assert out["result"]["lines"] == ["2026-11-10T19:00:00+0100 nas sshd[1]: hi"]
    assert ctx.runner.calls[-1] == ["journalctl", "--no-pager", "-q", "-o", "short-iso", "-n",
                                    "100", "-p", "warning", "-u", "ssh.service"]
    run(ctx, "system.log")
    assert ctx.runner.calls[-1][-2:] == ["-p", "info"]


@pytest.mark.parametrize("params", [{"unit": "-f"}, {"unit": "a b"}, {"priority": "all"},
                                    {"lines": 5}])
def test_system_log_rejects(ctx, params):
    assert err_code(run(ctx, "system.log", params)) == protocol.INVALID_PARAMS
