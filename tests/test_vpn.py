"""vpn.*: WireGuard server and Tailscale."""

import json
import subprocess

import pytest
from dahlia_agent import actions, firewall, protocol, vpn
from test_agent import INACTIVE, RUNNING, FakeRunner, err_code, run

PRIV = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
PUB = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="
PSK = "FpCyhws9cxwWoV4xELtfJvjJN+zQVRPISllRWgeorVU="
ADDRS = json.dumps([
    {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8,
                                    "scope": "host"}]},
    {"ifname": "enp1s0", "addr_info": [
        {"family": "inet", "local": "192.168.1.5", "prefixlen": 24, "scope": "global"},
        {"family": "inet6", "local": "2001:db8::5", "prefixlen": 64, "scope": "global"}]},
])


class Runner(FakeRunner):
    def run(self, cmd, **kw):
        if cmd[:2] == ["wg", "pubkey"]:
            self.calls.append(cmd)
            self.inputs.append(kw.get("input"))
            return subprocess.CompletedProcess(cmd, 0, PUB + "\n", "")
        return super().run(cmd, **kw)


@pytest.fixture
def ctx(tmp_path):
    runner = Runner({
        ("systemctl", "show"): (0, INACTIVE, ""),
        ("wg", "genkey"): (0, PRIV + "\n", ""),
        ("wg", "genpsk"): (0, PSK + "\n", ""),
        ("ip", "-j", "addr"): (0, ADDRS, ""),
        ("qrencode",): (0, "<svg>qr</svg>", ""),
    })
    return actions.Context(runner=runner, etc=tmp_path / "etc", run_dir=tmp_path / "run",
                           var=tmp_path / "var", clock=lambda: 1_794_000_000)


ON = {"enabled": True, "port": 51820, "endpoint": "home.example.org", "route": "lan"}


def test_status_defaults(ctx, monkeypatch, tmp_path):
    monkeypatch.setattr(vpn, "TAILSCALE", tmp_path / "no-tailscale")
    res = run(ctx, "vpn.status")["result"]
    wg = res["wireguard"]
    assert wg["enabled"] is False and wg["port"] == 51820 and wg["peers"] == []
    assert wg["public_key"] is None and wg["subnet"] == "10.253.0.0/24"
    assert res["tailscale"]["installed"] is False and res["tailscale"]["state"] is None
    assert ["wg", "genkey"] not in ctx.runner.calls


def test_enable_writes_conf_firewall_and_starts(ctx):
    res = run(ctx, "vpn.wireguard_set", ON)["result"]
    assert res["enabled"] and res["public_key"] == PUB
    conf = vpn.conf_file(ctx)
    assert conf.stat().st_mode & 0o777 == 0o600
    text = conf.read_text()
    assert f"PrivateKey = {PRIV}" in text and "Address = 10.253.0.1/24" in text
    assert "ListenPort = 51820" in text
    assert "PostUp = /usr/lib/dahlia/wireguard-forward.sh up %i" in text
    assert vpn.key_file(ctx).read_text().strip() == PRIV
    assert vpn.config_file(ctx).stat().st_mode & 0o777 == 0o600
    assert "net.ipv4.ip_forward = 1" in (ctx.etc / "sysctl.d/60-dahlia-forward.conf").read_text()
    assert ["systemctl", "enable", "wg-quick@dahlia-wg.service"] in ctx.runner.calls
    assert ["systemctl", "restart", "wg-quick@dahlia-wg.service"] in ctx.runner.calls
    rules = (ctx.run_dir / "firewall.nft").read_text()
    assert "udp dport 51820 accept" in rules
    assert 'ip saddr 10.253.0.0/24 oifname != "dahlia-wg" masquerade' in rules
    ctx.runner.calls.clear()
    run(ctx, "vpn.wireguard_set", {**ON, "enabled": False})
    assert ["systemctl", "disable", "--now", "wg-quick@dahlia-wg.service"] in ctx.runner.calls
    assert "51820" not in (ctx.run_dir / "firewall.nft").read_text()


