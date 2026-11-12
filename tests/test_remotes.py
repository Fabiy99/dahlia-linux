"""remotes.* actions of agent: SMB and NFS shares of other devices."""

import json

import pytest
from dahlia_agent import actions, apps, protocol, remotes
from test_agent import FakeRunner, err_code, run

PASSWD = (0, "apps:x:997:997::/nonexistent:/usr/sbin/nologin\n", "")
GROUP = (0, "dahlia:x:996:\n", "")


@pytest.fixture
def ctx(tmp_path):
    c = actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                        proc=tmp_path / "proc", var=tmp_path / "var",
                        remotes=tmp_path / "remotes", pool=tmp_path / "user",
                        system=tmp_path / "srv")
    c.proc.mkdir()
    (c.proc / "self").mkdir()
    (c.proc / "self/mounts").write_text("sysfs /sys sysfs rw 0 0\n")
    c.runner.answers[("getent", "passwd", "apps")] = PASSWD
    c.runner.answers[("getent", "group", "dahlia")] = GROUP
    c.runner.answers[("systemctl", "show")] = (0, "Result=success\n", "")
    return c


def smb(**kw):
    r = {"name": "nas-fotos", "type": "smb", "host": "nas.local", "share": "Fotos",
            "username": "anna", "password": "geheim 1", **kw}
    # The dashboard leaves the password out to keep the saved one.
    return {k: v for k, v in r.items() if v is not None}


def unit_path(ctx, suffix, name="nas-fotos"):
    return ctx.etc / "systemd/system" / f"{remotes.unit_base(ctx, name)}.{suffix}"


def unit(ctx, suffix, name="nas-fotos"):
    return unit_path(ctx, suffix, name).read_text()


def test_add_smb_share(ctx):
    res = run(ctx, "remotes.set", smb())
    assert res["ok"], res
    assert res["result"]["path"] == str(ctx.remotes / "nas-fotos")
    mount = unit(ctx, "mount")
    assert "What=//nas.local/Fotos\n" in mount
    assert f"Where={ctx.remotes / 'nas-fotos'}\n" in mount and "Type=cifs\n" in mount
    cred = ctx.etc / "dahlia/remotes/nas-fotos.cred"
    assert (f"Options=credentials={cred},uid=997,gid=996,file_mode=0664,dir_mode=0775,"
            "iocharset=utf8,noperm,_netdev,nofail\n") in mount
    assert "TimeoutSec=30\n" in mount
    assert "[Automount]" in unit(ctx, "automount")
    assert cred.read_text() == "username=anna\npassword=geheim 1\n"
    assert cred.stat().st_mode & 0o777 == 0o600
    assert (ctx.remotes / "nas-fotos").is_dir()
    # Test mount first, then the units.
    test = next(c for c in ctx.runner.calls if c[0] == "mount")
    assert test[:5] == ["mount", "-t", "cifs", "//nas.local/Fotos",
                        str(ctx.run_dir / "remote-test")]
    assert "credentials=" + str(ctx.run_dir / "remote-test.cred") in test[6]
    assert not (ctx.run_dir / "remote-test.cred").exists()
    automount = f"{remotes.unit_base(ctx, 'nas-fotos')}.automount"
    assert ctx.runner.calls[-1] == ["systemctl", "enable", "--now", automount]
    saved = json.loads((ctx.etc / "dahlia/remotes.json").read_text())["remotes"]
    assert saved == [{"name": "nas-fotos", "type": "smb", "host": "nas.local", "share": "Fotos",
                      "username": "anna", "readonly": False}]
    assert "geheim" not in (ctx.etc / "dahlia/remotes.json").read_text()


def test_guest_and_readonly(ctx):
    res = run(ctx, "remotes.set", smb(username="", password=None, readonly=True))
    assert res["ok"], res
    assert ",guest,uid=997," in "," + unit(ctx, "mount").split("Options=")[1]
    assert "noperm,ro,_netdev" in unit(ctx, "mount")
    assert not (ctx.etc / "dahlia/remotes/nas-fotos.cred").exists()


def test_nfs_share(ctx):
    assert run(ctx, "remotes.set", {"name": "backup", "type": "nfs", "host": "192.168.1.20",
                                    "share": "/volume1/backup", "username": "x",
                                    "password": "y"})["ok"]
    mount = unit(ctx, "mount", "backup")
    assert "What=192.168.1.20:/volume1/backup\n" in mount and "Type=nfs\n" in mount
    assert "Options=soft,timeo=300,retrans=4,_netdev,nofail\n" in mount
    assert not (ctx.etc / "dahlia/remotes/backup.cred").exists()
    assert json.loads((ctx.etc / "dahlia/remotes.json").read_text())["remotes"][0][
        "username"] == ""


def test_failed_test_mount_saves_nothing(ctx):
    ctx.runner.answers[("mount",)] = (32, "", "mount error(13): Permission denied\n")
    reply = run(ctx, "remotes.set", smb())
    assert err_code(reply) == protocol.FAILED
    assert "Permission denied" in reply["error"]["message"]
    assert not (ctx.etc / "dahlia/remotes.json").exists()
    assert not (ctx.etc / "systemd/system").exists()
    assert not (ctx.run_dir / "remote-test.cred").exists()


