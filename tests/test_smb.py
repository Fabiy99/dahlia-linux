"""shares.* actions of dahlia-agent: SMB shares and users."""

import json

import pytest
from dahlia_agent import actions, protocol, smb
from test_agent import FakeRunner, err_code, run

GROUP = (0, "dahlia-smb:x:998:anna,ben\n", "")


@pytest.fixture
def ctx(tmp_path):
    c = actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                        pool=tmp_path / "user")
    c.pool.mkdir()
    (c.pool / "media").mkdir()
    (c.etc / "samba").mkdir(parents=True)
    (c.etc / "samba/smb.conf").write_text("[global]\n   workgroup = DEBIAN\n[homes]\n")
    c.runner.answers[("getent", "group", "dahlia-smb")] = GROUP
    return c


def conf(ctx):
    return (ctx.etc / "samba/smb.conf").read_text()


def test_create_public_share(ctx):
    res = run(ctx, "shares.set", {"name": "media", "security": "public", "comment": "Filme"})
    assert res["ok"], res
    text = conf(ctx)
    assert "[media]\n" in text and f"path = {ctx.pool / 'media'}\n" in text
    assert "guest ok = yes\n   read only = no\n" in text and "[homes]" not in text
    assert "comment = Filme" in text and "force group = dahlia" in text
    # The Debian original is kept once.
    assert "workgroup = DEBIAN" in (ctx.etc / "samba/smb.conf.dahlia-orig").read_text()
    assert ["testparm", "-s", str(ctx.etc / "samba/smb.conf.dahlia-new")] in ctx.runner.calls
    assert ["systemctl", "enable", "--now", "smbd.service"] in ctx.runner.calls
    assert "_smb._tcp" in (ctx.etc / "avahi/services/dahlia-smb.service").read_text()
    saved = json.loads((ctx.etc / "dahlia/shares.json").read_text())["shares"]
    assert saved == [{"name": "media", "comment": "Filme", "security": "public", "users": {},
                      "time_machine": False}]


def test_private_share_with_users_and_time_machine(ctx):
    (ctx.pool / "backup").mkdir()
    assert run(ctx, "shares.set", {"name": "backup", "security": "private",
                                   "users": {"anna": "rw", "ben": "ro"},
                                   "time_machine": True})["ok"]
    text = conf(ctx)
    assert "valid users = anna ben\n" in text and "write list = anna\n" in text
    assert "guest ok = no\n   read only = yes\n" in text
    assert "fruit:time machine = yes" in text
    assert "adVN=backup" in (ctx.etc / "avahi/services/dahlia-smb.service").read_text()


def test_secure_share(ctx):
    assert run(ctx, "shares.set", {"name": "media", "security": "secure",
                                   "users": {"ben": "rw"}})["ok"]
    text = conf(ctx)
    assert "guest ok = yes\n   read only = yes\n   write list = ben\n" in text
    assert "valid users" not in text


def test_update_replaces_share(ctx):
    run(ctx, "shares.set", {"name": "media", "security": "public"})
    run(ctx, "shares.set", {"name": "media", "security": "private", "users": {"anna": "rw"}})
    shares = run(ctx, "shares.list")["result"]["shares"]
    assert [(s["name"], s["security"], s["exists"]) for s in shares] == [
        ("media", "private", True)]


@pytest.mark.parametrize("params", [
    {"name": "../etc", "security": "public"},
    {"name": "media/x", "security": "public"},
    {"name": "-media", "security": "public"},
    {"name": "homes", "security": "public"},
    {"name": "IPC$", "security": "public"},
    {"name": "media", "security": "open"},
    {"name": "media", "security": "private"},
    {"name": "media", "security": "private", "users": {"root": "rw"}},
    {"name": "media", "security": "private", "users": {"anna": "admin"}},
    {"name": "media", "security": "public", "comment": "x\n[evil]\npath = /"},
    {"name": "media", "security": "public", "comment": "a = b"},
])
def test_share_rejects(ctx, params):
    assert err_code(run(ctx, "shares.set", params)) == protocol.INVALID_PARAMS
    assert not (ctx.etc / "dahlia/shares.json").exists()
    assert "workgroup = DEBIAN" in conf(ctx)


