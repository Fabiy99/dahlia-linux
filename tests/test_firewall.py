"""firewall: settings, open ports, the nftables ruleset and the agent actions.

The tests never call the real nft or ip; Firewall gets a fake run. Only
test_ruleset_parses runs `nft -c` (check only, nothing is loaded), as root.
"""

import json
import os
import shutil
import subprocess

import pytest
from dahlia_agent import actions, firewall, protocol
from dahlia_agent.firewall import Firewall, FirewallError, Port
from test_agent import FakeRunner, err_code, run

IP_ADDR = json.dumps([
    {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8,
                                    "scope": "host"}]},
    {"ifname": "enp1s0", "addr_info": [
        {"family": "inet", "local": "192.168.178.20", "prefixlen": 24, "scope": "global"},
        {"family": "inet6", "local": "2a02:810d:1:2::20", "prefixlen": 64, "scope": "global"},
        {"family": "inet6", "local": "fe80::1", "prefixlen": 64, "scope": "link"},
    ]},
    {"ifname": "wg0", "addr_info": [{"family": "inet", "local": "10.8.0.2", "prefixlen": 32,
                                     "scope": "global"}]},
])

WHOAMI = """\
schema: 1
id: whoami
name: Whoami
description: {de: Test, en: Test}
image: traefik/whoami:v1.11.0
web: {port: 80, public_port: 8099}
lan_ports:
  - {container: 7359, host: 7359, protocol: udp}
"""


class FakeRun:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def __call__(self, cmd):
        self.calls.append(cmd)
        if self.fail and cmd[: len(self.fail)] == self.fail:
            raise FirewallError("nft: syntax error")
        return IP_ADDR if cmd[0] == "ip" else ""


@pytest.fixture
def fw(tmp_path):
    return Firewall(etc=tmp_path / "etc", run_dir=tmp_path / "run",
                    apps_dir=tmp_path / "apps", run=FakeRun())


def install_app(apps_dir, text=WHOAMI, app_id="whoami"):
    d = apps_dir / app_id
    d.mkdir(parents=True)
    (d / "app.yaml").write_text(text)
    (d / "state.json").write_text("{}")


def share(etc):
    (etc / "dahlia").mkdir(parents=True, exist_ok=True)
    (etc / "dahlia/shares.json").write_text(json.dumps({"shares": [{"name": "media"}]}))


# --- settings -----------------------------------------------------------------


def test_check_networks_normalizes():
    assert firewall.check_networks([" 192.168.10.7/24", "2a02:810d::/48", "192.168.10.0/24"]) \
        == ["192.168.10.0/24", "2a02:810d::/48"]


@pytest.mark.parametrize("bad", [["nonsense"], ["0.0.0.0/0"], ["::/0"], "10.0.0.0/8",
                                 [f"10.{i}.0.0/16" for i in range(21)]])
def test_check_networks_rejects(bad):
    with pytest.raises(FirewallError):
        firewall.check_networks(bad)


def test_load_settings_defaults(tmp_path):
    empty = {"enabled": True, "networks": [], "rules": []}
    assert firewall.load_settings(tmp_path / "missing.json") == empty
    p = tmp_path / "firewall.json"
    p.write_text("[1, 2]")
    assert firewall.load_settings(p) == empty
    p.write_text(json.dumps({"enabled": False, "networks": ["bad"], "rules": [{"action": "x"}]}))
    assert firewall.load_settings(p) == {**empty, "enabled": False}


# --- what is open ---------------------------------------------------------------


def test_base_ports_only(fw):
    ports = firewall.open_ports(fw.etc, fw.apps_dir)
    assert {(p.port, p.protocol) for p in ports} == {(22, "tcp"), (80, "tcp"), (443, "tcp"),
                                                       (5353, "udp")}


def test_shares_and_apps_open_ports(fw):
    share(fw.etc)
    install_app(fw.apps_dir)
    # A broken app does not keep the others closed.
    (fw.apps_dir / "broken").mkdir()
    (fw.apps_dir / "broken/state.json").write_text("{}")
    (fw.apps_dir / "broken/app.yaml").write_text("id: [")
    ports = {(p.port, p.protocol): p.service for p in firewall.open_ports(fw.etc, fw.apps_dir)}
    assert ports[(445, "tcp")] == "smb" and ports[(3702, "udp")] == "wsdd"
    assert ports[(8099, "tcp")] == "app:whoami" and ports[(7359, "udp")] == "app:whoami"


def test_nfs_port_only_for_exported_shares(fw):
    (fw.etc / "dahlia").mkdir(parents=True)
    path = fw.etc / "dahlia/shares.json"
    path.write_text(json.dumps({"shares": [{"name": "media", "security": "public"}]}))
    assert 2049 not in {p.port for p in firewall.open_ports(fw.etc, fw.apps_dir)}
    path.write_text(json.dumps({"shares": [
        {"name": "media", "security": "private", "nfs": True}]}))
    assert 2049 not in {p.port for p in firewall.open_ports(fw.etc, fw.apps_dir)}
    path.write_text(json.dumps({"shares": [{"name": "media", "security": "secure", "nfs": True}]}))
    ports = {(p.port, p.protocol): p.service for p in firewall.open_ports(fw.etc, fw.apps_dir)}
    assert ports[(2049, "tcp")] == "nfs" and (2049, "udp") not in ports


