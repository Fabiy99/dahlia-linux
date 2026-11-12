"""SMART self-tests and the disk test (burn-in) of agent."""

import json

import pytest
from dahlia_agent import actions, disktest, hardware, notify, protocol
from test_agent import FakeRunner, err_code, run

LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "type": "disk", "tran": "sata", "rota": True, "size": 8000000000000},
    {"name": "sdb", "type": "disk", "tran": "sata", "rota": True, "size": 4000000000000},
]})


def disk_list(status_b="empty", kind_b="disk"):
    return json.dumps([
        {"path": "/dev/sda1", "disk": "/dev/sda", "kind": "part", "status": "registered",
         "reason": "already managed by Dahlia"},
        {"path": "/dev/sdb" if kind_b == "disk" else "/dev/sdb1", "disk": "/dev/sdb",
         "kind": kind_b, "status": status_b, "reason": "no filesystem"},
    ])


@pytest.fixture
def ctx(tmp_path):
    c = actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                        var=tmp_path / "var", sys=tmp_path / "sys", proc=tmp_path / "proc")
    c.runner.answers[("lsblk",)] = (0, LSBLK, "")
    c.runner.answers[("disk", "list")] = (0, disk_list(), "")
    c.runner.answers[("systemctl", "is-active")] = (3, "inactive\n", "")
    return c


# --- parsing --------------------------------------------------------------------

ATA = {
    "smart_status": {"passed": True},
    "ata_smart_data": {"self_test": {"status": {"value": 0, "passed": True}}},
    "ata_smart_self_test_log": {"standard": {"table": [
        {"type": {"string": "Extended offline"}, "status": {"value": 0, "passed": True},
         "lifetime_hours": 12000},
        {"type": {"string": "Short offline"}, "status": {"value": 0x73}, "lifetime_hours": 11000},
    ]}},
}


def test_self_test_ata():
    st = hardware.self_test(ATA)
    assert st == {"running": False, "remaining": None,
                  "last": {"kind": "long", "result": "passed", "hours": 12000}}
    running = {**ATA, "ata_smart_data": {"self_test": {"status": {"value": 0xF6,
                                                                  "remaining_percent": 60}}}}
    assert hardware.self_test(running)["running"] is True
    assert hardware.self_test(running)["remaining"] == 60
    failed = {"ata_smart_self_test_log": {"standard": {"table": [
        {"type": {"string": "Short offline"}, "status": {"value": 0x73}, "lifetime_hours": 5}]}}}
    assert hardware.self_test(failed)["last"]["result"] == "failed"
    aborted = {"ata_smart_self_test_log": {"standard": {"table": [
        {"type": {"string": "Short offline"}, "status": {"value": 0x21}, "lifetime_hours": 5}]}}}
    assert hardware.self_test(aborted)["last"]["result"] == "aborted"
    assert hardware.self_test({}) == {"running": False, "remaining": None, "last": None}


def test_self_test_nvme():
    data = {"nvme_self_test_log": {
        "current_self_test_operation": {"value": 1, "string": "Short"},
        "current_self_test_completion_percent": 30,
        "table": [{"self_test_code": {"string": "Extended"},
                   "self_test_result": {"value": 7}, "power_on_hours": 900}]}}
    st = hardware.self_test(data)
    assert st == {"running": True, "remaining": 70,
                  "last": {"kind": "long", "result": "failed", "hours": 900}}
    assert hardware.smart_summary(data)["self_test"]["running"] is True


def test_failed_self_test_notifies(ctx):
    ctx.runner.answers[("smartctl", "-j", "-a")] = (0, json.dumps({
        "model_name": "WD", **ATA,
        "ata_smart_self_test_log": {"standard": {"table": [
            {"type": {"string": "Short offline"}, "status": {"value": 0x73},
             "lifetime_hours": 11000}]}}}), "")
    (ctx.proc / "self").mkdir(parents=True)
    (ctx.proc / "self/mounts").write_text("")
    problems = notify.current_problems(ctx)
    event, level, title, _ = problems["selftest:/dev/sda"]
    assert (event, level) == ("smart", "critical") and "self-test failed" in title


