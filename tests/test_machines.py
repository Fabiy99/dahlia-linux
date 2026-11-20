"""vm.*: VMs and containers with Incus."""

import json

import pytest
from dahlia_agent import actions, machines, protocol
from test_agent import INACTIVE, RUNNING, FakeRunner, err_code, run

FAILED, BUSY, INVALID = protocol.FAILED, protocol.BUSY, protocol.INVALID_PARAMS

ROUTES = json.dumps([{"dst": "default", "gateway": "192.168.1.1", "dev": "enp1s0"}])


def listing(*items):
    return json.dumps(list(items))


WEB = {
    "name": "web", "type": "container", "status": "Running", "created_at": "2026-11-18T10:00:00Z",
    "config": {"image.description": "Debian trixie amd64"},
    "expanded_config": {"limits.cpu": "2", "limits.memory": "1024MiB", "boot.autostart": "true",
                        "image.description": "Debian trixie amd64"},
    "devices": {},
    "expanded_devices": {"eth0": {"type": "nic", "network": "incusbr0"}},
    "state": {"memory": {"usage": 52428800},
              "network": {"lo": {"addresses": [{"family": "inet", "address": "127.0.0.1",
                                                "scope": "local"}]},
                          "eth0": {"addresses": [
                              {"family": "inet", "address": "10.12.0.5", "scope": "global"},
                              {"family": "inet6", "address": "fe80::1", "scope": "link"}]}}},
}
WIN = {
    "name": "win", "type": "virtual-machine", "status": "Stopped",
    "config": {"user.dahlia.vnc": "50", "raw.qemu": "-vnc 127.0.0.1:50"},
    "expanded_config": {"limits.cpu": "4", "limits.memory": "8GiB"},
    "devices": {"install": {"type": "disk", "pool": "default", "source": "win-install"}},
    "expanded_devices": {"eth0": {"type": "nic", "nictype": "macvlan", "parent": "enp1s0"}},
    "state": None,
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    cpus = "".join(f"processor\t: {i}\nmodel name\t: x\n\n" for i in range(8))
    (proc / "cpuinfo").write_text(cpus)
    (proc / "meminfo").write_text("MemTotal:  16777216 kB\nMemFree: 100 kB\n")
    inc = tmp_path / "incus"
    monkeypatch.setattr(machines, "INCUS", inc)
    monkeypatch.setattr(machines, "CACHE", tmp_path / "cache")
    runner = FakeRunner({
        ("systemctl", "show"): (0, INACTIVE, ""),
        ("ip", "-j", "route"): (0, ROUTES, ""),
        (str(inc), "list"): (0, listing(WEB, WIN), ""),
    })
    system = tmp_path / "srv"
    (system / "isos").mkdir(parents=True)
    (system / "isos/win11.iso").write_bytes(b"iso")
    return actions.Context(runner=runner, proc=proc, run_dir=tmp_path / "run", system=system,
                           pool=tmp_path / "user")


def incus_calls(ctx):
    return [c[1:] for c in ctx.runner.calls if c[0] == str(machines.INCUS)]


def test_status_before_and_after_install(ctx):
    st = run(ctx, "vm.status")["result"]
    assert [st["installed"], st["instances"]] == [False, []]
    assert st["host"] == {"cpus": 8, "memory": 16384}
    assert st["lan"] is True and st["cache"] is False
    assert "debian/13" in st["images"]
    assert incus_calls(ctx) == []

    machines.INCUS.write_text("")
    st = run(ctx, "vm.status")["result"]
    web, win = st["instances"]
    assert web == {"name": "web", "type": "container", "status": "running", "cpus": 2,
                   "memory": 1024,
                   "autostart": True, "image": "Debian trixie amd64", "addresses": ["10.12.0.5"],
                   "network": "nat", "console": False, "iso": False, "memory_used": 52428800,
                   "created": "2026-11-18T10:00:00Z"}
    assert [win["type"], win["memory"], win["network"], win["console"], win["iso"]] == [
        "vm", 8192, "lan", True, True]
    assert st["error"] is None

    ctx.runner.answers[(str(machines.INCUS), "list")] = (1, "", "Error: daemon not running")
    st = run(ctx, "vm.status")["result"]
    assert st["instances"] == [] and "daemon not running" in st["error"]


def test_install(ctx):
    reply = run(ctx, "vm.install", {"location": "system", "user": "fabian"})
    assert reply["ok"]
    cmd = ctx.runner.calls[-1]
    assert cmd[:3] == ["systemd-run", "--unit", "incus-install.service"]
    assert cmd[-3:] == ["/usr/lib/dahlia/incus-install.sh", "system", "fabian"]
    assert err_code(run(ctx, "vm.install", {"location": "cache", "user": "fabian"})) == FAILED
    assert err_code(run(ctx, "vm.install", {"location": "system", "user": "Fa bian"})) == (
        protocol.INVALID_PARAMS)
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    assert err_code(run(ctx, "vm.install", {"location": "system", "user": "fabian"})) == BUSY
    machines.INCUS.write_text("")
    assert err_code(run(ctx, "vm.install", {"location": "system", "user": "fabian"})) == FAILED


BASE = {"cpus": 2, "memory": 2048, "disk": 20, "network": "nat"}


def test_create_container_from_image(ctx):
    assert err_code(run(ctx, "vm.create", {"name": "db", "kind": "container", "source": "image",
                                           **BASE})) == protocol.FAILED  # not installed
    machines.INCUS.write_text("")
    reply = run(ctx, "vm.create", {"name": "db", "kind": "container", "source": "image",
                                   "image": "alpine/3.22", **BASE, "autostart": True})
    assert reply["result"] == {"started": True, "name": "db"}
    cmd = ctx.runner.calls[-1]
    assert cmd[:3] == ["systemd-run", "--unit", "vm-create-db.service"]
    assert cmd[-3:] == ["/usr/sbin/machines", "create", "db"]
    spec = json.loads((ctx.run_dir / "vm-create/db.json").read_text())
    assert spec["image"] == "alpine/3.22" and "vnc" not in spec
    assert machines.create_commands(spec) == [
        ["init", "images:alpine/3.22", "db", "-c", "limits.cpu=2", "-c", "limits.memory=2048MiB",
         "-c", "boot.autostart=true"],
        ["start", "db"],
    ]
    # Pending: listed, and the name is taken.
    assert run(ctx, "vm.status")["result"]["creating"][0]["name"] == "db"
    assert err_code(run(ctx, "vm.create", {"name": "db", "kind": "container", "source": "image",
                                           **BASE})) == protocol.FAILED
    assert err_code(run(ctx, "vm.create", {"name": "web", "kind": "container", "source": "image",
                                           **BASE})) == protocol.FAILED


def test_create_limits(ctx):
    machines.INCUS.write_text("")
    for params, code in [
        ({"name": "Web"}, protocol.INVALID_PARAMS),
        ({"cpus": 9}, protocol.INVALID_PARAMS),
        ({"memory": 20000}, protocol.INVALID_PARAMS),
        ({"image": "gentoo"}, protocol.INVALID_PARAMS),
        ({"source": "iso", "iso_root": "/srv", "iso_path": "isos/win11.iso"}, INVALID),
        ({"windows": True}, protocol.INVALID_PARAMS),
    ]:
        p = {"name": "x1", "kind": "container", "source": "image", **BASE, **params}
        assert err_code(run(ctx, "vm.create", p)) == code, params


def test_create_windows_vm_from_iso_on_lan(ctx):
    machines.INCUS.write_text("")
    p = {"name": "win2", "kind": "vm", "source": "iso", "iso_root": "/srv", **BASE,
         "memory": 8192, "disk": 64, "network": "lan", "windows": True}
    assert err_code(run(ctx, "vm.create", {**p, "iso_path": "isos/missing.iso"})) == protocol.FAILED
    assert err_code(run(ctx, "vm.create", {**p, "iso_path": "isos"})) == protocol.INVALID_PARAMS
    assert err_code(run(ctx, "vm.create", {**p, "iso_path": "../etc/x.iso"})) in (
        protocol.INVALID_PARAMS, protocol.FAILED)
    assert run(ctx, "vm.create", {**p, "iso_path": "isos/win11.iso"})["ok"]
    spec = json.loads((ctx.run_dir / "vm-create/win2.json").read_text())
    assert spec["vnc"] == 51  # 50 belongs to win
    iso = str(ctx.system / "isos/win11.iso")
    assert machines.create_commands(spec) == [
        ["init", "--empty", "win2", "--vm", "-c", "limits.cpu=2", "-c", "limits.memory=8192MiB",
         "-c", "boot.autostart=false", "-c", "raw.qemu=-vnc 127.0.0.1:51",
         "-c", "user.dahlia.vnc=51",
         "-c", "image.os=Windows", "-d", "root,size=64GiB,io.bus=nvme"],
        ["config", "device", "add", "win2", "eth0", "nic", "nictype=macvlan", "parent=enp1s0",
         "name=eth0"],
        ["config", "device", "add", "win2", "vtpm", "tpm"],
        ["storage", "volume", "import", "default", iso, "win2-install", "--type=iso"],
        ["config", "device", "add", "win2", "install", "disk", "pool=default",
         "source=win2-install", "boot.priority=10"],
        ["start", "win2"],
    ]
    # The next VM gets the next display.
    assert run(ctx, "vm.create", {"name": "deb", "kind": "vm", "source": "image", **BASE,
                                  "secureboot": False, "start": False})["ok"]
    spec = json.loads((ctx.run_dir / "vm-create/deb.json").read_text())
    assert spec["vnc"] == 52
    assert machines.create_commands(spec) == [
        ["init", "images:debian/13", "deb", "--vm", "-c", "limits.cpu=2",
         "-c", "limits.memory=2048MiB", "-c", "boot.autostart=false",
         "-c", "raw.qemu=-vnc 127.0.0.1:52", "-c", "user.dahlia.vnc=52",
         "-c", "security.secureboot=false", "-d", "root,size=20GiB"],
    ]


def test_create_job_cleans_up_on_failure(ctx):
    machines.INCUS.write_text("")
    assert run(ctx, "vm.create", {"name": "deb", "kind": "vm", "source": "image", **BASE})["ok"]
    ctx.runner.calls.clear()
    ctx.runner.answers[(str(machines.INCUS), "start")] = (1, "", "Error: no KVM")
    with pytest.raises(protocol.AgentError):
        machines.create(ctx, "deb")
    assert incus_calls(ctx)[-2:] == [["delete", "--force", "--", "deb"],
                                     ["storage", "volume", "delete", "default", "deb-install"]]
    assert not (ctx.run_dir / "vm-create/deb.json").exists()

    assert run(ctx, "vm.create", {"name": "deb", "kind": "vm", "source": "image", **BASE})["ok"]
    del ctx.runner.answers[(str(machines.INCUS), "start")]
    ctx.runner.calls.clear()
    machines.create(ctx, "deb")
    assert [c[0] for c in incus_calls(ctx)] == ["init", "start"]
    assert not (ctx.run_dir / "vm-create/deb.json").exists()


def test_operations(ctx):
    machines.INCUS.write_text("")
    assert run(ctx, "vm.op", {"name": "web", "op": "restart"})["ok"]
    assert incus_calls(ctx)[-1] == ["restart", "--", "web"]
    assert run(ctx, "vm.op", {"name": "web", "op": "force-stop"})["ok"]
    assert incus_calls(ctx)[-1] == ["stop", "--force", "--", "web"]
    assert err_code(run(ctx, "vm.op", {"name": "nope", "op": "start"})) == protocol.FAILED

    assert run(ctx, "vm.set", {"name": "win", "cpus": 2, "memory": 4096, "autostart": True})["ok"]
    assert incus_calls(ctx)[-1] == ["config", "set", "win", "limits.cpu=2", "limits.memory=4096MiB",
                                    "boot.autostart=true"]

    # Running: no delete, no eject.
    assert err_code(run(ctx, "vm.delete", {"name": "web"})) == protocol.FAILED
    assert err_code(run(ctx, "vm.eject", {"name": "web"})) == protocol.FAILED
    assert run(ctx, "vm.eject", {"name": "win"})["ok"]
    assert incus_calls(ctx)[-2:] == [["config", "device", "remove", "win", "install"],
                                     ["storage", "volume", "delete", "default", "win-install"]]
    assert run(ctx, "vm.delete", {"name": "win"})["ok"]
    assert incus_calls(ctx)[-2][:3] == ["delete", "--", "win"]


def test_console(ctx):
    machines.INCUS.write_text("")
    assert err_code(run(ctx, "vm.console", {"name": "web"})) == protocol.FAILED  # container
    assert err_code(run(ctx, "vm.console", {"name": "win"})) == protocol.FAILED  # stopped
    running = listing({**WIN, "status": "Running"})
    ctx.runner.answers[(str(machines.INCUS), "list")] = (0, running, "")
    assert run(ctx, "vm.console", {"name": "win"})["result"] == {"name": "win", "port": 5950}


def test_memory_values():
    assert [machines.memory_mib(v) for v in ("512MiB", "2GiB", "1073741824", "50%", "1GB")] == [
        512, 2048, 1024, None, 953]