def test_share_needs_mounted_pool(ctx):
    reply = run(ctx, "shares.set", {"name": "new", "security": "public"})
    assert err_code(reply) == protocol.FAILED and "not mounted" in reply["error"]["message"]
    assert not (ctx.pool / "new").exists()


def test_testparm_failure_keeps_old_conf(ctx):
    ctx.runner.answers[("testparm",)] = (1, "", "Unknown parameter encountered\n")
    reply = run(ctx, "shares.set", {"name": "media", "security": "public"})
    assert err_code(reply) == protocol.FAILED and "testparm" in reply["error"]["message"]
    assert "workgroup = DEBIAN" in conf(ctx)
    assert not (ctx.etc / "dahlia/shares.json").exists()
    assert not any(c[:2] == ["systemctl", "enable"] for c in ctx.runner.calls)


def test_remove_last_share_stops_samba(ctx):
    run(ctx, "shares.set", {"name": "media", "security": "public"})
    assert run(ctx, "shares.remove", {"name": "media"})["ok"]
    assert "[media]" not in conf(ctx)
    assert ["systemctl", "disable", "--now", "smbd.service", "nmbd.service"] in ctx.runner.calls
    assert not (ctx.etc / "avahi/services/dahlia-smb.service").exists()
    assert (ctx.pool / "media").is_dir()
    assert err_code(run(ctx, "shares.remove", {"name": "media"})) == protocol.FAILED


def test_user_add(ctx):
    ctx.runner.answers[("getent", "passwd")] = (2, "", "")
    assert run(ctx, "shares.user_add", {"name": "carla", "password": "correct horse"})["ok"]
    useradd = next(c for c in ctx.runner.calls if c[0] == "useradd")
    assert useradd[-1] == "carla" and "dahlia,dahlia-smb" in useradd
    i = ctx.runner.calls.index(["smbpasswd", "-s", "-a", "carla"])
    assert ctx.runner.inputs[i] == "correct horse\ncorrect horse\n"
    # The password never appears on a command line.
    assert not any("correct horse" in " ".join(c) for c in ctx.runner.calls)


def test_user_add_existing_and_rollback(ctx):
    assert err_code(run(ctx, "shares.user_add", {"name": "root", "password": "12345678"})) == \
        protocol.FAILED
    ctx.runner.answers[("getent", "passwd")] = (2, "", "")
    ctx.runner.answers[("smbpasswd",)] = (1, "", "Failed to add entry\n")
    assert err_code(run(ctx, "shares.user_add", {"name": "dora", "password": "12345678"})) == \
        protocol.FAILED
    assert ctx.runner.calls[-1] == ["userdel", "dora"]


@pytest.mark.parametrize("params", [
    {"name": "Carla", "password": "12345678"},
    {"name": "carla;id", "password": "12345678"},
    {"name": "carla", "password": "short"},
    {"name": "carla", "password": "line\nbreak1"},
])
def test_user_rejects(ctx, params):
    assert err_code(run(ctx, "shares.user_add", params)) == protocol.INVALID_PARAMS
    assert ctx.runner.calls == []


def test_user_password_only_for_managed(ctx):
    assert err_code(run(ctx, "shares.user_password",
                        {"name": "root", "password": "12345678"})) == protocol.FAILED
    assert run(ctx, "shares.user_password", {"name": "anna", "password": "neues passwort"})["ok"]
    assert ctx.runner.calls[-1] == ["smbpasswd", "-s", "anna"]


def test_user_remove_drops_from_shares(ctx):
    run(ctx, "shares.set", {"name": "media", "security": "private",
                            "users": {"anna": "rw", "ben": "ro"}})
    assert run(ctx, "shares.user_remove", {"name": "anna"})["ok"]
    assert "valid users = ben\n" in conf(ctx)
    assert smb.load_shares(ctx)[0]["users"] == {"ben": "ro"}
    assert ctx.runner.calls[-2:] == [["smbpasswd", "-x", "anna"], ["userdel", "anna"]]
    assert err_code(run(ctx, "shares.user_remove", {"name": "root"})) == protocol.FAILED


def test_list(ctx):
    ctx.runner.answers[("systemctl", "is-active")] = (3, "inactive\n", "")
    out = run(ctx, "shares.list")["result"]
    assert out == {"shares": [], "users": ["anna", "ben"], "pool_mounted": False,
                   "running": False}
