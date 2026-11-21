"""Unraid migration helper (migrate/): Unraid reader, disk locks, settings,
web API with a fake install script."""

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "migrate"))

from helper import disks, job, net, unraid, web  # noqa: E402

DISKS_INI = '''["parity"]
idx="0"
name="parity"
device="sdb"
id="WDC_WD40EFRX_WD-1"
type="Parity"
fsType=""
["disk1"]
name="disk1"
device="sdc"
id="WDC_WD40EFRX_WD-2"
type="Data"
fsType="xfs"
["disk2"]
name="disk2"
device=""
type="Data"
["cache"]
name="cache"
device="nvme0n1"
id="Samsung_SSD_970"
type="Cache"
fsType="btrfs"
["flash"]
name="flash"
device="sda"
type="Flash"
fsType="vfat"
'''

TEMPLATE = '''<?xml version="1.0"?>
<Container version="2">
  <Name>jellyfin</Name>
  <Repository>jellyfin/jellyfin:10.10.3</Repository>
  <Network>bridge</Network>
  <WebUI>http://[IP]:[PORT:8096]/</WebUI>
  <Config Name="Web" Target="8096" Default="8096" Mode="tcp" Type="Port">8097</Config>
  <Config Name="Disc" Target="1900" Default="1900" Mode="udp" Type="Port">1900</Config>
  <Config Name="Config" Target="/config" Mode="rw" Type="Path">/mnt/user/appdata/jellyfin</Config>
  <Config Name="Media" Target="/media" Default="" Mode="ro" Type="Path">/mnt/user/media</Config>
  <Config Name="TZ" Target="TZ" Default="UTC" Mode="" Type="Variable">Europe/Berlin</Config>
  <Config Name="GPU" Target="/dev/dri" Default="" Mode="" Type="Device">/dev/dri</Config>
</Container>
'''


@pytest.fixture
def unraid_tree(tmp_path):
    cfg = tmp_path / "config"
    (cfg / "shares").mkdir(parents=True)
    (cfg / "plugins/dockerMan/templates-user").mkdir(parents=True)
    (cfg / "wireguard").mkdir()
    (cfg / "plugins/dynamix.vm.manager").mkdir()
    (cfg / "ident.cfg").write_text('NAME="Tower"\ntimeZone="Europe/Berlin"\n')
    (cfg / "network.cfg").write_text('USE_DHCP[0]="no"\nIPADDR[0]="192.168.1.10"\n'
                                     'NETMASK[0]="255.255.255.0"\nGATEWAY[0]="192.168.1.1"\n'
                                     'DNS_SERVER1="192.168.1.1"\nDNS_SERVER2="9.9.9.9"\n')
    (cfg / "docker.cfg").write_text('DOCKER_APP_CONFIG_PATH="/mnt/cache/appdata/"\n')
    (cfg / "shares/media.cfg").write_text('shareComment="Filme"\nshareExport="e"\n'
                                          'shareSecurity="private"\nshareUseCache="prefer"\n'
                                          'shareReadList="tom"\nshareWriteList="anna,bad name"\n')
    (cfg / "shares/backup.cfg").write_text('shareExport="-"\nshareSecurity="public"\n')
    (cfg / "passwd").write_text("root:x:0:0::/root:/bin/bash\nanna:x:1000:100::/:/bin/false\n"
                                "tom:x:1001:100::/:/bin/false\n")
    (cfg / "smbpasswd").write_text(
        "anna:1000:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX:8846F7EAEE8FB117AD06BDD830B7586C:[U ]:LCT-0:\n")
    (cfg / "plugins/dockerMan/templates-user/my-jellyfin.xml").write_text(TEMPLATE)
    (cfg / "plugins/dockerMan/templates-user/my-evil.xml").write_text(
        '<!DOCTYPE x [<!ENTITY a "b">]><Container><Name>x</Name><Repository>y</Repository>'
        "</Container>")
    (cfg / "wireguard/wg0.conf").write_text("[Interface]\n")
    em = tmp_path / "emhttp"
    em.mkdir()
    (em / "disks.ini").write_text(DISKS_INI)
    return cfg, em


