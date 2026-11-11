"""dahlia-backup: settings, sources, the restic calls and the agent actions.

Most tests use a fake runner. test_real_restic runs restic itself, only on
temporary folders (repository, sources and restore target), when restic is
installed.
"""

import json
import shutil

import pytest
from dahlia_agent import actions, apps, backup
from dahlia_agent.backup import Backup, BackupError
from test_agent import FakeRunner, err_code, run

WHOAMI = """\
schema: 2
id: whoami
name: Whoami
category: tools
description: {de: Test, en: Test}
services:
  app:
    image: traefik/whoami:v1.11.0
    volumes:
      - {container: /config, appdata: config}
"""


class FakeDocker:
    def __init__(self, states):
        self.states = states
        self.calls = []

    def compose(self, app_dir, *args, timeout=1800):
        self.calls.append((app_dir.name, *args))
        return ""

    def state(self, container):
        return self.states.get(container, "missing")


class FakeApps:
    def __init__(self, states):
        self.docker = FakeDocker(states)

    def status(self, t):
        return self.docker.state(apps.container_name(t.id, "app"))


class FakeRun:
    def __init__(self, answers=None, fail=()):
        self.calls = []
        self.envs = []
        self.answers = answers or {}
        self.fail = fail

    def __call__(self, cmd, env=None, timeout=None):
        self.calls.append(cmd)
        self.envs.append(env)
        for word in self.fail:
            if word in cmd:
                raise BackupError(f"restic {word}: failed")
        for word, out in self.answers.items():
            if word in cmd:
                return out
        return ""


@pytest.fixture
def b(tmp_path):
    paths = apps.Paths(apps=tmp_path / "var/apps", pool=tmp_path / "user", system=tmp_path / "srv")
    d = paths.apps / "whoami"
    d.mkdir(parents=True)
    (d / "app.yaml").write_text(WHOAMI)
    (d / "state.json").write_text(json.dumps({"location": str(tmp_path / "srv")}))
    (tmp_path / "srv/appdata/whoami/config").mkdir(parents=True)
    (tmp_path / "srv/appdata/whoami/config/app.db").write_text("data")
    (tmp_path / "etc/dahlia").mkdir(parents=True)
    (tmp_path / "dash").mkdir()
    return Backup(etc=tmp_path / "etc", state=tmp_path / "var", dashboard_state=tmp_path / "dash",
                  paths=paths, run=FakeRun(), app_manager=FakeApps({"dahlia-whoami": "running"}))


def test_check_target():
    assert backup.check_target({"type": "folder", "path": "/mnt/disk3/backup/"}) == {
        "type": "folder", "path": "/mnt/disk3/backup"}
    assert backup.check_target({"type": "sftp", "host": "nas.lan", "user": "backup",
                                "path": "dahlia"})["port"] == 22
    for bad in ({"type": "folder", "path": "/etc"}, {"type": "folder", "path": "/mnt/../etc"},
                {"type": "folder", "path": "/mnt/a;rm"}, {"type": "s3"}, "x",
                {"type": "sftp", "host": "-oProxyCommand=x", "user": "b", "path": "p"},
                {"type": "sftp", "host": "nas", "user": "b", "path": "p", "port": 0}):
        with pytest.raises(BackupError):
            backup.check_target(bad)


def test_configure_makes_password_and_key(b):
    b.configure({"type": "sftp", "host": "nas.lan", "user": "backup", "path": "dahlia"}, True, [])
    assert oct(b.password_file.stat().st_mode & 0o777) == "0o600"
    assert len(b.password()) == 64
    assert b.run.calls[0][:2] == ["ssh-keygen", "-q"]
    old = b.password()
    b.configure({"type": "folder", "path": "/mnt/disk2/backup"}, False, [])
    assert b.password() == old  # never replaced: old snapshots need it
    assert b.settings()["target"]["path"] == "/mnt/disk2/backup"


def test_folders_below_pool_or_srv(b, tmp_path):
    ok = str(tmp_path / "user/photos")
    assert backup.check_folders([ok, ok], b.paths) == [ok]
    with pytest.raises(BackupError):
        backup.check_folders(["/etc"], b.paths)


def test_sources(b, tmp_path):
    srcs = b.sources()
    assert str(tmp_path / "etc/dahlia") in srcs and str(b.paths.apps) in srcs
    assert str(tmp_path / "srv/appdata/whoami") in srcs and str(tmp_path / "dash") in srcs


def test_backup_stops_and_starts_apps(b):
    b.configure({"type": "folder", "path": "/mnt/disk2/backup"}, True, [])
    summary = json.dumps({"message_type": "summary", "snapshot_id": "abc123", "data_added": 10,
                          "total_files_processed": 3})
    b.run.answers = {"backup": '{"message_type":"status"}\n' + summary + "\n"}
    status = b.backup()
    assert status["result"] == "success" and status["snapshot"] == "abc123"
    assert b.app_manager.docker.calls == [("whoami", "stop"), ("whoami", "up", "-d")]
    restic = [c for c in b.run.calls if c[0] == "restic"]
    assert restic[0][:3] == ["restic", "--repo", "/mnt/disk2/backup"]
    assert any("backup" in c and "--tag" in c for c in restic)
    assert any("forget" in c and "--keep-daily" in c and "--prune" in c for c in restic)
    assert json.loads(b.status_file.read_text())["apps_stopped"] == ["whoami"]