def test_empty_share_list_keeps_smb_closed(fw):
    (fw.etc / "dahlia").mkdir(parents=True)
    (fw.etc / "dahlia/shares.json").write_text(json.dumps({"shares": []}))
    assert 445 not in {p.port for p in firewall.open_ports(fw.etc, fw.apps_dir)}


def test_local_subnets():
    assert firewall.local_subnets(lambda cmd: IP_ADDR) == ["192.168.178.0/24",
                                                           "2a02:810d:1:2::/64"]
    assert firewall.local_subnets(lambda cmd: "not json") == []


# --- ruleset --------------------------------------------------------------------


def test_ruleset():
    text = firewall.ruleset([Port(22, "tcp", "ssh"), Port(5353, "udp", "mdns"),
                             Port(443, "tcp", "https")], ["192.168.178.0/24", "2a02::/48"])
    # Replaces only its own table, in one transaction.
    assert text.count("delete table inet dahlia") == 1 and "flush ruleset" not in text
    assert "policy drop" in text
    assert "elements = { 22, 443 }" in text and "elements = { 5353 }" in text
    assert "192.168.178.0/24 }" in text and "fc00::/7, fe80::/10, 2a02::/48 }" in text
    assert "ip saddr @local4 tcp dport @tcp_ports accept" in text
    assert "ip6 saddr @local6 udp dport @udp_ports accept" in text
    assert "ct status dnat ip saddr != @local4 drop" in text


def test_ruleset_without_udp_ports():
    text = firewall.ruleset([Port(22, "tcp", "ssh")], [])
    assert "udp_ports" not in text


OWN_RULES = [
    {"action": "accept", "protocol": "tcp", "ports": "25565", "source": "any",
     "comment": "Minecraft"},
    {"action": "accept", "protocol": "udp", "ports": "27015-27030", "source": "local"},
    {"action": "drop", "protocol": "all", "source": "192.168.178.66", "comment": "Kids PC"},
    {"action": "drop", "protocol": "tcp", "ports": "22", "source": "2a02:810d:1:2::/64"},
]


