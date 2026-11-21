"""unraid.*: take over an Unraid setup staged by the migration helper."""

import json

import pytest
from dahlia_agent import actions, unraid, vpn
from test_agent import INACTIVE, RUNNING, FakeRunner, err_code, run

SERVER_KEY = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
PHONE = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="
PSK = "FpCyhws9cxwWoV4xELtfJvjJN+zQVRPISllRWgeorVU="
LAPTOP = "HIgo9xNzJMWLKASShiTqIybxZ0U3wGLiUeJ1PKf8ykw="
HASH = "8846F7EAEE8FB117AD06BDD830B7586C"

SUMMARY = {
    "found": True,
    "identity": {"hostname": "tower", "timezone": "Europe/Berlin"},
    "network": {"mode": "dhcp"},
    "disks": [
        {"slot": "flash", "role": "flash", "device": "sda", "id": "Cruzer", "fs": "vfat",
         "pool": ""},
        {"slot": "parity", "role": "parity", "device": "sdb", "id": "WDC_WD40_PAR1", "fs": "",
         "pool": ""},
        {"slot": "disk1", "role": "data", "device": "sdc", "id": "WDC_WD40_DATA1", "fs": "xfs",
         "pool": ""},
        {"slot": "disk2", "role": "data", "device": "sdd", "id": "WDC_WD40_DATA2",
         "fs": "luks:xfs", "pool": ""},
        {"slot": "cache", "role": "cache", "device": "nvme0n1", "id": "Samsung_SSD_S4EW",
         "fs": "btrfs", "pool": "cache"},
    ],
    "shares": [
        {"name": "Filme", "comment": "Filme", "smb": True, "security": "public",
         "cache": "yes", "read": [], "write": []},
        {"name": "Fotos", "comment": "", "smb": True, "security": "private",
         "cache": "no", "read": ["ben"], "write": ["anna"]},
        {"name": "system", "comment": "", "smb": False, "security": "public",
         "cache": "prefer", "read": [], "write": []},
    ],
    "users": [{"name": "anna", "smb_password": True}, {"name": "ben", "smb_password": False}],
    "containers": [
        {"name": "Plex-Media-Server", "image": "plexinc/pms-docker", "network": "bridge",
         "webui": "http://[IP]:[PORT:32400]/web", "file": "my-plex.xml",
         "ports": [{"host": 32400, "container": 32400, "protocol": "tcp"}],
         "paths": [{"host": "/mnt/user/appdata/Plex-Media-Server", "container": "/config",
                    "readonly": False},
                   {"host": "/mnt/user/Filme", "container": "/media", "readonly": True},
                   {"host": "/tmp", "container": "/transcode", "readonly": False}],
         "env": {"TZ": "Europe/Berlin", "PLEX_CLAIM": "", "BAD": "{X}"},
         "devices": ["/dev/dri"], "privileged": False},
        {"name": "qbittorrent", "image": "lscr.io/linuxserver/qbittorrent:4.6.7",
         "network": "bridge", "webui": "http://[IP]:[PORT:8080]", "file": "my-qb.xml",
         "ports": [{"host": 8080, "container": 8080, "protocol": "tcp"},
                   {"host": 6881, "container": 6881, "protocol": "udp"}],
         "paths": [{"host": "/mnt/cache/appdata/qbittorrent", "container": "/config",
                    "readonly": False}],
         "env": {}, "devices": [], "privileged": False},
        {"name": "homeassistant", "image": "ghcr.io/home-assistant/home-assistant:latest",
         "network": "host", "webui": "", "file": "my-ha.xml", "ports": [],
         "paths": [{"host": "/mnt/user/appdata/homeassistant", "container": "/config",
                    "readonly": False}],
         "env": {}, "devices": [], "privileged": True},
    ],
    "appdata": "/mnt/user/appdata/",
    "wireguard": ["wg0.conf"],
    "vms": True,
}

WG0 = f"""[Interface]
#Remote access
PrivateKey={SERVER_KEY}
Address=10.253.0.1
ListenPort=51820
PostUp=logger -t wireguard 'Tunnel WireGuard-wg0 started'
[Peer]
#Annas Handy
PublicKey={PHONE}
PresharedKey={PSK}
AllowedIPs=10.253.0.2
[Peer]
#Laptop
PublicKey={LAPTOP}
AllowedIPs=10.253.0.3/32, 192.168.5.0/24
"""