def test_reads_unraid_settings(unraid_tree):
    cfg, em = unraid_tree
    u = unraid.read(cfg, em)
    assert u["found"]
    assert u["identity"] == {"hostname": "tower", "timezone": "Europe/Berlin"}
    assert u["network"] == {"mode": "static", "address": "192.168.1.10/24",
                            "gateway": "192.168.1.1", "dns": ["192.168.1.1", "9.9.9.9"]}
    assert [(d["role"], d["device"], d["slot"]) for d in u["disks"]] == [
        ("flash", "sda", "flash"), ("parity", "sdb", "parity"), ("data", "sdc", "disk1"),
        ("cache", "nvme0n1", "cache")]
    assert u["disks"][3]["pool"] == "cache"
    media = next(s for s in u["shares"] if s["name"] == "media")
    assert media == {"name": "media", "comment": "Filme", "smb": True, "security": "private",
                     "cache": "prefer", "read": ["tom"], "write": ["anna"]}
    assert not next(s for s in u["shares"] if s["name"] == "backup")["smb"]
    assert u["users"] == [{"name": "anna", "smb_password": True},
                          {"name": "tom", "smb_password": False}]
    assert [c["name"] for c in u["containers"]] == ["jellyfin"]  # DTD template rejected
    c = u["containers"][0]
    assert c["ports"] == [{"host": 8097, "container": 8096, "protocol": "tcp"},
                          {"host": 1900, "container": 1900, "protocol": "udp"}]
    assert c["paths"][1] == {"host": "/mnt/user/media", "container": "/media", "readonly": True}
    assert c["env"] == {"TZ": "Europe/Berlin"} and c["devices"] == ["/dev/dri"]
    assert u["appdata"] == "/mnt/cache/appdata/"
    assert u["wireguard"] == ["wg0.conf"] and u["vms"]


def test_missing_unraid_and_bad_network(tmp_path):
    u = unraid.read(tmp_path / "nothing", tmp_path / "nothing")
    assert not u["found"] and u["network"] == {"mode": "dhcp"} and u["disks"] == []
    assert unraid.network({"USE_DHCP": "no", "IPADDR": "x"}) == {"mode": "dhcp"}
    assert unraid.identity({"NAME": "Bad Name!", "timeZone": "../etc"}) == \
        {"hostname": "", "timezone": ""}


def lsblk(*devs):
    return lambda cmd: json.dumps({"blockdevices": list(devs)})


def dev(name, size, **kw):
    return {"name": name, "path": f"/dev/{name}", "type": "disk", "size": size, "model": "M",
            "serial": kw.pop("serial", "S" + name), "tran": kw.pop("tran", "sata"),
            "rota": kw.pop("rota", False), "rm": False, "ro": False, "fstype": None,
            "pttype": kw.pop("pttype", None), "children": kw.pop("children", None), **kw}


def test_disk_locks(unraid_tree):
    cfg, em = unraid_tree
    used = unraid.read(cfg, em)["disks"]
    runner = lsblk(
        dev("sda", 32 * 10**9, tran="usb", rm=True),           # Unraid flash
        dev("sdb", 4 * 10**12, rota=True),                     # parity
        dev("sdc", 4 * 10**12, rota=True, children=[{"name": "sdc1", "fstype": "xfs"}]),
        dev("sdd", 500 * 10**9),                               # free, empty
        dev("sde", 256 * 10**9, pttype="gpt", children=[{"name": "sde1", "fstype": "ntfs"}]),
        dev("sdf", 8 * 10**9),                                 # too small
        dev("sdg", 64 * 10**9, tran="usb", rm=True),           # some USB stick
        {"name": "loop0", "type": "loop", "size": 30 * 10**9},
        {"name": "sr0", "type": "rom", "size": 1},
    )
    found = {d["name"]: d for d in disks.scan(used, runner)}
    assert set(found) == {"sda", "sdb", "sdc", "sdd", "sde", "sdf", "sdg"}
    assert found["sda"]["lock"] == "unraid_flash"
    assert found["sdb"]["lock"] == "unraid_parity" and not found["sdb"]["ssd"]
    assert found["sdc"]["lock"] == "unraid_data" and found["sdc"]["unraid_slot"] == "disk1"
    assert found["sdd"]["lock"] == "" and not found["sdd"]["has_data"]
    assert found["sde"]["lock"] == "" and found["sde"]["filesystems"] == ["ntfs"]
    assert found["sdf"]["lock"] == "too_small" and found["sdg"]["lock"] == "removable"
    assert disks.confirm_ok(found["sdd"], "")
    assert not disks.confirm_ok(found["sde"], "wrong")
    assert disks.confirm_ok(found["sde"], " ssde ")
    assert "loop0" in {d["name"] for d in disks.scan(used, runner, allow_loop=True)}