# --- self-tests -----------------------------------------------------------------


def test_smart_test_start_and_abort(ctx):
    assert run(ctx, "disks.smart_test", {"device": "/dev/sda", "kind": "short"})["ok"]
    assert ["smartctl", "-t", "short", "/dev/sda"] in ctx.runner.calls
    assert run(ctx, "disks.smart_test", {"device": "/dev/sdb", "kind": "abort"})["ok"]
    assert ["smartctl", "-X", "/dev/sdb"] in ctx.runner.calls


def test_smart_test_rejects(ctx):
    assert err_code(run(ctx, "disks.smart_test", {"device": "/dev/sdz", "kind": "short"})) \
        == protocol.FAILED
    assert err_code(run(ctx, "disks.smart_test", {"device": "/etc/passwd", "kind": "short"})) \
        == protocol.INVALID_PARAMS
    assert err_code(run(ctx, "disks.smart_test", {"device": "/dev/sda", "kind": "conveyance"})) \
        == protocol.INVALID_PARAMS
    ctx.runner.answers[("smartctl", "-t")] = (4, "SMART command failed\n", "")
    reply = run(ctx, "disks.smart_test", {"device": "/dev/sda", "kind": "long"})
    assert err_code(reply) == protocol.FAILED and "SMART command failed" in \
        reply["error"]["message"]


# --- burn-in --------------------------------------------------------------------


def test_burnin_start(ctx):
    assert run(ctx, "disks.burnin_start", {"device": "/dev/sdb", "confirm": "sdb"})["ok"]
    assert ["systemctl", "start", "--no-block", "burnin@sdb.service"] in ctx.runner.calls


@pytest.mark.parametrize("device,confirm,code", [
    ("/dev/sdb", "sda", protocol.INVALID_PARAMS),
    ("/dev/sdb", "", protocol.INVALID_PARAMS),
    ("/dev/sda", "sda", protocol.FAILED),  # in the array
    ("/dev/sdc", "sdc", protocol.FAILED),  # unknown
    ("/dev/loop1", "loop1", protocol.INVALID_PARAMS),
    ("/dev/sdb;rm", "sdb;rm", protocol.INVALID_PARAMS),
])
def test_burnin_rejects(ctx, device, confirm, code):
    assert err_code(run(ctx, "disks.burnin_start", {"device": device, "confirm": confirm})) == code
    assert not any(c[:2] == ["systemctl", "start"] for c in ctx.runner.calls)


@pytest.mark.parametrize("status,kind", [("candidate", "part"), ("empty", "part"),
                                         ("mounted", "disk")])
def test_burnin_only_on_blank_disks(ctx, status, kind):
    ctx.runner.answers[("disk", "list")] = (0, disk_list(status, kind), "")
    reply = run(ctx, "disks.burnin_start", {"device": "/dev/sdb", "confirm": "sdb"})
    assert err_code(reply) == protocol.FAILED and "not empty" in reply["error"]["message"]


def test_burnin_not_twice_and_blocks_format(ctx):
    ctx.runner.answers[("systemctl", "is-active")] = (0, "active\n", "")
    reply = run(ctx, "disks.burnin_start", {"device": "/dev/sdb", "confirm": "sdb"})
    assert err_code(reply) == protocol.FAILED and "already running" in reply["error"]["message"]
    reply = run(ctx, "storage.format", {"device": "/dev/sdb", "filesystem": "xfs",
                                        "confirm": "sdb"})
    assert err_code(reply) == protocol.FAILED and "disk test" in reply["error"]["message"]
    assert not any(c[0] == "disk" and c[1] == "format" for c in ctx.runner.calls)


