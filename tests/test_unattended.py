"""updates.auto / updates.set_auto: automatic security updates."""

import pytest
from dahlia_agent import actions, protocol, unattended
from test_agent import FakeRunner, err_code, run

LOG = "".join(f"{line}\n" for line in [
    "2026-11-09 06:12:01,100 INFO Starting unattended upgrades script",
    "2026-11-09 06:12:01,101 INFO Allowed origins are: origin=Debian,codename=trixie-security",
    "2026-11-09 06:12:09,300 INFO No packages found that can be upgraded unattended"
    " and no pending auto-removals",
    "2026-11-10 06:20:11,000 INFO Starting unattended upgrades script",
    "2026-11-10 06:20:11,001 INFO Allowed origins are: origin=Debian,codename=trixie-security",
    "2026-11-10 06:20:14,000 INFO Packages that will be upgraded: libssl3t64 openssl",
    "2026-11-10 06:20:14,001 INFO Writing dpkg log to"
    " /var/log/unattended-upgrades/unattended-upgrades-dpkg.log",
    "2026-11-10 06:20:40,000 INFO All upgrades installed",
])


@pytest.fixture
def ctx(tmp_path):
    return actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                           var=tmp_path / "var", log_dir=tmp_path / "log",
                           reboot_flag=tmp_path / "reboot-required")


def write_log(ctx, text):
    d = ctx.log_dir / "unattended-upgrades"
    d.mkdir(parents=True, exist_ok=True)
    (d / "unattended-upgrades.log").write_text(text)


def test_defaults_without_files(ctx):
    res = run(ctx, "updates.auto")["result"]
    assert res == {"enabled": False, "reboot": False, "reboot_time": "04:00", "last": None,
                   "reboot_required": None}


def test_reads_the_debian_switch(ctx):
    f = unattended.periodic_file(ctx)
    f.parent.mkdir(parents=True)
    f.write_text('APT::Periodic::Update-Package-Lists "1";\n'
                 'APT::Periodic::Unattended-Upgrade "1";\n')
    assert run(ctx, "updates.auto")["result"]["enabled"] is True
    f.write_text('APT::Periodic::Unattended-Upgrade "0";\n')
    assert run(ctx, "updates.auto")["result"]["enabled"] is False


def test_set_writes_apt_config(ctx):
    res = run(ctx, "updates.set_auto",
              {"enabled": True, "reboot": True, "reboot_time": "03:30"})["result"]
    assert res["enabled"] and res["reboot"] and res["reboot_time"] == "03:30"
    periodic = unattended.periodic_file(ctx).read_text()
    assert 'APT::Periodic::Unattended-Upgrade "1";' in periodic
    reboot = unattended.reboot_file(ctx).read_text()
    assert 'Unattended-Upgrade::Automatic-Reboot "true";' in reboot
    assert 'Unattended-Upgrade::Automatic-Reboot-Time "03:30";' in reboot
    run(ctx, "updates.set_auto", {"enabled": False, "reboot": False, "reboot_time": "03:30"})
    assert 'Unattended-Upgrade "0";' in unattended.periodic_file(ctx).read_text()
    assert 'Automatic-Reboot "false";' in unattended.reboot_file(ctx).read_text()
    # Package lists stay fresh for the dashboard either way.
    assert 'Update-Package-Lists "1";' in unattended.periodic_file(ctx).read_text()


@pytest.mark.parametrize("value", ["24:00", "4:00", "04:00;", 'now"; x "', ""])
def test_set_rejects_bad_time(ctx, value):
    params = {"enabled": True, "reboot": True, "reboot_time": value}
    assert err_code(run(ctx, "updates.set_auto", params)) == protocol.INVALID_PARAMS
    assert not unattended.reboot_file(ctx).exists()


def test_last_run_from_log(ctx):
    write_log(ctx, LOG)
    last = run(ctx, "updates.auto")["result"]["last"]
    assert last == {"time": "2026-11-10 06:20:11", "result": "installed",
                    "packages": ["libssl3t64", "openssl"], "errors": []}


def test_last_run_failed_and_running(ctx):
    write_log(ctx, LOG + "2026-11-11 06:10:00,000 INFO Starting unattended upgrades script\n")
    assert unattended.last_run(ctx)["result"] == "running"
    write_log(ctx, LOG + "2026-11-11 06:10:00,000 INFO Starting unattended upgrades script\n"
              "2026-11-11 06:10:05,000 INFO Packages that will be upgraded: libc6\n"
              "2026-11-11 06:10:30,000 ERROR Installing the upgrades failed!\n"
              "2026-11-11 06:10:30,001 ERROR error message: dpkg was interrupted\n")
    last = unattended.last_run(ctx)
    assert last["result"] == "failed" and last["packages"] == ["libc6"]
    assert last["errors"] == ["Installing the upgrades failed!",
                              "error message: dpkg was interrupted"]


def test_reboot_required(ctx):
    ctx.reboot_flag.write_text("*** System restart required ***\n")
    assert run(ctx, "updates.auto")["result"]["reboot_required"] == []
    pkgs = ctx.reboot_flag.parent / "reboot-required.pkgs"
    pkgs.write_text("libc6\nlinux-image-amd64\nlibc6\n")
    assert unattended.reboot_required(ctx) == ["libc6", "linux-image-amd64"]