def disk(path, parent, serial, status, uuid=None, fstype=None):
    return {"path": path, "disk": parent, "serial": serial, "status": status, "uuid": uuid,
            "fstype": fstype}


DISKS = [
    disk("/dev/sdb1", "/dev/sdb", "PAR1", "empty"),
    disk("/dev/sdc1", "/dev/sdc", "DATA1", "candidate", "1111-aaaa-bbbb", "xfs"),
    disk("/dev/sdd1", "/dev/sdd", "DATA2", "locked"),
    disk("/dev/nvme0n1p1", "/dev/nvme0n1", "S4EW", "candidate", "2222-cccc-dddd", "btrfs"),
]
CATALOG = {"apps": [{"id": "plex", "image": "plexinc/pms-docker:1.41.3",
                     "services": [{"name": "app", "image": "plexinc/pms-docker:1.41.3"}]}]}


@pytest.fixture
def ctx(tmp_path):
    runner = FakeRunner({
        ("disk", "list", "--json"): (0, json.dumps(DISKS), ""),
        ("disk", "format"): (0, json.dumps({"device": "/dev/sdb", "uuid": "3333-eeee"}), ""),
        ("getent", "group", "smb"): (0, "smb:x:998:anna\n", ""),
        ("getent", "group"): (0, "dahlia:x:997:\n", ""),
        ("apps", "catalog"): (0, json.dumps(CATALOG), ""),
        ("apps", "list"): (0, "[]", ""),
        ("systemctl", "show"): (0, INACTIVE, ""),
        ("id", "-u"): (0, "996\n", ""),
        ("wg", "pubkey"): (0, PHONE + "\n", ""),
        ("ip", "-j", "addr"): (0, "[]", ""),
    })
    mnt = tmp_path / "mnt"
    c = actions.Context(runner=runner, etc=tmp_path / "etc", run_dir=tmp_path / "run",
                        var=tmp_path / "var", pool=mnt / "user", clock=lambda: 1_794_000_000)
    (mnt / "user").mkdir(parents=True)
    for name in ("Filme", "Fotos"):
        (mnt / "user" / name).mkdir()
    (c.etc / "samba").mkdir(parents=True)
    (c.etc / "samba/smb.conf").write_text("[global]\n")
    b = unraid.base(c)
    (b / "config/wireguard").mkdir(parents=True)
    (b / "unraid.json").write_text(json.dumps(SUMMARY))
    (b / "config/smbpasswd").write_text(
        f"anna:1000:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX:{HASH}:[U          ]:LCT-5F1E2B3C:\n")
    (b / "config/wireguard/wg0.conf").write_text(WG0)
    return c


def items(plan, kind):
    return {i["name"]: i for i in plan[kind]}


def test_no_unraid(tmp_path):
    c = actions.Context(runner=FakeRunner(), var=tmp_path)
    assert run(c, "unraid.status")["result"] == {"found": False}
    assert err_code(run(c, "unraid.import")) == "failed"


def test_plan(ctx):
    res = run(ctx, "unraid.status")["result"]
    assert res["found"] and res["dismissed"] is False and res["job"] is None
    plan = res["plan"]
    disks = items(plan, "disks")
    assert "flash" not in disks
    assert disks["disk1"]["state"] == "ready" and disks["disk1"]["uuid"] == "1111-aaaa-bbbb"
    assert disks["disk2"]["state"] == "skip" and disks["disk2"]["reason"] == "encrypted"
    assert disks["parity"]["state"] == "confirm" and disks["parity"]["device"] == "/dev/sdb"
    assert disks["cache"]["state"] == "ready"
    users = items(plan, "users")
    assert users["anna"]["state"] == "done"  # already in the smb group
    assert users["ben"]["state"] == "skip" and users["ben"]["reason"] == "no_password"
    shares = items(plan, "shares")
    assert shares["Filme"]["state"] == "ready" and shares["Filme"]["security"] == "public"
    assert shares["Fotos"]["users"] == {"anna": "rw"}
    assert shares["system"]["reason"] == "not_exported"
    wg = plan["wireguard"]
    assert wg["state"] == "ready" and wg["subnet"] == "10.253.0.0/24"
    assert wg["peers"] == ["Annas Handy", "Laptop"] and "private_key" not in wg
    assert plan["not_taken"] == ["vms", "plugins"]