@pytest.mark.skipif(os.geteuid() != 0 or not shutil.which("nft"), reason="needs root and nft")
def test_ruleset_parses(tmp_path):
    rules = tmp_path / "firewall.nft"
    rules.write_text(firewall.ruleset(list(firewall.BASE_PORTS + firewall.SMB_PORTS),
                                      ["192.168.178.0/24", "2a02:810d:1:2::/64"],
                                      rules=firewall.check_rules(OWN_RULES)))
    proc = subprocess.run(["nft", "-c", "-f", str(rules)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_check_rules_normalizes():
    got = firewall.check_rules(OWN_RULES)
    assert got[0] == {"action": "accept", "protocol": "tcp", "ports": "25565", "source": "any",
                      "comment": "Minecraft"}
    assert got[1]["source"] == "local" and got[1]["comment"] == ""
    assert got[2] == {"action": "drop", "protocol": "all", "ports": "", "source":
                      "192.168.178.66/32", "comment": "Kids PC"}
    assert firewall.check_rule({"action": "accept", "protocol": "tcp", "ports": "80-80",
                                "source": "0.0.0.0/0"})["source"] == "any"
    assert firewall.check_rule({"action": "accept", "protocol": "tcp", "ports": "80-80"}
                               )["ports"] == "80"


@pytest.mark.parametrize("bad,why", [
    ({"action": "reject"}, "action"),
    ({"protocol": "icmp"}, "protocol"),
    ({"ports": "0"}, "1 to 65535"),
    ({"ports": "9000-8000"}, "lower one first"),
    ({"ports": "80,443"}, "not a port"),
    ({"protocol": "all", "ports": "80"}, "need tcp or udp"),
    ({"protocol": "all", "ports": "", "source": "any"}, "turns the firewall off"),
    ({"action": "drop", "protocol": "all", "ports": "", "source": "local"}, "single device"),
    ({"action": "drop", "source": "any"}, "single device"),
    ({"source": "nas.local"}, "not local, any or a network"),
    ({"comment": 'a" accept'}, "comment"),
    ({"comment": "x" * 65}, "comment"),
])
def test_check_rule_rejects(bad, why):
    rule = {"action": "accept", "protocol": "tcp", "ports": "8080", "source": "local", **bad}
    with pytest.raises(FirewallError, match=why):
        firewall.check_rule(rule)


def test_own_rules_come_first():
    text = firewall.ruleset(list(firewall.BASE_PORTS), [], {"port": 51820, "subnet":
                            "10.253.0.0/24", "interface": "wg"},
                            firewall.check_rules(OWN_RULES))
    lines = [ln.strip() for ln in text.splitlines()]
    assert 'tcp dport 25565 accept comment "Minecraft"' in lines
    assert "ip saddr @local4 udp dport 27015-27030 accept" in lines
    assert "ip6 saddr @local6 udp dport 27015-27030 accept" in lines
    assert 'ip saddr 192.168.178.66/32 drop comment "Kids PC"' in lines
    assert "ip6 saddr 2a02:810d:1:2::/64 tcp dport 22 drop" in lines
    # Before WireGuard and the generated port rules.
    first_own = lines.index('tcp dport 25565 accept comment "Minecraft"')
    assert first_own < lines.index("udp dport 51820 accept")
    assert first_own < lines.index("ip saddr @local4 tcp dport @tcp_ports accept")
    assert first_own > lines.index("ct state established,related accept")


def test_set_rules_keeps_networks(fw):
    fw.set(True, ["10.20.0.0/16"])
    status = fw.set_rules(OWN_RULES[:1])
    assert status["networks"] == ["10.20.0.0/16"] and status["rules"][0]["ports"] == "25565"
    assert "tcp dport 25565 accept" in (fw.run_dir / "firewall.nft").read_text()
    # Changing the networks keeps the rules.
    fw.set(True, [])
    assert json.loads(fw.config.read_text())["rules"][0]["comment"] == "Minecraft"


# --- apply ----------------------------------------------------------------------


def test_apply_checks_then_loads(fw):
    status = fw.apply()
    rules = fw.run_dir / "firewall.nft"
    assert fw.run.calls[-2:] == [["nft", "-c", "-f", str(rules)], ["nft", "-f", str(rules)]]
    assert "192.168.178.0/24" in rules.read_text()
    assert status["enabled"] and status["subnets"] == ["192.168.178.0/24", "2a02:810d:1:2::/64"]


def test_disable_removes_table(fw):
    status = fw.set(False, [])
    assert status["enabled"] is False
    assert (fw.run_dir / "firewall.nft").read_text() == firewall.disabled_ruleset()
    assert json.loads(fw.config.read_text()) == {"enabled": False, "networks": [], "rules": []}


def test_set_restores_settings_when_nft_fails(fw):
    fw.set(True, ["10.20.0.0/16"])
    fw.run.fail = ["nft", "-c"]
    with pytest.raises(FirewallError):
        fw.set(True, ["10.30.0.0/16"])
    assert json.loads(fw.config.read_text())["networks"] == ["10.20.0.0/16"]
    # Only the check ran; the loaded table was never touched.
    assert fw.run.calls[-1][:2] == ["nft", "-c"]


def test_set_without_old_settings_removes_file(fw):
    fw.run.fail = ["nft"]
    with pytest.raises(FirewallError):
        fw.set(True, [])
    assert not fw.config.exists()


def test_apply_quietly_reports_error(fw, capsys):
    fw.run.fail = ["nft"]
    assert "syntax error" in firewall.apply_quietly(fw)
    assert "firewall" in capsys.readouterr().err
    fw.run.fail = None
    assert firewall.apply_quietly(fw) == ""


# --- agent actions --------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path):
    runner = FakeRunner({("ip", "-j"): (0, IP_ADDR, "")})
    return actions.Context(runner=runner, etc=tmp_path / "etc", run_dir=tmp_path / "run",
                           var=tmp_path / "var")


def test_action_status(ctx):
    res = run(ctx, "settings.firewall")
    assert res["ok"], res
    assert res["result"]["enabled"] and "fc00::/7" in res["result"]["local"]
    assert {"port": 22, "protocol": "tcp", "service": "ssh"} in res["result"]["ports"]
    assert not any(c[0] == "nft" for c in ctx.runner.calls)


def test_action_set(ctx):
    res = run(ctx, "settings.set_firewall", {"enabled": True, "networks": ["10.99.0.0/16"]})
    assert res["ok"], res
    assert res["result"]["networks"] == ["10.99.0.0/16"]
    assert ["nft", "-f", str(ctx.run_dir / "firewall.nft")] in ctx.runner.calls
    assert "10.99.0.0/16" in (ctx.run_dir / "firewall.nft").read_text()


def test_action_set_invalid_network(ctx):
    res = run(ctx, "settings.set_firewall", {"enabled": True, "networks": ["0.0.0.0/0"]})
    assert err_code(res) == protocol.INVALID_PARAMS
    assert not (ctx.etc / "dahlia/firewall.json").exists()


def test_action_set_nft_failure_rolls_back(ctx):
    ctx.runner.answers[("nft",)] = (1, "", "Error: Could not process rule\n")
    res = run(ctx, "settings.set_firewall", {"enabled": False})
    assert err_code(res) == protocol.FAILED
    assert not (ctx.etc / "dahlia/firewall.json").exists()


def test_action_set_rules(ctx):
    res = run(ctx, "settings.set_firewall_rules", {"rules": OWN_RULES})
    assert res["ok"], res
    assert len(res["result"]["rules"]) == 4
    assert 'drop comment "Kids PC"' in (ctx.run_dir / "firewall.nft").read_text()
    bad = run(ctx, "settings.set_firewall_rules",
              {"rules": [{"action": "drop", "protocol": "all", "source": "local"}]})
    assert err_code(bad) == protocol.INVALID_PARAMS
    assert len(run(ctx, "settings.firewall")["result"]["rules"]) == 4