def test_password_kept_when_not_given(ctx):
    assert run(ctx, "remotes.set", smb())["ok"]
    assert run(ctx, "remotes.set", smb(username="ben", password=None, readonly=True))["ok"]
    cred = ctx.etc / "dahlia/remotes/nas-fotos.cred"
    assert cred.read_text() == "username=ben\npassword=geheim 1\n"
    listed = run(ctx, "remotes.list")["result"]["remotes"]
    assert listed == [{"name": "nas-fotos", "type": "smb", "host": "nas.local", "share": "Fotos",
                       "username": "ben", "readonly": True,
                       "path": str(ctx.remotes / "nas-fotos"), "state": "off",
                       "has_password": True}]


def test_new_share_without_password_is_refused(ctx):
    assert err_code(run(ctx, "remotes.set", smb(password=None))) == protocol.INVALID_PARAMS


@pytest.mark.parametrize("change", [
    {"name": "Nas"}, {"name": "../x"}, {"type": "ftp"}, {"host": "nas;rm"},
    {"share": "a/b"}, {"share": "%h"}, {"username": "a b"}, {"password": "a\nb"},
    {"type": "nfs", "share": "volume1"}, {"type": "nfs", "share": "/a/../etc"},
])
def test_bad_params(ctx, change):
    assert err_code(run(ctx, "remotes.set", smb(**change))) == protocol.INVALID_PARAMS
    assert not any(c[0] == "mount" for c in ctx.runner.calls)


def test_unit_name_is_escaped():
    assert remotes.unit_base(actions.Context(), "nas-fotos") == "mnt-remotes-nas\\x2dfotos"


def test_state(ctx):
    assert run(ctx, "remotes.set", smb())["ok"]
    mp = ctx.remotes / "nas-fotos"
    assert remotes.mount_state(ctx, "nas-fotos") == "off"
    (ctx.proc / "self/mounts").write_text(f"systemd-1 {mp} autofs rw 0 0\n")
    assert remotes.mount_state(ctx, "nas-fotos") == "idle"
    (ctx.proc / "self/mounts").write_text(
        f"systemd-1 {mp} autofs rw 0 0\n//nas.local/Fotos {mp} cifs rw 0 0\n")
    assert remotes.mount_state(ctx, "nas-fotos") == "mounted"
    (ctx.proc / "self/mounts").write_text(f"systemd-1 {mp} autofs rw 0 0\n")
    ctx.runner.answers[("systemctl", "show")] = (0, "Result=exit-code\n", "")
    assert remotes.mount_state(ctx, "nas-fotos") == "error"


def test_remove(ctx):
    assert run(ctx, "remotes.set", smb())["ok"]
    assert run(ctx, "remotes.remove", {"name": "nas-fotos"})["ok"]
    assert not unit_path(ctx, "mount").exists()
    assert not unit_path(ctx, "automount").exists()
    assert not (ctx.etc / "dahlia/remotes/nas-fotos.cred").exists()
    assert not (ctx.remotes / "nas-fotos").exists()
    automount = f"{remotes.unit_base(ctx, 'nas-fotos')}.automount"
    assert ["systemctl", "disable", "--now", automount] in ctx.runner.calls
    assert run(ctx, "remotes.list")["result"]["remotes"] == []
    assert err_code(run(ctx, "remotes.remove", {"name": "nas-fotos"})) == protocol.FAILED


def test_remove_used_by_app_needs_force(ctx):
    assert run(ctx, "remotes.set", smb())["ok"]
    app = ctx.var / "apps/jellyfin"
    app.mkdir(parents=True)
    (app / "state.json").write_text(json.dumps(
        {"folders": {"media": f"{ctx.remotes}/nas-fotos/Urlaub"}}))
    reply = run(ctx, "remotes.remove", {"name": "nas-fotos"})
    assert err_code(reply) == protocol.FAILED and "jellyfin" in reply["error"]["message"]
    assert (ctx.etc / "dahlia/remotes.json").read_text().count("nas-fotos") == 1
    assert run(ctx, "remotes.remove", {"name": "nas-fotos", "force": True})["ok"]


def test_folder_picker_lists_remotes(ctx):
    browse = run(ctx, "files.browse", {"root": "/mnt/remotes"})["result"]
    assert browse["mounted"] is False
    assert {"name": "remotes", "path": "/mnt/remotes", "available": False} in browse["locations"]
    assert run(ctx, "remotes.set", smb())["ok"]
    (ctx.remotes / "nas-fotos/Urlaub").mkdir()
    browse = run(ctx, "files.browse", {"root": "/mnt/remotes"})["result"]
    assert browse["mounted"] is True and browse["dirs"] == ["nas-fotos"]
    assert run(ctx, "files.browse", {"root": "/mnt/remotes", "path": "nas-fotos"})[
        "result"]["dirs"] == ["Urlaub"]
    # New folders only inside a share, never a new top-level entry.
    assert err_code(run(ctx, "files.mkdir", {"root": "/mnt/remotes", "path": "neu"})) == \
        protocol.INVALID_PARAMS
    assert run(ctx, "files.mkdir", {"root": "/mnt/remotes", "path": "nas-fotos/Neu"})["ok"]


def test_app_folder_on_remote(tmp_path):
    paths = apps.Paths(apps=tmp_path / "apps", pool=tmp_path / "user", system=tmp_path / "srv",
                       remotes=tmp_path / "remotes")
    ref = f"{paths.remotes}/nas-fotos/Urlaub"
    assert apps.split_folder(ref, paths) == (str(paths.remotes), "nas-fotos/Urlaub")
    assert paths.share_root(str(paths.remotes)) == paths.remotes
    # App data never goes to a remote share.
    with pytest.raises(apps.AppError, match="unknown location"):
        paths.root(str(paths.remotes))