def test_containers(ctx):
    plan = run(ctx, "unraid.status")["result"]["plan"]
    c = items(plan, "containers")
    plex = c["Plex-Media-Server"]
    assert plex["state"] == "ready" and plex["id"] == "plex-media-server"
    assert plex["image"] == "plexinc/pms-docker:1.41.3"
    keys = {n["key"] for n in plex["notes"]}
    assert {"catalog_tag", "path_skipped", "variable_skipped"} <= keys
    qb = c["qbittorrent"]
    assert qb["state"] == "ready"
    assert {"key": "port_changed", "value": "8080->8100"} in qb["notes"]
    ha = c["homeassistant"]
    assert ha["state"] == "needs_tag"
    assert {n["key"] for n in ha["notes"]} >= {"network", "privileged"}


def test_container_spec():
    from dahlia_agent import apps

    c = SUMMARY["containers"][0]
    item = unraid.container_item(c, "plex", "/mnt/user/appdata/", {"cache"},
                                 {"plexinc/pms-docker": "1.41.3"}, None, set(apps.RESERVED_PUBLIC))
    spec = item["spec"]
    assert spec["web_port"] == 32400 and spec["public_port"] == 32400
    assert spec["volumes"] == [{"container": "/config", "appdata": "plex-media-server"},
                               {"container": "/media", "folder": "/mnt/user/Filme",
                                "readonly": True}]
    assert spec["environment"] == {"TZ": "Europe/Berlin", "PLEX_CLAIM": ""}
    assert spec["gpu"] is True
    assert item["moves"] == [{"from": "Plex-Media-Server", "to": "plex-media-server"}]
    # A version the user typed wins over "latest".
    ha = unraid.container_item(SUMMARY["containers"][2], "homeassistant", "/mnt/user/appdata/",
                               set(), {}, "2025.11.3", set())
    assert ha["state"] == "ready"
    assert ha["spec"]["image"] == "ghcr.io/home-assistant/home-assistant:2025.11.3"


def test_host_folder():
    assert unraid.host_folder("/mnt/disk3/Filme/", set()) == "Filme"
    assert unraid.host_folder("/mnt/user0/a/b", set()) == "a/b"
    assert unraid.host_folder("/mnt/nvme/appdata/x", {"nvme"}) == "appdata/x"
    assert unraid.host_folder("/boot/config", set()) is None
    assert unraid.slug("Plex-Media-Server") == "plex-media-server"
    assert unraid.slug("2fauth") == "app-2fauth"
    assert unraid.split_image("localhost:5000/a/b") == ("localhost:5000/a/b", "")
    assert unraid.normal_repo("docker.io/library/nginx") == "nginx"


def test_parse_wg_skips_bad_peers():
    wg = unraid.parse_wg(WG0 + "[Peer]\n#bad\nPublicKey=nope\nAllowedIPs=10.253.0.9\n")
    assert len(wg["peers"]) == 3 and wg["peers"][0]["#"] == "Annas Handy"
    assert unraid.parse_wg("[Interface]\nAddress=10.0.0.1\n") is None


def test_import_starts_unit(ctx):
    res = run(ctx, "unraid.import", {"parity": True, "tags": {"homeassistant": "2025.11.3"}})
    assert res["ok"], res
    assert ["systemd-run", "--unit=unraid-import.service", "--description=Dahlia: Unraid import",
            "--property=After=docker.service", "--", "/usr/sbin/unraid-import"] \
        in ctx.runner.calls
    req = json.loads(unraid.request_file(ctx).read_text())
    assert req == {"parity": True, "tags": {"homeassistant": "2025.11.3"}}
    assert err_code(run(ctx, "unraid.import", {"tags": {"x": "latest"}})) == "invalid_params"
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    assert err_code(run(ctx, "unraid.import")) == "busy"
    # While it runs, the status shows only the progress.
    res = run(ctx, "unraid.status")["result"]
    assert res["plan"] is None and res["job"]["state"] == "running"


def test_dismiss(ctx):
    assert run(ctx, "unraid.dismiss", {"dismissed": True})["ok"]
    assert run(ctx, "unraid.status")["result"]["dismissed"] is True
    run(ctx, "unraid.dismiss", {"dismissed": False})
    assert run(ctx, "unraid.status")["result"]["dismissed"] is False


