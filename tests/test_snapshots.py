"""snapshots.*: snapper snapshots of / and rollback."""

import json

import pytest
from dahlia_agent import actions, protocol, snapshots
from test_agent import INACTIVE, RUNNING, FakeRunner, err_code, run

LIST = json.dumps({"root": [
    {"subvolume": "/", "number": 0, "default": False, "active": False, "type": "single",
     "pre-number": None, "date": "", "user": "root", "cleanup": "",
     "description": "current", "userdata": None},
    {"subvolume": "/", "number": 4, "type": "pre", "pre-number": None,
     "date": "2026-11-10 06:20:11", "user": "root", "cleanup": "number",
     "description": "apt", "userdata": {"important": "yes"}},
    {"subvolume": "/", "number": 5, "type": "post", "pre-number": 4,
     "date": "2026-11-10 06:20:40", "user": "root", "cleanup": "number",
     "description": "apt", "userdata": None},
]})
SUBVOLS = ("ID 256 gen 900 top level 5 path @rootfs\n"
           "ID 257 gen 900 top level 256 path .snapshots\n"
           "ID 258 gen 10 top level 5 path @srv\n"
           "ID 300 gen 800 top level 5 path <FS_TREE>/@rootfs.pre-rollback-20261111-211354\n"
           "ID 301 gen 800 top level 5 path @rootfs.pre-rollback-bad\n")
OLD = "@rootfs.pre-rollback-20261111-211354"


@pytest.fixture
def ctx(tmp_path):
    runner = FakeRunner({
        ("snapper", "--no-dbus", "-c", "root", "--jsonout", "list"): (0, LIST, ""),
        ("btrfs", "subvolume", "list"): (0, SUBVOLS, ""),
        ("systemctl", "show", snapshots.ROLLBACK_UNIT): (0, INACTIVE, ""),
    })
    c = actions.Context(runner=runner, etc=tmp_path / "etc", var=tmp_path / "var")
    cfg = c.etc / "snapper" / "configs" / "root"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('SUBVOLUME="/"\n')
    c.var.mkdir()
    return c


def test_list(ctx):
    (ctx.var / "rollback.json").write_text('{"snapshot": 3, "old_root": "x", "time": "t"}\n')
    res = run(ctx, "snapshots.list")["result"]
    assert res["available"] is True
    assert [s["number"] for s in res["snapshots"]] == [4, 5]
    assert res["snapshots"][1]["pre_number"] == 4
    assert res["snapshots"][0]["userdata"] == {"important": "yes"}
    assert res["old_roots"] == [OLD]
    assert res["rollback"]["snapshot"] == 3
    assert res["job"]["active"] == "inactive"


def test_list_without_snapper(ctx):
    (ctx.etc / "snapper" / "configs" / "root").unlink()
    res = run(ctx, "snapshots.list")["result"]
    assert res == {"available": False, "snapshots": [], "old_roots": [], "rollback": None,
                   "job": None}
    assert ctx.runner.calls == []


def test_create(ctx):
    ctx.runner.answers[("snapper", "--no-dbus", "-c", "root", "create")] = (0, "6\n", "")
    for bad in ("", "x" * 81, "a;b", "a\nb", "`id`"):
        reply = run(ctx, "snapshots.create", {"description": bad})
        assert err_code(reply) == protocol.INVALID_PARAMS, bad
    res = run(ctx, "snapshots.create", {"description": "before trying something"})["result"]
    assert res == {"number": 6}
    assert ctx.runner.calls[-1] == [
        "snapper", "--no-dbus", "-c", "root", "create", "--cleanup-algorithm", "number",
        "--description", "before trying something", "--print-number"]


def test_delete(ctx):
    assert err_code(run(ctx, "snapshots.delete", {"number": 0})) == protocol.INVALID_PARAMS
    assert err_code(run(ctx, "snapshots.delete", {"number": 9})) == protocol.FAILED
    assert run(ctx, "snapshots.delete", {"number": 5})["ok"]
    assert ctx.runner.calls[-1] == ["snapper", "--no-dbus", "-c", "root", "delete", "5"]


def test_rollback_starts_unit(ctx):
    assert err_code(run(ctx, "snapshots.rollback", {"number": 9})) == protocol.FAILED
    assert run(ctx, "snapshots.rollback", {"number": 4})["result"] == {
        "started": True, "number": 4}
    cmd = ctx.runner.calls[-1]
    assert cmd[:4] == ["systemd-run", "--unit", "dahlia-rollback.service", "--no-block"]
    assert cmd[-3:] == ["/usr/lib/dahlia/rollback.sh", "4", "--reboot"]
    assert "OnFailure=dahlia-notify-failure@dahlia-rollback.service.service" in cmd


def test_rollback_busy(ctx):
    ctx.runner.answers[("systemctl", "show", snapshots.ROLLBACK_UNIT)] = (0, RUNNING, "")
    assert err_code(run(ctx, "snapshots.rollback", {"number": 4})) == protocol.BUSY


def test_delete_old_root(ctx):
    for bad in ("@rootfs", "../x", "@rootfs.pre-rollback-bad"):
        reply = run(ctx, "snapshots.delete_old_root", {"name": bad})
        assert err_code(reply) == protocol.INVALID_PARAMS, bad
    missing = "@rootfs.pre-rollback-20260101-000000"
    assert err_code(run(ctx, "snapshots.delete_old_root", {"name": missing})) == protocol.FAILED
    assert run(ctx, "snapshots.delete_old_root", {"name": OLD})["ok"]
    assert ctx.runner.calls[-1] == ["/usr/lib/dahlia/rollback.sh", "--delete-old", OLD]