def test_predicted_interface_name(tmp_path):
    out = "ID_NET_NAMING_SCHEME=v257\nID_NET_NAME_MAC=enx001122334455\nID_NET_NAME_PATH=enp3s0\n"
    assert net.predicted_name(out) == "enp3s0"
    assert net.predicted_name(out + "ID_NET_NAME_ONBOARD=eno1\n") == "eno1"
    assert net.predicted_name("") == "lan0"
    sysnet = tmp_path / "net"
    for name, carrier in (("br0", "1"), ("eth1", "0"), ("eth0", "1"), ("veth9", "1")):
        (sysnet / name).mkdir(parents=True)
        (sysnet / name / "carrier").write_text(carrier)
    (sysnet / "eth0" / "device").mkdir()
    (sysnet / "eth1" / "device").mkdir()
    assert net.physical(sysnet) == ["eth0", "eth1"]
    assert net.main_interface(sysnet) == "eth0"


def test_validate_settings(monkeypatch):
    good = {"hostname": "NAS", "account": "fabi", "password": "x" * 10,
            "timezone": "Europe/Berlin", "keymap": "de"}
    assert job.validate(good)["hostname"] == "nas"
    for key, value, error in (("hostname", "bad_name", "error.hostname"),
                              ("account", "root", "error.account"),
                              ("account", "dahlia-admin", "error.account"),
                              ("password", "short", "error.password"),
                              ("password", "line\nbreak12345", "error.password"),
                              ("timezone", "Mars/Base", "error.timezone"),
                              ("keymap", "xx", "error.keymap")):
        with pytest.raises(job.InvalidSettings, match=error):
            job.validate({**good, key: value})
    assert re.fullmatch(r"[2-9A-HJKMNP-Z]{5}-[2-9A-HJKMNP-Z]{5}", job.setup_code())


def test_settings_file_is_safe_shell(tmp_path):
    disk = {"path": "/dev/sdd", "size": 500, "serial": "S-1"}
    s = {"hostname": "nas", "account": "fabi", "password": "pa'ss $(x)",
         "timezone": "Europe/Berlin", "keymap": "de"}
    nic = {"name": "enp3s0", "mac": "00:11:22:33:44:55"}
    netcfg = {"mode": "static", "address": "192.168.1.10/24", "gateway": "192.168.1.1",
              "dns": ["192.168.1.1"]}
    f = job.settings_file(disk, s, nic, netcfg, "ABCDE-FGH23", tmp_path, None, Path("/k.asc"))
    text = f.read_text()
    assert "pa'ss" not in text and "NET_DNS='192.168.1.1'" in text
    assert (tmp_path / "password").read_text() == "pa'ss $(x)"
    assert (tmp_path / "password").stat().st_mode & 0o777 == 0o600
    with pytest.raises(job.InvalidSettings):
        job.settings_file({**disk, "serial": "x'; rm -rf /"}, s, nic, netcfg, "ABCDE-FGH23",
                          tmp_path, None, Path("/k.asc"))


def test_stage_copies_raw_files(unraid_tree, tmp_path):
    cfg, em = unraid_tree
    u = unraid.read(cfg, em)
    out = tmp_path / "stage"
    job.stage_unraid(cfg, u, out)
    assert json.loads((out / "unraid.json").read_text())["identity"]["hostname"] == "tower"
    assert (out / "config/plugins/dockerMan/templates-user/my-jellyfin.xml").is_file()
    assert (out / "config/smbpasswd").is_file() and (out / "config/wireguard/wg0.conf").is_file()