def test_cache_layout_and_appdata_moves(ctx):
    mnt = ctx.pool.parent
    (mnt / "cache/appdata/Plex-Media-Server").mkdir(parents=True)
    (mnt / "cache/appdata/Plex-Media-Server/Preferences.xml").write_text("x")
    (mnt / "cache/system").mkdir()
    (mnt / "cache/shares/system").mkdir(parents=True)  # already there: left alone
    assert unraid.cache_layout(ctx) == ["appdata"]
    assert (mnt / "cache/system").is_dir()
    (mnt / "disk1/appdata/Plex-Media-Server").mkdir(parents=True)
    moves = [{"from": "Plex-Media-Server", "to": "plex-media-server"}]
    assert unraid.move_appdata(ctx, "plex", moves) == 2
    assert (mnt / "cache/shares/appdata/plex/plex-media-server/Preferences.xml").is_file()
    assert (mnt / "disk1/appdata/plex/plex-media-server").is_dir()
    assert unraid.move_appdata(ctx, "plex", moves) == 0


def test_run_import(ctx, monkeypatch):
    monkeypatch.setattr(vpn, "apply", lambda c, cfg, restart: None)
    ctx.runner.answers[("getent", "group", "smb")] = (0, "smb:x:998:anna\n", "")
    unraid.write_file(unraid.request_file(ctx),
                      json.dumps({"parity": True, "tags": {"homeassistant": "2025.11.3"}}))
    (ctx.pool.parent / "cache").mkdir()
    assert unraid.run_import(ctx) == 0
    calls = ctx.runner.calls
    assert ["disk", "add", "1111-aaaa-bbbb", "--role", "data"] in calls
    assert ["disk", "add", "2222-cccc-dddd", "--role", "cache"] in calls
    assert ["disk", "format", "/dev/sdb", "--fs", "xfs", "--json"] in calls
    assert ["disk", "add", "3333-eeee", "--role", "parity"] in calls
    installs = [c for c in calls if c[:2] == ["/usr/sbin/apps", "install"]]
    assert [c[2] for c in installs] == ["plex-media-server", "qbittorrent", "homeassistant"]
    assert ["systemctl", "start", "--no-block", "snapraid-sync.service"] in calls
    shares = json.loads((ctx.etc / "dahlia/shares.json").read_text())["shares"]
    assert {s["name"]: s["users"] for s in shares} == {"Filme": {}, "Fotos": {"anna": "rw"}}
    cfg = vpn.load(ctx)
    assert cfg["enabled"] and [p["address"] for p in cfg["peers"]] == ["10.253.0.2/32",
                                                                       "10.253.0.3/32"]
    assert vpn.key_file(ctx).read_text().strip() == SERVER_KEY
    conf = vpn.render_conf(cfg, SERVER_KEY)
    assert conf.count("PresharedKey") == 1
    job = json.loads(unraid.progress_file(ctx).read_text())
    assert job["state"] == "done" and job["step"] == "finished"
    assert {"kind": "container", "name": "qbittorrent", "state": "done"} in job["items"]
    # Again: everything is there already, nothing is installed twice.
    ctx.runner.answers[("apps", "list")] = (0, json.dumps(
        [{"id": i, "status": "running"} for i in ("plex-media-server", "qbittorrent",
                                                 "homeassistant")]), "")
    plan = unraid.build_plan(ctx, SUMMARY, {})
    assert {c["state"] for c in plan["containers"]} == {"done"}
    assert plan["wireguard"]["state"] == "done"


def test_import_users_with_hash(ctx):
    ctx.runner.answers[("getent", "group", "smb")] = (0, "smb:x:998:\n", "")
    plan = unraid.build_plan(ctx, SUMMARY, {})
    assert items(plan, "users")["anna"]["state"] == "ready"
    unraid.add_users(ctx, ["anna"])
    pdb = [c for c in ctx.runner.calls if c[0] == "pdbedit"]
    assert pdb and pdb[0][:2] == ["pdbedit", "-i"] and pdb[0][-2:] == ["-e", "tdbsam"]
    assert not (ctx.run_dir / "unraid-smbpasswd").exists()
    assert ["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent", "--shell",
            "/usr/sbin/nologin", "--groups", "dahlia,smb", "anna"] in ctx.runner.calls
