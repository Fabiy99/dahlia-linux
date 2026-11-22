"""storage.replace: the disk replacement wizard."""

import json

import pytest
from dahlia_agent import actions
from test_agent import INACTIVE, RUNNING, FakeRunner, err_code, run

MANAGED = [
    {"uuid": "aaaa-1111", "role": "data", "index": 2, "fstype": "xfs", "size": 4_000_000_000_000,
     "luks": "", "mountpoint": "/mnt/disk2", "mounted": False, "serial": "OLD", "model": "WD",
     "name": ""},
    {"uuid": "bbbb-2222", "role": "data", "index": 1, "fstype": "xfs", "size": 8_000_000_000_000,
     "luks": "", "mountpoint": "/mnt/disk1", "mounted": True, "serial": "D1", "model": "WD",
     "name": ""},
    {"uuid": "cccc-3333", "role": "parity", "index": 1, "fstype": "xfs",
     "size": 8_000_000_000_000, "luks": "", "mountpoint": "/mnt/parity1", "mounted": True,
     "serial": "P1", "model": "WD", "name": ""},
    {"uuid": "dddd-4444", "role": "cache", "index": 0, "fstype": "btrfs", "size": 1_000_000_000_000,
     "luks": "", "mountpoint": "/mnt/cache", "mounted": True, "serial": "C", "model": "S",
     "name": ""},
]


def disks(size):
    return [{"path": "/dev/sde", "disk": "/dev/sde", "status": "empty", "size": size},
            {"path": "/dev/sdf1", "disk": "/dev/sdf", "status": "candidate", "size": size}]


@pytest.fixture
def ctx(tmp_path):
    runner = FakeRunner({
        ("disk", "managed", "--json"): (0, json.dumps(MANAGED), ""),
        ("disk", "list", "--json"): (0, json.dumps(disks(8_000_000_000_000)), ""),
        ("disk", "format"): (0, json.dumps({"device": "/dev/sde", "uuid": "eeee-5555"}), ""),
        ("systemctl", "show"): (0, INACTIVE, ""),
        ("pgrep",): (1, "", ""),
    })
    return actions.Context(runner=runner, run_dir=tmp_path, var=tmp_path)


def test_managed(ctx):
    assert run(ctx, "storage.managed")["result"]["disks"][0]["mountpoint"] == "/mnt/disk2"


def test_replace_data_disk(ctx):
    res = run(ctx, "storage.replace", {"uuid": "aaaa-1111", "device": "/dev/sde", "confirm": "sde"})
    assert res["ok"], res
    assert res["result"] == {"uuid": "eeee-5555", "role": "data", "index": 2,
                             "mountpoint": "/mnt/disk2", "rebuild": True}
    calls = ctx.runner.calls
    order = [calls.index(c) for c in (
        ["disk", "remove", "aaaa-1111"],
        ["disk", "format", "/dev/sde", "--fs", "xfs", "--json"],
        ["disk", "add", "eeee-5555", "--role", "data", "--index", "2"],
    )]
    assert order == sorted(order)
    unit = [c for c in calls if c[0] == "systemd-run"][-1]
    assert unit[-4:] == ["/usr/sbin/dahlia-snapraid", "fix", "--disk", "2"]


def test_replace_parity_disk(ctx):
    res = run(ctx, "storage.replace", {"uuid": "cccc-3333", "device": "/dev/sde", "confirm": "sde"})
    assert res["ok"], res
    unit = [c for c in ctx.runner.calls if c[0] == "systemd-run"][-1]
    assert unit[-3:] == ["/usr/sbin/dahlia-snapraid", "sync", "--force"]


def test_replace_refuses(ctx):
    base = {"uuid": "aaaa-1111", "device": "/dev/sde", "confirm": "sde"}
    assert err_code(run(ctx, "storage.replace", {**base, "confirm": "sdx"})) == "invalid_params"
    assert err_code(run(ctx, "storage.replace", {**base, "uuid": "dddd-4444"})) == "invalid_params"
    # Not blank.
    assert err_code(run(ctx, "storage.replace", {**base, "device": "/dev/sdf",
                                                 "confirm": "sdf"})) == "failed"
    # Smaller than the old disk; for parity smaller than the largest data disk.
    ctx.runner.answers[("disk", "list", "--json")] = (0, json.dumps(disks(3_000_000_000_000)), "")
    assert err_code(run(ctx, "storage.replace", base)) == "failed"
    ctx.runner.answers[("disk", "list", "--json")] = (0, json.dumps(disks(5_000_000_000_000)), "")
    assert err_code(run(ctx, "storage.replace", {**base, "uuid": "cccc-3333"})) == "failed"
    # Busy parity.
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    assert err_code(run(ctx, "storage.replace", base)) == "busy"
    assert ["disk", "remove", "aaaa-1111"] not in ctx.runner.calls


def test_replace_without_parity(ctx):
    ctx.runner.answers[("disk", "managed", "--json")] = (0, json.dumps(MANAGED[:2]), "")
    reply = run(ctx, "storage.replace",
                {"uuid": "aaaa-1111", "device": "/dev/sde", "confirm": "sde"})
    assert err_code(reply) == "failed" and "parity" in reply["error"]["message"]


def test_failed_format_adds_the_old_disk_back(ctx):
    ctx.runner.answers[("disk", "format")] = (1, "", "mkfs failed")
    reply = run(ctx, "storage.replace",
                {"uuid": "aaaa-1111", "device": "/dev/sde", "confirm": "sde"})
    assert err_code(reply) == "failed"
    assert ["disk", "add", "aaaa-1111", "--role", "data", "--index", "2"] in ctx.runner.calls