FAKE_INSTALL = """#!/bin/bash
set -eu
. "$1"
echo "@@step check"
echo "disk $DISK host $HOSTNAME_NEW code $SETUP_CODE"
test -d "$UNRAID_DIR/config"
for s in partition format debootstrap system dahlia unraid boot finished; do echo "@@step $s"; done
"""


@pytest.fixture
def server(unraid_tree, tmp_path):
    cfg, em = unraid_tree
    script = tmp_path / "install.sh"
    script.write_text(FAKE_INSTALL)
    runner = lsblk(dev("sdb", 4 * 10**12), dev("sdd", 500 * 10**9),
                   dev("sde", 256 * 10**9, children=[{"name": "sde1", "fstype": "ext4"}]))
    helper = web.Helper(cfg, em, ROOT / "migrate/www", script, Path("/k.asc"), "ABCDE-FGH23",
                        runner=runner, nic={"current": "eth0", "name": "enp3s0",
                                            "mac": "00:11:22:33:44:55"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.handler(helper))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", helper
    httpd.shutdown()


def call(base, path, body=None, code="ABCDE-FGH23"):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(base + path, data=data,  # noqa: S310 - local test server
                                 headers={"X-Access-Code": code,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_web_flow(server):
    base, helper = server
    assert call(base, "/api/state", code="WRONG")[0] == 401
    with urllib.request.urlopen(base + "/", timeout=10) as r:  # noqa: S310
        assert b"app.js" in r.read()
    assert call(base, "/../job.py")[0] == 404
    status, state = call(base, "/api/state")
    assert status == 200 and state["unraid"]["containers"] == ["jellyfin"]
    assert {d["name"]: d["lock"] for d in state["disks"]} == \
        {"sdb": "unraid_parity", "sdd": "", "sde": ""}
    form = {"hostname": "nas", "account": "fabi", "password": "correct horse",
            "timezone": "Europe/Berlin", "keymap": "de", "network": "unraid"}
    assert call(base, "/api/install", {**form, "disk": "/dev/sdb"})[1]["error"] == \
        "error.disk_locked"
    assert call(base, "/api/install", {**form, "disk": "/dev/sde"})[1]["error"] == "error.confirm"
    status, out = call(base, "/api/install", {**form, "disk": "/dev/sdd"})
    assert status == 200 and re.fullmatch(r"[2-9A-HJKMNP-Z]{5}-[2-9A-HJKMNP-Z]{5}",
                                          out["setup_code"])
    for _ in range(50):
        state = call(base, "/api/state")[1]
        if state["job"]["state"] != "running":
            break
        time.sleep(0.1)
    assert state["job"]["state"] == "done", state["job"]
    assert state["setup_code"] == out["setup_code"]
    assert f"disk /dev/sdd host nas code {out['setup_code']}" in state["job"]["log"]


def test_locales_complete():
    www = ROOT / "migrate/www"
    de = json.loads((www / "locales/de.json").read_text(encoding="utf-8"))
    en = json.loads((www / "locales/en.json").read_text(encoding="utf-8"))
    assert set(de) == set(en)
    html = (www / "index.html").read_text()
    js = (www / "app.js").read_text()
    used = set(re.findall(r'data-i18n="([^"]+)"', html))
    used |= set(re.findall(r"(?<![A-Za-z])t\(\"([a-z_.]+)\"", js))
    used |= {f"step.{s}" for s in job.STEPS if s != "finished"}
    used |= {f"job.{s}" for s in ("running", "done", "failed")}
    used |= {f"lock.{k}" for k in ("unraid_data", "unraid_parity", "unraid_cache", "unraid_flash",
                                   "too_small", "readonly", "removable")}
    errors = set(re.findall(r'"(error\.[a-z_]+)"', (ROOT / "migrate/helper/job.py").read_text()
                            + (ROOT / "migrate/helper/web.py").read_text()))
    assert not (used | errors) - set(de), (used | errors) - set(de)