LOG = ("Checking for bad blocks in read-write mode\nFrom block 0 to 976754645\n"
       "Testing with pattern 0xaa: done                                                 \n"
       "Reading and comparing: done                                                 \n"
       "Testing with pattern 0x55:  12.50% done, 1:02:03 elapsed. (0/0/0 errors)"
       "\b\b\b\b\b\b\b\b\b\b 13.75% done, 1:05:00 elapsed. (0/0/0 errors)")


def test_progress():
    assert disktest.progress(LOG) == {"pass": 3, "percent": 13.75}
    assert disktest.progress("") == {"pass": 0, "percent": 0.0}
    reading = LOG + "\nTesting with pattern 0x55: done\nReading and comparing:  40.00% done"
    assert disktest.progress(reading) == {"pass": 4, "percent": 40.0}


def smart_file(reallocated):
    return json.dumps({"smart_status": {"passed": True}, "ata_smart_attributes": {"table": [
        {"id": 5, "raw": {"value": reallocated}}]}})


def test_burnin_status(ctx):
    d = ctx.var / "burnin"
    d.mkdir(parents=True)
    (d / "sdb.log").write_text(LOG)
    (d / "sdb.smart-before.json").write_text(smart_file(0))
    ctx.runner.answers[("systemctl", "show")] = (0, "ActiveState=active\nResult=success\n", "")
    t = run(ctx, "disks.burnin_status")["result"]["tests"]
    assert t == [{"device": "/dev/sdb", "result": "running", "pass": 3, "percent": 13.75,
                  "bad_blocks": 0, "smart_new": {}, "started": ""}]

    ctx.runner.answers[("systemctl", "show")] = (0, "ActiveState=inactive\nResult=success\n", "")
    (d / "sdb.done").write_text("")
    (d / "sdb.smart-after.json").write_text(smart_file(0))
    assert run(ctx, "disks.burnin_status")["result"]["tests"][0]["result"] == "passed"
    (d / "sdb.smart-after.json").write_text(smart_file(16))
    t = run(ctx, "disks.burnin_status")["result"]["tests"][0]
    assert t["result"] == "failed" and t["smart_new"] == {"reallocated": 16}
    (d / "sdb.smart-after.json").write_text(smart_file(0))
    (d / "sdb.bad").write_text("123\n456\n")
    t = run(ctx, "disks.burnin_status")["result"]["tests"][0]
    assert t["result"] == "failed" and t["bad_blocks"] == 2
    (d / "sdb.done").unlink()
    assert run(ctx, "disks.burnin_status")["result"]["tests"][0]["result"] == "stopped"

    assert run(ctx, "disks.burnin_clear", {"device": "/dev/sdb"})["ok"]
    assert run(ctx, "disks.burnin_status")["result"]["tests"] == []
    assert list(d.iterdir()) == []


def test_burnin_stop(ctx):
    assert run(ctx, "disks.burnin_stop", {"device": "/dev/sdb"})["ok"]
    assert ctx.runner.calls[-1] == ["systemctl", "stop", "--no-block", "burnin@sdb.service"]


def test_smart_schedules(ctx):
    ctx.runner.answers[("systemctl", "show")] = (0, "UnitFileState=enabled\n", "")
    s = run(ctx, "settings.schedules")["result"]["schedules"]
    assert s["smart-short"]["frequency"] == "weekly" and s["smart-short"]["weekday"] == "sat"
    assert s["smart-long"]["frequency"] == "off"
    assert run(ctx, "settings.set_schedule", {"job": "smart-long", "frequency": "monthly",
                                              "monthday": 15, "time": "05:00"})["ok"]
    dropin = ctx.etc / "systemd/system/smart-long.timer.d/schedule.conf"
    assert "OnCalendar=*-*-15 05:00:00" in dropin.read_text()


def test_smart_jobs_startable(ctx):
    assert run(ctx, "job.start", {"job": "smart-short"})["ok"]
    assert any("smart-test@short.service" in c for c in ctx.runner.calls)