def test_add_and_remove_peer(ctx):
    assert err_code(run(ctx, "vpn.wireguard_add_peer", {"name": "Handy"})) == protocol.FAILED
    run(ctx, "vpn.wireguard_set", {**ON, "endpoint": "", "dns": "192.168.1.2"})
    reply = run(ctx, "vpn.wireguard_add_peer", {"name": "Handy"})
    assert "address clients connect to" in reply["error"]["message"]
    run(ctx, "vpn.wireguard_set", {**ON, "dns": "192.168.1.2"})
    ctx.runner.answers[("systemctl", "show")] = (0, RUNNING, "")
    ctx.runner.calls.clear()
    res = run(ctx, "vpn.wireguard_add_peer", {"name": "Handy"})["result"]
    assert res["address"] == "10.253.0.2/32" and res["qr_svg"] == "<svg>qr</svg>"
    client = res["config"]
    assert f"PrivateKey = {PRIV}" in client and "Address = 10.253.0.2/32" in client
    assert "DNS = 192.168.1.2" in client and f"PresharedKey = {PSK}" in client
    assert "Endpoint = home.example.org:51820" in client
    assert "AllowedIPs = 10.253.0.0/24, 192.168.1.0/24" in client
    # Peers change without restarting the tunnel of the others.
    assert ["systemctl", "restart", "wg-quick@dahlia-wg.service"] not in ctx.runner.calls
    sync = [c for c in ctx.runner.calls if c[:2] == ["wg", "syncconf"]]
    assert sync and not (ctx.run_dir / "dahlia-wg.sync").exists()
    saved = json.loads(vpn.config_file(ctx).read_text())["peers"][0]
    assert saved["public_key"] == PUB and "private" not in json.dumps(saved).lower()
    assert saved["created"] == "2026-11-06T21:20:00+00:00"
    assert "# Handy" in vpn.conf_file(ctx).read_text()
    assert err_code(run(ctx, "vpn.wireguard_add_peer", {"name": "Handy"})) == protocol.FAILED
    assert run(ctx, "vpn.wireguard_add_peer", {"name": "Laptop"})["result"]["address"] == (
        "10.253.0.3/32")
    run(ctx, "vpn.wireguard_set", {**ON, "route": "all"})
    res = run(ctx, "vpn.wireguard_add_peer", {"name": "Tablet"})["result"]
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in res["config"]

    assert run(ctx, "vpn.wireguard_remove_peer", {"name": "Handy"})["ok"]
    assert [p["name"] for p in vpn.load(ctx)["peers"]] == ["Laptop", "Tablet"]
    assert "# Handy" not in vpn.conf_file(ctx).read_text()
    assert err_code(run(ctx, "vpn.wireguard_remove_peer", {"name": "Handy"})) == protocol.FAILED
    reply = run(ctx, "vpn.wireguard_set", {**ON, "subnet": "10.254.0.0/24"})
    assert "remove all peers" in reply["error"]["message"]


def test_peer_stats_from_dump(ctx):
    run(ctx, "vpn.wireguard_set", ON)
    run(ctx, "vpn.wireguard_add_peer", {"name": "Handy"})
    ctx.runner.answers[("wg", "show")] = (0, f"{PRIV}\t{PUB}\t51820\toff\n"
                                          f"{PUB}\t{PSK}\t203.0.113.7:4500\t10.253.0.2/32\t"
                                          "1794000000\t1234\t5678\t25\n", "")
    peer = run(ctx, "vpn.status")["result"]["wireguard"]["peers"][0]
    assert peer["endpoint"] == "203.0.113.7:4500" and peer["handshake"] == 1794000000
    assert peer["rx"] == 1234 and peer["tx"] == 5678


@pytest.mark.parametrize("params", [
    {**ON, "port": 443},
    {**ON, "port": 80},
    {**ON, "endpoint": "home example"},
    {**ON, "endpoint": "a;b"},
    {**ON, "route": "some"},
    {**ON, "dns": "8.8.8"},
    {**ON, "dns": "999.1.1.1"},
    {**ON, "subnet": "8.8.8.0/24"},
    {**ON, "subnet": "192.168.1.0/24"},
])
def test_set_rejects(ctx, params):
    assert err_code(run(ctx, "vpn.wireguard_set", params)) == protocol.INVALID_PARAMS
    assert not vpn.conf_file(ctx).exists()


