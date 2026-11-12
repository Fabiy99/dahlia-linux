"""Installer shell scripts (iso/installer), run with the system /bin/sh.

debian-installer runs them with busybox ash; dash is the closest POSIX shell
available on the test machines.
"""

import re
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parent.parent / "iso/installer"
LIB = INSTALLER / "lib.sh"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq4cJ0q0m9oJmZ8VXQ2m1n3q0z1cX8pQvYb2dY3sJ7k test@laptop"


def lib(func: str, *args: str) -> bool:
    proc = subprocess.run(["sh", "-c", f'. "{LIB}"; {func} "$@"', "sh", *args],
                          capture_output=True, text=True)
    return proc.returncode == 0


def lib_out(func: str, *args: str) -> str:
    return subprocess.run(["sh", "-c", f'. "{LIB}"; {func} "$@"', "sh", *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("name", ["fabian", "fa", "anna_2", "ben-k", "x" * 32])
def test_account_ok(name):
    assert lib("valid_account", name)


@pytest.mark.parametrize("name", ["", "f", "x" * 33, "Fabian", "1abc", "-ab", "fa bi", "root",
                                  "dahlia", "dahlia-ui", "dahlia-admin2", "systemd-network",
                                  "docker", "sudo", "nobody"])
def test_account_bad(name):
    assert not lib("valid_account", name)


@pytest.mark.parametrize("name", ["dahlia", "nas-01", "a", "x" * 63])
def test_hostname_ok(name):
    assert lib("valid_hostname", name)


@pytest.mark.parametrize("name", ["", "Nas", "-a", "a-", "a.b", "a b", "x" * 64, "ä", "a_b"])
def test_hostname_bad(name):
    assert not lib("valid_hostname", name)


@pytest.mark.parametrize("ip", ["192.168.1.1", "10.0.0.0", "255.255.255.255", "1.0.0.0"])
def test_ipv4_ok(ip):
    assert lib("valid_ipv4", ip)


@pytest.mark.parametrize("ip", ["", "1.2.3", "1.2.3.4.5", "256.1.1.1", "1..2.3", ".1.2.3",
                                "1.2.3.", "01.2.3.4", "a.b.c.d", "1.2.3.4/24"])
def test_ipv4_bad(ip):
    assert not lib("valid_ipv4", ip)


@pytest.mark.parametrize("cidr", ["192.168.1.10/24", "10.0.0.5/8", "172.16.0.2/30"])
def test_cidr_ok(cidr):
    assert lib("valid_cidr", cidr)


@pytest.mark.parametrize("cidr", ["192.168.1.10", "192.168.1.10/", "192.168.1.10/7",
                                  "192.168.1.10/31", "192.168.1.10/024", "1.2.3/24", "/24"])
def test_cidr_bad(cidr):
    assert not lib("valid_cidr", cidr)


def test_ssh_key_ok():
    assert lib("valid_ssh_key", KEY)
    assert lib("valid_ssh_key", KEY.rsplit(" ", 1)[0])


@pytest.mark.parametrize("key", ["", "ssh-ed25519", "ssh-dss AAAAB3NzaC1kc3MAAACBAP" + "a" * 60,
                                 "ssh-ed25519 notbase64!" + "a" * 60, "ssh-ed25519 AAAA",
                                 KEY + "\nssh-ed25519 AAAA", "-----BEGIN OPENSSH PRIVATE KEY-----"])
def test_ssh_key_bad(key):
    assert not lib("valid_ssh_key", key)


@pytest.mark.parametrize(("size", "text"), [(500107862016, "500 GB"), (2000398934016, "2.0 TB"),
                                            (21474836480, "21 GB"), (536870912, "537 MB")])
def test_format_size(size, text):
    assert lib_out("format_size", str(size)) == text


# --- apply-answers.sh ---------------------------------------------------------

INTERFACES = """\
# This file describes the network interfaces available on your system
source /etc/network/interfaces.d/*

auto lo
iface lo inet loopback

# The primary network interface
allow-hotplug enp1s0
iface enp1s0 inet dhcp
"""


@pytest.fixture
def target(tmp_path):
    root = tmp_path / "target"
    for d in ("etc/network", "usr/share/zoneinfo/Europe", "home/fabi"):
        (root / d).mkdir(parents=True)
    (root / "etc/hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\tdahlia\n::1\tlocalhost\n")
    (root / "etc/network/interfaces").write_text(INTERFACES)
    (root / "etc/resolv.conf").write_text("nameserver 10.0.2.3\n")
    (root / "usr/share/zoneinfo/Europe/Vienna").write_text("TZif")
    return root


def apply(target, tmp_path, answers: str, key: str | None = None, account="fabi"):
    a = tmp_path / "answers"
    a.write_text((f"ACCOUNT={account}\n" if account is not None else "") + answers)
    args = ["sh", str(INSTALLER / "apply-answers.sh"), str(target), str(a)]
    if key is not None:
        k = tmp_path / "key"
        k.write_text(key)
        args.append(str(k))
    return subprocess.run(args, capture_output=True, text=True)


def test_apply_dhcp(target, tmp_path):
    answers = "DISK=/dev/vda\nHOSTNAME=nas\nTIMEZONE=Europe/Vienna\nNETWORK=dhcp\n"
    res = apply(target, tmp_path, answers)
    assert res.returncode == 0, res.stderr
    assert (target / "etc/hostname").read_text() == "nas\n"
    assert (target / "etc/hosts").read_text() == (
        "127.0.0.1\tlocalhost\n::1\tlocalhost\n127.0.1.1\tnas\n"
    )
    assert (target / "etc/timezone").read_text() == "Europe/Vienna\n"
    assert (target / "etc/localtime").readlink() == Path("/usr/share/zoneinfo/Europe/Vienna")
    assert (target / "etc/network/interfaces").read_text() == INTERFACES
    assert not (target / "home/fabi/.ssh").exists()


def test_apply_static_and_key(target, tmp_path):
    answers = ("HOSTNAME=nas\nTIMEZONE=Europe/Vienna\nNETWORK=static\n"
               "ADDRESS=192.168.1.10/24\nGATEWAY=192.168.1.1\nDNS=192.168.1.2\n")
    res = apply(target, tmp_path, answers, KEY + "\n")
    assert res.returncode == 0, res.stderr
    ifaces = (target / "etc/network/interfaces").read_text()
    assert "iface enp1s0 inet dhcp" not in ifaces
    assert ifaces.endswith(
        "allow-hotplug enp1s0\niface enp1s0 inet static\n"
        "    address 192.168.1.10/24\n    gateway 192.168.1.1\n"
    )
    assert "iface lo inet loopback" in ifaces
    assert (target / "etc/resolv.conf").read_text() == "nameserver 192.168.1.2\n"
    keys = target / "home/fabi/.ssh/authorized_keys"
    assert keys.read_text() == KEY + "\n"
    assert keys.stat().st_mode & 0o777 == 0o600
    assert keys.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(("answers", "message"), [
    ("HOSTNAME=Bad_Name\n", "host name"),
    ("HOSTNAME=nas\nTIMEZONE=Mars/Base\n", "time zone"),
    ("HOSTNAME=nas\nTIMEZONE=../../etc/passwd\n", "time zone"),
    ("HOSTNAME=nas\nNETWORK=static\nADDRESS=192.168.1.10\nGATEWAY=1.1.1.1\nDNS=1.1.1.1\n",
     "address"),
    ("HOSTNAME=nas\nNETWORK=static\nADDRESS=10.0.0.2/24\nGATEWAY=x\nDNS=1.1.1.1\n", "gateway"),
    ("HOSTNAME=nas\nNETWORK=bridge\n", "network mode"),
])
def test_apply_rejects(target, tmp_path, answers, message):
    res = apply(target, tmp_path, answers)
    assert res.returncode != 0
    assert message in res.stderr
    # Nothing half-applied for an invalid host name.
    if "host name" in message:
        assert not (target / "etc/hostname").exists()


@pytest.mark.parametrize("account", [None, "x", "Fabian", "dahlia-ui", "root", "docker", "a/b"])
def test_apply_rejects_account(target, tmp_path, account):
    res = apply(target, tmp_path, "HOSTNAME=nas\n", account=account)
    assert res.returncode != 0
    assert "account name" in res.stderr


def test_apply_rejects_bad_key(target, tmp_path):
    res = apply(target, tmp_path, "HOSTNAME=nas\n", "not a key\n")
    assert res.returncode != 0
    assert "SSH key" in res.stderr


def test_apply_static_without_dhcp_interface(target, tmp_path):
    (target / "etc/network/interfaces").write_text("auto lo\niface lo inet loopback\n")
    res = apply(target, tmp_path, "HOSTNAME=nas\nNETWORK=static\nADDRESS=10.0.0.2/24\n"
                                  "GATEWAY=10.0.0.1\nDNS=10.0.0.1\n")
    assert res.returncode != 0
    assert "no DHCP interface" in res.stderr


# --- templates ----------------------------------------------------------------


def templates() -> dict[str, dict[str, str]]:
    out = {}
    for block in (INSTALLER / "dahlia.templates").read_text().split("\n\n"):
        fields, key = {}, None
        for line in block.splitlines():
            if line.startswith(" "):
                fields[key] += "\n" + line
            else:
                key, _, value = line.partition(": ")
                fields[key] = value
        out[fields["Template"]] = fields
    return out


def test_every_question_in_guided_sh_has_a_template():
    script = (INSTALLER / "guided.sh").read_text()
    used = set(re.findall(r"dahlia/[a-z_]+", script))
    assert used <= set(templates())


def test_timezone_choices_are_real_zones():
    zones = templates()["dahlia/timezone"]["Choices"].split(", ")
    assert "Europe/Berlin" in zones
    zoneinfo = Path("/usr/share/zoneinfo")
    if zoneinfo.is_dir():
        assert all((zoneinfo / z).is_file() for z in zones)


def test_network_choices_match():
    t = templates()["dahlia/network"]
    assert len(t["Choices-C"].split(", ")) == len(t["Choices"].split(", "))
