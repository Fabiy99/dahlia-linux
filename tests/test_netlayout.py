"""Network layout (bond, bridge, VLAN) and Wake-on-LAN in agent."""

import pytest
from dahlia_agent import actions, netlayout, network, protocol
from test_agent import FakeRunner, err_code, run

INSTALLED = """\
source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

# The primary network interface
allow-hotplug eno1
iface eno1 inet static
    address 192.168.1.20/24
    gateway 192.168.1.1
# This is an autoconfigured IPv6 interface
iface eno1 inet6 auto
"""

ETHTOOL = """Settings for eno1:
\tSupported ports: [ TP ]
\tSpeed: 1000Mb/s
\tSupports Wake-on: pumbg
\tWake-on: d
\tLink detected: yes
"""


@pytest.fixture
def ctx(tmp_path):
    c = actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", run_dir=tmp_path / "run",
                        sys=tmp_path / "sys")
    (c.etc / "network").mkdir(parents=True)
    (c.etc / "network/interfaces").write_text(INSTALLED)
    for name, mac, carrier in (("eno1", "aa:bb:cc:00:00:01", "1"),
                               ("eno2", "aa:bb:cc:00:00:02", "0")):
        d = c.sys / "class/net" / name
        (d / "device").mkdir(parents=True)
        (d / "address").write_text(mac + "\n")
        (d / "carrier").write_text(carrier + "\n")
        (d / "speed").write_text("1000\n" if carrier == "1" else "-1\n")
    (c.sys / "class/net/lo").mkdir(parents=True)
    wlan = c.sys / "class/net/wlp2s0"
    (wlan / "device").mkdir(parents=True)
    (wlan / "wireless").mkdir()
    c.runner.answers[("ethtool", "eno1")] = (0, ETHTOOL, "")
    c.runner.answers[("ethtool", "eno2")] = (0, "Settings for eno2:\n\tLink detected: no\n", "")
    return c


def staged(ctx):
    return (ctx.etc / "network/interfaces.dahlia-new").read_text()


def test_read_installed(ctx):
    out = run(ctx, "settings.network_layout")["result"]
    assert out["main"] == "eno1" and out["nics"] == ["eno1"]
    assert out["bond_mode"] == "" and out["bridge"] is False and out["vlans"] == []
    assert out["managed"] is False and out["pending"] is None
    assert [p["name"] for p in out["ports"]] == ["eno1", "eno2"]
    assert out["ports"][0] == {"name": "eno1", "mac": "aa:bb:cc:00:00:01", "link": True,
                               "speed": 1000, "wol_supported": True, "wol": False,
                               "wol_at_boot": False}
    assert out["ports"][1]["speed"] is None and out["ports"][1]["wol_supported"] is False


def test_bridge_with_vlan(ctx):
    res = run(ctx, "settings.set_network_layout", {
        "nics": ["eno1"], "bridge": True,
        "vlans": [{"id": 20, "mode": "static", "address": "10.0.20.5/24"},
                  {"id": 30, "mode": "manual"}]})["result"]
    assert res["rollback_seconds"] == network.ROLLBACK_SECONDS
    text = staged(ctx)
    assert text.startswith(netlayout.HEADER)
    assert "iface eno1 inet manual\n" in text
    assert ("auto br0\niface br0 inet static\n    address 192.168.1.20/24\n"
            "    gateway 192.168.1.1\n    bridge_ports eno1\n") in text
    assert "auto br0.20\niface br0.20 inet static\n    address 10.0.20.5/24\n" in text
    assert "auto br0.30\niface br0.30 inet manual\n" in text
    assert "\n\n\n" not in text
    # The live file is only replaced by network apply.
    assert (ctx.etc / "network/interfaces").read_text() == INSTALLED
    assert ctx.runner.calls[-1][-3:] == ["apply", "--iface", "eno1"]
    layout = netlayout.parse_layout(text)
    assert layout == {"main": "br0", "nics": ["eno1"], "bond_mode": "", "bridge": True,
                      "vlans": [{"id": 20, "mode": "static", "address": "10.0.20.5/24"},
                                {"id": 30, "mode": "manual", "address": ""}],
                      "managed": True}
    assert network.parse_interfaces(text)["address"] == "192.168.1.20/24"


def test_bond(ctx):
    run(ctx, "settings.set_network_layout", {"nics": ["eno1", "eno2"],
                                             "bond_mode": "active-backup"})
    text = staged(ctx)
    assert ("auto bond0\niface bond0 inet static\n    address 192.168.1.20/24\n"
            "    gateway 192.168.1.1\n    bond-slaves eno1 eno2\n    bond-mode active-backup\n"
            "    bond-miimon 100\n    bond-primary eno1\n") in text
    assert netlayout.parse_layout(text)["nics"] == ["eno1", "eno2"]
    assert netlayout.parse_layout(text)["bond_mode"] == "active-backup"