def test_failed_backup_starts_apps_again(b):
    b.configure({"type": "folder", "path": "/mnt/disk2/backup"}, True, [])
    b.run.fail = ("backup",)
    with pytest.raises(BackupError):
        b.backup()
    assert b.app_manager.docker.calls[-1] == ("whoami", "up", "-d")
    status = b.status()
    assert status["result"] == "failed" and "backup" in status["error"]


def test_sftp_command(b):
    b.configure({"type": "sftp", "host": "nas.lan", "port": 2222, "user": "backup",
                 "path": "dahlia"}, True, [])
    b.restic("snapshots")
    cmd = b.run.calls[-1]
    assert cmd[2] == "sftp:backup@nas.lan:dahlia"
    opt = cmd[cmd.index("-o") + 1]
    assert opt.startswith("sftp.command=ssh -i ") and "-p 2222" in opt and "BatchMode=yes" in opt


def test_restore_app(b, tmp_path):
    b.configure({"type": "folder", "path": "/mnt/disk2/backup"}, True, [])
    b.restore("abc12345", "whoami")
    cmd = b.run.calls[-1]
    assert cmd[-6:] == ["restore", "abc12345", "--target", "/", "--include",
                        str(tmp_path / "srv/appdata/whoami")]
    assert b.app_manager.docker.calls == [("whoami", "stop"), ("whoami", "up", "-d")]
    with pytest.raises(BackupError):
        b.restore("abc12345", "nextcloud")
    with pytest.raises(BackupError):
        b.restore("../x", "whoami")


@pytest.mark.skipif(not shutil.which("restic"), reason="needs restic")
def test_real_restic(b, tmp_path, monkeypatch):
    """Back up and restore with restic itself; everything below tmp_path."""
    monkeypatch.setattr(backup, "FOLDER_TARGET_RE", __import__("re").compile(r"^/.*$"))
    b.run = backup._run
    repo = tmp_path / "repo"
    b.configure({"type": "folder", "path": str(repo)}, False, [])
    status = b.backup()
    assert status["result"] == "success" and status["snapshot"]
    snaps = b.snapshots()
    assert len(snaps) == 1 and str(tmp_path / "srv/appdata/whoami") in snaps[0]["paths"]
    data = tmp_path / "srv/appdata/whoami/config/app.db"
    data.write_text("broken")
    b.restic("restore", snaps[0]["id"], "--target", str(tmp_path / "out"),
             "--include", str(data))
    assert (tmp_path / "out" / str(data).lstrip("/")).read_text() == "data"


# --- agent actions --------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path):
    c = actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                        var=tmp_path / "var", pool=tmp_path / "user", system=tmp_path / "srv")
    (c.var / "apps/whoami").mkdir(parents=True)
    (c.var / "apps/whoami/app.yaml").write_text(WHOAMI)
    (c.var / "apps/whoami/state.json").write_text("{}")
    return c


def test_action_set_and_get(ctx):
    res = run(ctx, "backup.set", {"target": {"type": "folder", "path": "/mnt/disk2/backup"}})
    assert res["ok"], res
    assert ["systemctl", "enable", "dahlia-backup.timer"] in ctx.runner.calls
    got = run(ctx, "backup.get")["result"]
    assert got["target"] == {"type": "folder", "path": "/mnt/disk2/backup"} and got["stop_apps"]
    assert len(run(ctx, "backup.key")["result"]["password"]) == 64


def test_action_get_candidates(ctx):
    rows = [{"target": t, "fstype": "xfs", "size": 10, "avail": 5}
            for t in ("/", "/mnt/disk1", "/mnt/parity1", "/mnt/user", "/media/usb", "/srv")]
    ctx.runner.answers[("findmnt",)] = (0, json.dumps({"filesystems": rows}), "")
    got = run(ctx, "backup.get")["result"]
    assert [c["path"] for c in got["candidates"]] == [
        "/mnt/disk1/dahlia-backup", "/mnt/user/dahlia-backup", "/media/usb/dahlia-backup"]
    assert got["target"] is None


def test_action_set_invalid(ctx):
    res = run(ctx, "backup.set", {"target": {"type": "folder", "path": "/etc"}})
    assert err_code(res) == "invalid_params"


def test_action_restore_starts_unit(ctx):
    res = run(ctx, "backup.restore", {"snapshot": "abc12345", "what": "whoami"})
    assert res["ok"], res
    cmd = ctx.runner.calls[-1]
    assert cmd[:4] == ["systemd-run", "--unit", "dahlia-backup-restore.service", "--no-block"]
    assert cmd[-3:] == ["restore", "abc12345", "whoami"]
    assert err_code(run(ctx, "backup.restore", {"snapshot": "abc12345", "what": "nope"})) \
        == "invalid_params"
    assert err_code(run(ctx, "backup.restore", {"snapshot": "x;y", "what": "config"})) \
        == "invalid_params"
