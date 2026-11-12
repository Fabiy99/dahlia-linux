"""shares.* actions of dahlia-agent: SMB shares and users."""

import json
import os

import pytest
from dahlia_agent import actions, protocol, smb
from test_agent import FakeRunner, err_code, run

GROUP = (0, "dahlia-smb:x:998:anna,ben\n", "")


@pytest.fixture
def ctx(tmp_path):
    c = actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                        pool=tmp_path / "user", var=tmp_path / "var")
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
                      "time_machine": False, "time_machine_size": 0, "nfs": False,
                      "recycle_days": 0}]


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
                   "running": False, "nfs_running": False}


def test_shares_reload_firewall(ctx):
    rules = str(ctx.run_dir / "firewall.nft")
    assert run(ctx, "shares.set", {"name": "media", "security": "public"})["ok"]
    assert ["nft", "-f", rules] in ctx.runner.calls
    assert "445" in (ctx.run_dir / "firewall.nft").read_text()
    assert run(ctx, "shares.remove", {"name": "media"})["ok"]
    assert "445" not in (ctx.run_dir / "firewall.nft").read_text()


DAHLIA = (0, "dahlia:x:1001:\n", "")


def exports(ctx):
    return (ctx.etc / "exports.d/dahlia.exports").read_text()


def test_nfs_export_public_and_secure(ctx):
    ctx.runner.answers[("getent", "group", "dahlia")] = DAHLIA
    (ctx.pool / "docs").mkdir()
    assert run(ctx, "shares.set", {"name": "media", "security": "public", "nfs": True})["ok"]
    assert run(ctx, "shares.set", {"name": "docs", "security": "secure", "nfs": True,
                                   "users": {"anna": "rw"}})["ok"]
    lines = exports(ctx).splitlines()
    media = next(ln for ln in lines if ln.startswith(f"{ctx.pool / 'media'} "))
    docs = next(ln for ln in lines if ln.startswith(f"{ctx.pool / 'docs'} "))
    assert f"192.168.0.0/16(rw,sync,no_subtree_check,all_squash,anonuid=65534,anongid=1001," \
           f"fsid={smb.nfs_fsid('media')},sec=sys)" in media
    assert "fd00::/7" not in media and "fc00::/7(rw," in media
    assert "(ro," in docs and "(rw," not in docs
    assert smb.nfs_fsid("media") != smb.nfs_fsid("docs") and smb.nfs_fsid("media") > 0
    assert ["systemctl", "enable", "--now", "nfs-server.service"] in ctx.runner.calls
    assert ["exportfs", "-ra"] in ctx.runner.calls
    assert smb.load_shares(ctx)[0]["nfs"] is True
    nft = (ctx.run_dir / "firewall.nft").read_text()
    assert "2049" in nft


def test_nfs_off_stops_server_and_closes_port(ctx):
    ctx.runner.answers[("getent", "group", "dahlia")] = DAHLIA
    run(ctx, "shares.set", {"name": "media", "security": "public", "nfs": True})
    assert run(ctx, "shares.set", {"name": "media", "security": "public"})["ok"]
    assert not (ctx.etc / "exports.d/dahlia.exports").exists()
    assert ["systemctl", "disable", "--now", "nfs-server.service"] in ctx.runner.calls
    nft = (ctx.run_dir / "firewall.nft").read_text()
    assert "2049" not in nft and "445" in nft


def test_nfs_removed_with_share(ctx):
    ctx.runner.answers[("getent", "group", "dahlia")] = DAHLIA
    run(ctx, "shares.set", {"name": "media", "security": "public", "nfs": True})
    assert run(ctx, "shares.remove", {"name": "media"})["ok"]
    assert not (ctx.etc / "exports.d/dahlia.exports").exists()


def test_nfs_not_for_private(ctx):
    reply = run(ctx, "shares.set", {"name": "media", "security": "private",
                                    "users": {"anna": "rw"}, "nfs": True})
    assert err_code(reply) == protocol.INVALID_PARAMS
    assert not (ctx.etc / "exports.d").exists()


def test_nfs_flag_of_private_share_in_file_is_ignored(ctx):
    (ctx.etc / "dahlia").mkdir(parents=True)
    (ctx.etc / "dahlia/shares.json").write_text(json.dumps({"shares": [
        {"name": "media", "security": "private", "users": {"anna": "rw"}, "nfs": True}]}))
    assert smb.load_shares(ctx)[0]["nfs"] is False