def test_bond_under_bridge(ctx):
    run(ctx, "settings.set_network_layout", {"nics": ["eno1", "eno2"], "bond_mode": "802.3ad",
                                             "bridge": True})
    text = staged(ctx)
    assert "auto bond0\niface bond0 inet manual\n    bond-slaves eno1 eno2\n" in text
    assert "bond-xmit-hash-policy layer3+4" in text
    assert "    bridge_ports bond0\n" in text
    assert netlayout.parse_layout(text) == {
        "main": "br0", "nics": ["eno1", "eno2"], "bond_mode": "802.3ad", "bridge": True,
        "vlans": [], "managed": True}


def test_back_to_plain_dhcp(ctx):
    (ctx.etc / "network/interfaces").write_text(netlayout.render_layout(
        ["eno1"], "", True, [], "dhcp", "", ""))
    run(ctx, "settings.set_network_layout", {"nics": ["eno1"]})
    text = staged(ctx)
    assert "auto eno1\niface eno1 inet dhcp\niface eno1 inet6 auto\n" in text
    assert "br0" not in text
    assert ctx.runner.calls[-1][-1] == "br0"  # rollback target: the old main interface


@pytest.mark.parametrize("params", [
    {"nics": []},
    {"nics": ["wlp2s0"]},
    {"nics": ["eno1", "eno1"], "bond_mode": "802.3ad"},
    {"nics": ["eno1", "eno2"]},
    {"nics": ["eno1"], "bond_mode": "active-backup"},
    {"nics": ["eno1"], "bond_mode": "broadcast"},
    {"nics": ["eno1"], "vlans": [{"id": 0, "mode": "manual"}]},
    {"nics": ["eno1"], "vlans": [{"id": 5, "mode": "dhcp"}]},
    {"nics": ["eno1"], "vlans": [{"id": 5, "mode": "manual"}, {"id": 5, "mode": "manual"}]},
    {"nics": ["eno1"], "vlans": [{"id": 5, "mode": "static", "address": "192.168.1.30/24"}]},
    {"nics": ["eno1"], "vlans": [{"id": 5, "mode": "static", "address": "10.0.5.0/24"}]},
    {"nics": ["eno1"], "vlans": [{"id": 5, "mode": "static", "address": "x\nstop"}]},
])
def test_rejects(ctx, params):
    assert err_code(run(ctx, "settings.set_network_layout", params)) == protocol.INVALID_PARAMS
    assert not (ctx.etc / "network/interfaces.dahlia-new").exists()


def test_busy(ctx):
    ctx.run_dir.mkdir(parents=True)
    (ctx.run_dir / "network-pending").write_text("9999999999")
    assert err_code(run(ctx, "settings.set_network_layout", {"nics": ["eno1"]})) \
        == protocol.BUSY


def test_swap_in_handles_layout_change(ctx, tmp_path):
    """Plain eno1 -> br0 with a VLAN: old interfaces down, new ones up."""
    paths = network.Paths.of(ctx)
    new = netlayout.render_layout(["eno1"], "", True, [{"id": 20, "mode": "manual"}],
                                  "dhcp", "", "")
    paths.new(paths.interfaces).write_text(new)
    runner = FakeRunner()
    network.swap_in(paths, runner, "eno1", [(paths.new(paths.interfaces), paths.interfaces)])
    assert runner.calls == [["ifdown", "--force", "eno1"], ["ip", "addr", "flush", "dev", "eno1"],
                            ["ifup", "br0"], ["ifup", "br0.20"]]
    # and back: VLAN and bridge down first
    paths.backup(paths.interfaces).write_text(INSTALLED)
    runner = FakeRunner()
    network.swap_in(paths, runner, "br0", [(paths.backup(paths.interfaces), paths.interfaces)])
    assert runner.calls[:2] == [["ifdown", "--force", "br0.20"], ["ifdown", "--force", "br0"]]
    assert runner.calls[-1] == ["ifup", "eno1"]


def test_wol(ctx):
    out = run(ctx, "settings.set_wol", {"port": "eno1", "enabled": True})["result"]
    assert out["name"] == "eno1"
    assert ["ethtool", "-s", "eno1", "wol", "g"] in ctx.runner.calls
    assert ["systemctl", "enable", "wol@eno1.service"] in ctx.runner.calls
    run(ctx, "settings.set_wol", {"port": "eno1", "enabled": False})
    assert ["systemctl", "disable", "wol@eno1.service"] in ctx.runner.calls


def test_wol_rejects(ctx):
    assert err_code(run(ctx, "settings.set_wol", {"port": "eno2", "enabled": True})) \
        == protocol.FAILED
    assert err_code(run(ctx, "settings.set_wol", {"port": "eth9;reboot", "enabled": True})) \
        == protocol.INVALID_PARAMS
    assert not any(c[:2] == ["ethtool", "-s"] for c in ctx.runner.calls)


def test_address_change_keeps_bridge_options():
    text = netlayout.render_layout(["eno1"], "", True, [], "dhcp", "", "")
    out = network.render_interfaces(text, "br0", "static", "10.0.0.5/24", "10.0.0.1")
    assert ("iface br0 inet static\n    address 10.0.0.5/24\n    gateway 10.0.0.1\n"
            "    bridge_ports eno1\n") in out
    back = network.render_interfaces(out, "br0", "dhcp", "", "")
    assert back == text