@pytest.mark.parametrize("name", ["", "a/b", "x" * 33, "-x", "a\nb"])
def test_peer_name_checked(ctx, name):
    assert err_code(run(ctx, "vpn.wireguard_add_peer", {"name": name})) == (
        protocol.INVALID_PARAMS)


def test_firewall_keeps_nat_when_disabled(tmp_path):
    (tmp_path / "dahlia").mkdir()
    (tmp_path / "dahlia/wireguard.json").write_text(json.dumps(
        {"enabled": True, "port": 51820, "subnet": "10.253.0.0/24"}))
    vpn_rules = firewall.wireguard(tmp_path)
    assert vpn_rules == {"port": 51820, "subnet": "10.253.0.0/24", "interface": "dahlia-wg"}
    off = firewall.disabled_ruleset(vpn_rules)
    assert "delete table inet dahlia" in off and "masquerade" in off and "dport" not in off
    (tmp_path / "dahlia/wireguard.json").write_text('{"enabled": true, "port": "x"}')
    assert firewall.wireguard(tmp_path) is None


def test_tailscale(ctx, monkeypatch, tmp_path):
    ts = tmp_path / "tailscale"
    monkeypatch.setattr(vpn, "TAILSCALE", ts)
    assert run(ctx, "vpn.tailscale_install")["ok"]
    cmd = ctx.runner.calls[-1]
    assert cmd[:3] == ["systemd-run", "--unit", "dahlia-tailscale-install.service"]
    assert cmd[-1] == "/usr/lib/dahlia/tailscale-install.sh"
    assert err_code(run(ctx, "vpn.tailscale_up", {"routes": False, "exit_node": False})) == (
        protocol.FAILED)
    ts.write_text("")
    assert err_code(run(ctx, "vpn.tailscale_install")) == protocol.FAILED

    ctx.runner.answers[(str(ts), "status")] = (0, json.dumps({
        "BackendState": "NeedsLogin", "AuthURL": "https://login.tailscale.com/a/abc123",
        "Self": {"HostName": "dahlia", "DNSName": "", "TailscaleIPs": []}}), "")
    st = run(ctx, "vpn.status")["result"]["tailscale"]
    assert st["installed"] and st["state"] == "NeedsLogin"
    assert st["auth_url"] == "https://login.tailscale.com/a/abc123"

    ctx.runner.calls.clear()
    assert run(ctx, "vpn.tailscale_up", {"routes": True, "exit_node": True})["ok"]
    up = ctx.runner.calls[-1]
    assert up[:3] == ["systemd-run", "--unit", "dahlia-tailscale-up.service"]
    assert up[up.index(str(ts)):] == [str(ts), "up", "--reset", "--timeout=600s",
                                      "--advertise-routes=192.168.1.0/24",
                                      "--advertise-exit-node"]
    assert (ctx.etc / "sysctl.d/60-dahlia-forward.conf").exists()

    ctx.runner.answers[(str(ts), "status")] = (0, json.dumps({
        "BackendState": "Running", "AuthURL": "",
        "Self": {"HostName": "dahlia", "DNSName": "dahlia.tail1234.ts.net.",
                 "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"]},
        "CurrentTailnet": {"Name": "fabian@example.org"}}), "")
    st = run(ctx, "vpn.status")["result"]["tailscale"]
    assert st["state"] == "Running" and st["auth_url"] is None
    assert st["dns_name"] == "dahlia.tail1234.ts.net" and st["routes"] and st["exit_node"]
    assert st["addresses"] == ["100.101.102.103", "fd7a:115c:a1e0::1"]

    assert run(ctx, "vpn.tailscale_logout")["ok"]
    assert ctx.runner.calls[-1] == [str(ts), "logout"]


def test_tailscale_auth_url_must_be_https(ctx, monkeypatch, tmp_path):
    ts = tmp_path / "tailscale"
    ts.write_text("")
    monkeypatch.setattr(vpn, "TAILSCALE", ts)
    ctx.runner.answers[(str(ts), "status")] = (0, json.dumps(
        {"BackendState": "NeedsLogin", "AuthURL": "javascript:alert(1)"}), "")
    assert run(ctx, "vpn.status")["result"]["tailscale"]["auth_url"] is None