def test_nfs_exports_follow_firewall_networks(ctx):
    ctx.runner.answers[("getent", "group", "dahlia")] = DAHLIA
    run(ctx, "shares.set", {"name": "media", "security": "public", "nfs": True})
    assert "203.0.113.0/24" not in exports(ctx)
    assert run(ctx, "settings.set_firewall",
               {"enabled": True, "networks": ["203.0.113.0/24"]})["ok"]
    assert "203.0.113.0/24(rw," in exports(ctx)


def test_time_machine_size(ctx):
    assert run(ctx, "shares.set", {"name": "media", "security": "public", "time_machine": True,
                                   "time_machine_size": 500})["ok"]
    assert "fruit:time machine max size = 500G\n" in conf(ctx)
    assert smb.load_shares(ctx)[0]["time_machine_size"] == 500
    # Without Time Machine the limit is dropped.
    assert run(ctx, "shares.set", {"name": "media", "security": "public",
                                   "time_machine_size": 500})["ok"]
    assert "max size" not in conf(ctx)
    assert smb.load_shares(ctx)[0]["time_machine_size"] == 0
    assert err_code(run(ctx, "shares.set", {"name": "media", "security": "public",
                                            "time_machine_size": -1})) == protocol.INVALID_PARAMS


# --- recycle bin ------------------------------------------------------------------


def test_recycle_bin_config(ctx):
    res = run(ctx, "shares.set", {"name": "media", "security": "public", "recycle_days": 30})
    assert res["result"]["recycle_days"] == 30
    text = conf(ctx)
    assert "   vfs objects = catia fruit streams_xattr recycle\n" in text
    assert "   recycle:repository = .recycle\n" in text
    assert "   recycle:keeptree = yes\n" in text and "   recycle:touch = yes\n" in text
    assert smb.load_shares(ctx)[0]["recycle_days"] == 30
    run(ctx, "shares.set", {"name": "media", "security": "public"})
    assert "recycle:" not in conf(ctx).split("[media]")[1]


def test_recycle_days_rejects(ctx):
    for days in (-1, 366):
        assert err_code(run(ctx, "shares.set", {"name": "media", "security": "public",
                                                "recycle_days": days})) == protocol.INVALID_PARAMS


def touch(path, age_days, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 10)
    t = now - age_days * 86400
    os.utime(path, (t, t))


def test_clean_recycle_by_age(ctx):
    now = 2_000_000_000
    ctx.clock = lambda: now
    run(ctx, "shares.set", {"name": "media", "security": "public", "recycle_days": 7})
    bin_ = ctx.pool / "media/.recycle"
    touch(bin_ / "Filme/alt.mkv", 10, now)
    touch(bin_ / "Filme/neu.mkv", 2, now)
    touch(bin_ / "weg/ganz/alt.txt", 30, now)
    outside = ctx.pool / "media/Filme/alt.mkv"
    touch(outside, 400, now)
    out = smb.clean_recycle(ctx)
    assert out == {"media": {"files": 2, "bytes": 20}}
    assert (bin_ / "Filme/neu.mkv").exists() and not (bin_ / "Filme/alt.mkv").exists()
    assert not (bin_ / "weg").exists()  # empty folders go too
    assert outside.exists()  # never outside the bin


def test_clean_recycle_skips_symlinked_bin(ctx, tmp_path):
    now = 2_000_000_000
    ctx.clock = lambda: now
    run(ctx, "shares.set", {"name": "media", "security": "public", "recycle_days": 1})
    target = tmp_path / "elsewhere"
    touch(target / "important.txt", 100, now)
    (ctx.pool / "media/.recycle").symlink_to(target)
    assert smb.clean_recycle(ctx) == {}
    assert run(ctx, "shares.empty_recycle", {"name": "media"})["result"]["files"] == 0
    assert (target / "important.txt").exists()


def test_empty_recycle(ctx, tmp_path):
    run(ctx, "shares.set", {"name": "media", "security": "public", "recycle_days": 30})
    bin_ = ctx.pool / "media/.recycle"
    touch(bin_ / "a/b.txt", 0, 2_000_000_000)
    target = tmp_path / "elsewhere"
    touch(target / "keep.txt", 0, 2_000_000_000)
    (bin_ / "link").symlink_to(target)
    res = run(ctx, "shares.empty_recycle", {"name": "media"})["result"]
    assert res["files"] == 1
    assert bin_.is_dir() and list(bin_.iterdir()) == []
    assert (target / "keep.txt").exists()
    assert err_code(run(ctx, "shares.empty_recycle", {"name": "other"})) == protocol.INVALID_PARAMS
    assert err_code(run(ctx, "shares.empty_recycle", {"name": "../etc"})) \
        == protocol.INVALID_PARAMS
