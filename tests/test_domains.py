import json
import shutil
import subprocess

import pytest
from dahlia_agent import domains
from dahlia_agent.domains import AppProxy, DomainError, Domains, Host, Settings, TlsPaths

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")

HOST = Host("dahlia", ["192.168.1.5", "10.0.0.7"])
WHOAMI = AppProxy("whoami", 41000, 8099)
JELLYFIN = AppProxy("jellyfin", 41001, 8096)


def verify(tls: TlsPaths, crt=None) -> subprocess.CompletedProcess:
    # server.crt holds the chain; verify only the first (leaf) certificate.
    leaf = (crt or tls.crt).read_text().split("-----END CERTIFICATE-----")[0]
    leaf += "-----END CERTIFICATE-----\n"
    return subprocess.run(
        ["openssl", "verify", "-x509_strict", "-CAfile", str(tls.ca_crt)],
        input=leaf, capture_output=True, text=True,
    )


def sans(crt) -> str:
    return subprocess.run(
        ["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(crt)],
        capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def d(tmp_path):
    paths = domains.Paths(
        config=tmp_path / "domains.json",
        tls=TlsPaths(tmp_path / "tls"),
        nginx=tmp_path / "nginx",
        aliases=tmp_path / "aliases.json",
    )
    paths.nginx.mkdir()
    calls = {"nginx": 0, "mdns": 0}

    def count(name):
        def f():
            calls[name] += 1
        return f

    obj = Domains(paths=paths, host=HOST, nginx_reload=count("nginx"),
                  mdns_reload=count("mdns"))
    obj.calls = calls
    return obj


# --- settings -----------------------------------------------------------------


@pytest.mark.parametrize("domain", ["dahlia.home.arpa", "lab.example.org", "a-b.lan.home.arpa"])
def test_domain_ok(domain):
    assert domains.check_domain(domain) == domain


@pytest.mark.parametrize("domain", ["", "home", "dahlia.local", "x.y.local", "Dahlia.home.arpa",
                                    "a..b", "10.0.0.1", "a.b.c.d.e.f.g", "x_y.home.arpa", 3])
def test_domain_rejected(domain):
    with pytest.raises(DomainError):
        domains.check_domain(domain)


def test_settings_default_and_roundtrip(tmp_path):
    path = tmp_path / "domains.json"
    assert Settings.load(path) == Settings("dahlia.home.arpa", True)
    Settings("lab.home.arpa", False).save(path)
    assert Settings.load(path) == Settings("lab.home.arpa", False)


@pytest.mark.parametrize("text", ["{", "[]", '{"domain": "x.local"}', '{"mdns_aliases": 1}'])
def test_settings_invalid(tmp_path, text):
    path = tmp_path / "domains.json"
    path.write_text(text)
    with pytest.raises(DomainError):
        Settings.load(path)


def test_app_urls():
    s = Settings()
    assert domains.app_urls("whoami", 8099, s, HOST) == {
        "domain": "https://whoami.dahlia.home.arpa",
        "mdns": "https://whoami.local",
        "port": "http://dahlia.local:8099",
    }
    assert domains.app_urls("dahlia", 8099, s, HOST)["mdns"] is None
    assert domains.app_urls("whoami", 8099, Settings(mdns_aliases=False), HOST)["mdns"] is None


# --- host facts ---------------------------------------------------------------


def test_lan_addresses(monkeypatch):
    addr = [
        {"ifname": "docker0", "addr_info": [{"local": "172.17.0.1"}]},
        {"ifname": "enp1s0", "addr_info": [{"local": "10.0.0.7"}, {"local": "192.168.1.5"}]},
        {"ifname": "wan0", "addr_info": [{"local": "203.0.113.4"}]},
    ]
    route = [{"dst": "1.1.1.1", "prefsrc": "192.168.1.5"}]

    def fake(cmd):
        return route if "route" in cmd else addr

    monkeypatch.setattr(domains, "run_json", fake)
    assert domains.lan_addresses() == ["192.168.1.5", "10.0.0.7"]


def test_lan_addresses_without_ip(monkeypatch):
    monkeypatch.setattr(domains, "run_json", lambda cmd: None)
    assert domains.lan_addresses() == []


# --- certificates -------------------------------------------------------------


def test_ca_and_server_cert(d):
    result = d.apply([WHOAMI])
    assert result == {"ca_renewed": True, "cert_renewed": True, "apps_cert_renewed": True}
    tls = d.paths.tls
    assert tls.ca_key.stat().st_mode & 0o777 == 0o600
    assert tls.key.stat().st_mode & 0o777 == 0o600
    assert tls.apps_key.stat().st_mode & 0o777 == 0o600
    assert tls.ca_crt.stat().st_mode & 0o777 == 0o644
    assert verify(tls).returncode == 0, verify(tls).stderr
    assert verify(tls, tls.apps_crt).returncode == 0
    names = sans(tls.crt)
    for n in ("DNS:dahlia.local", "DNS:dahlia.home.arpa", "DNS:*.dahlia.home.arpa",
              "IP Address:192.168.1.5", "IP Address:10.0.0.7"):
        assert n in names
    assert "whoami.local" not in names
    apps = sans(tls.apps_crt)
    assert "DNS:*.dahlia.home.arpa" in apps and "DNS:whoami.local" in apps
    assert d.calls == {"nginx": 1, "mdns": 1}


def test_apply_twice_changes_nothing(d):
    d.apply([WHOAMI])
    before = d.paths.tls.crt.read_text()
    assert d.apply([WHOAMI]) == {
        "ca_renewed": False, "cert_renewed": False, "apps_cert_renewed": False,
    }
    assert d.paths.tls.crt.read_text() == before
    assert d.calls == {"nginx": 1, "mdns": 1}


def test_new_app_keeps_server_cert(d):
    # A browser that accepted the server certificate must not lose the
    # dashboard because an app was installed.
    d.apply([WHOAMI])
    ca = d.paths.tls.ca_crt.read_text()
    server = d.paths.tls.crt.read_text()
    assert d.apply([WHOAMI, JELLYFIN]) == {
        "ca_renewed": False, "cert_renewed": False, "apps_cert_renewed": True,
    }
    assert d.paths.tls.ca_crt.read_text() == ca
    assert d.paths.tls.crt.read_text() == server
    assert "DNS:jellyfin.local" in sans(d.paths.tls.apps_crt)
    assert verify(d.paths.tls, d.paths.tls.apps_crt).returncode == 0
    d.apply([JELLYFIN])
    assert d.paths.tls.crt.read_text() == server


def test_ca_name_constraints_block_other_names(d, tmp_path):
    d.apply([])
    tls = d.paths.tls
    for san in ("DNS:www.example.com", "IP:8.8.8.8"):
        ext = tmp_path / "ext.cnf"
        ext.write_text(f"[v3]\nsubjectAltName = {san}\nextendedKeyUsage = serverAuth\n")
        csr = tmp_path / "x.csr"
        crt = tmp_path / "x.crt"
        subprocess.run(["openssl", "req", "-new", "-key", str(tls.key), "-subj", "/CN=x",
                        "-out", str(csr)], check=True, capture_output=True)
        subprocess.run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(tls.ca_crt),
                        "-CAkey", str(tls.ca_key), "-set_serial", "7", "-days", "1",
                        "-extfile", str(ext), "-extensions", "v3", "-out", str(crt)],
                       check=True, capture_output=True)
        res = verify(tls, crt)
        assert res.returncode != 0
        assert "subtree violation" in res.stdout + res.stderr


def test_domain_outside_constraints_makes_new_ca(d):
    d.apply([WHOAMI])
    old_ca = d.paths.tls.ca_crt.read_text()
    result = d.set([WHOAMI], domain="lab.example.org")
    assert result["ca_renewed"] is True
    assert d.paths.tls.ca_crt.read_text() != old_ca
    assert json.loads(d.paths.tls.ca_meta.read_text())["dns"] == [
        "local", "home.arpa", "lab.example.org",
    ]
    assert "DNS:*.lab.example.org" in sans(d.paths.tls.crt)
    assert "DNS:*.lab.example.org" in sans(d.paths.tls.apps_crt)
    assert verify(d.paths.tls).returncode == 0
    assert verify(d.paths.tls, d.paths.tls.apps_crt).returncode == 0


def test_domain_inside_home_arpa_keeps_ca(d):
    d.apply([])
    old_ca = d.paths.tls.ca_crt.read_text()
    result = d.set([], domain="nas.home.arpa")
    assert result["ca_renewed"] is False
    assert result["cert_renewed"] is True
    assert d.paths.tls.ca_crt.read_text() == old_ca


def test_expiring_cert_is_renewed(d, monkeypatch):
    d.apply([])
    monkeypatch.setattr(domains, "CERT_RENEW_SECONDS", domains.CERT_DAYS * 86400 + 86400)
    assert d.apply([])["cert_renewed"] is True


def test_set_invalid_keeps_settings(d):
    d.set([], domain="lab.home.arpa")
    with pytest.raises(DomainError):
        d.set([], domain="x.dahlia.local")
    assert Settings.load(d.paths.config).domain == "lab.home.arpa"


def test_show(d):
    d.apply([WHOAMI])
    s = d.show([WHOAMI])
    assert s["domain"] == "dahlia.home.arpa"
    assert s["address"] == "192.168.1.5"
    assert s["dns_records"] == ["dahlia.home.arpa", "*.dahlia.home.arpa"]
    assert len(s["ca"]["sha256"].split(":")) == 32
    assert s["cert"]["not_after"]
    assert "whoami.local" in s["names"]
    assert s["aliases"] == ["whoami"]


def test_show_before_apply(d):
    s = d.show([])
    assert s["ca"] is None
    assert s["cert"] is None
    assert s["names"] == []


# --- proxies and aliases ------------------------------------------------------


def test_proxy_conf(d):
    d.apply([WHOAMI])
    conf = (d.paths.nginx / "dahlia-app-whoami.conf").read_text()
    assert "listen 8099;" in conf
    assert "listen 443 ssl;" in conf
    assert "server_name whoami.dahlia.home.arpa whoami.local;" in conf
    assert f"ssl_certificate     {d.paths.tls.apps_crt};" in conf
    assert f"ssl_certificate_key {d.paths.tls.apps_key};" in conf
    assert conf.count("proxy_pass http://127.0.0.1:41000;") == 2
    assert (d.paths.nginx / "dahlia-apps-map.conf").exists()


def test_stale_proxy_removed(d):
    d.apply([WHOAMI, JELLYFIN])
    (d.paths.nginx / "other.conf").write_text("keep")
    d.apply([JELLYFIN])
    assert not (d.paths.nginx / "dahlia-app-whoami.conf").exists()
    assert (d.paths.nginx / "dahlia-app-jellyfin.conf").exists()
    assert (d.paths.nginx / "other.conf").read_text() == "keep"


def test_aliases_file(d):
    d.apply([WHOAMI, AppProxy("dahlia", 41002, 8100)])
    # An app named like the host would collide with <host>.local.
    assert json.loads(d.paths.aliases.read_text()) == {"aliases": ["whoami"]}
    assert domains.load_aliases(d.paths.aliases) == ["whoami"]


def test_mdns_off(d):
    d.apply([WHOAMI])
    mdns_before = d.calls["mdns"]
    result = d.set([WHOAMI], mdns_aliases=False)
    assert result["cert_renewed"] is False
    assert result["apps_cert_renewed"] is True
    assert "whoami.local" not in sans(d.paths.tls.apps_crt)
    assert json.loads(d.paths.aliases.read_text()) == {"aliases": []}
    assert d.calls["mdns"] == mdns_before + 1


def test_load_aliases_filters_bad_names(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"aliases": ["ok", "Bad", "a.b", 3, "-x"]}))
    assert domains.load_aliases(p) == ["ok"]
    assert domains.load_aliases(tmp_path / "missing.json") == []


# --- publisher ----------------------------------------------------------------


class FakeProc:
    def __init__(self, cmd):
        self.cmd = cmd
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def pub(tmp_path):
    state = {"addr": "192.168.1.5", "now": 0.0, "procs": []}

    def spawn(cmd, **_kw):
        p = FakeProc(cmd)
        state["procs"].append(p)
        return p

    p = domains.Publisher(alias_file=tmp_path / "aliases.json", address=lambda: state["addr"],
                          spawn=spawn, clock=lambda: state["now"], retry=60)
    p.state = state
    p.alias_file.write_text(json.dumps({"aliases": ["whoami", "jellyfin"]}))
    return p


def test_publisher_starts_one_child_per_alias(pub):
    pub.sync()
    cmds = [p.cmd for p in pub.state["procs"]]
    assert cmds == [
        ["avahi-publish", "-a", "-R", "whoami.local", "192.168.1.5"],
        ["avahi-publish", "-a", "-R", "jellyfin.local", "192.168.1.5"],
    ]
    pub.sync()
    assert len(pub.state["procs"]) == 2


def test_publisher_restarts_on_address_change(pub):
    pub.sync()
    first = list(pub.state["procs"])
    pub.state["addr"] = "192.168.1.9"
    pub.sync()
    assert all(p.terminated for p in first)
    assert pub.state["procs"][-1].cmd[-1] == "192.168.1.9"
    assert len(pub.state["procs"]) == 4


def test_publisher_retries_exited_child_later(pub):
    pub.sync()
    pub.state["procs"][0].returncode = 1
    pub.state["now"] = 30
    pub.sync()
    assert len(pub.state["procs"]) == 2
    pub.state["now"] = 61
    pub.sync()
    assert len(pub.state["procs"]) == 3
    assert pub.state["procs"][-1].cmd[3] == "whoami.local"


def test_publisher_without_address(pub):
    pub.state["addr"] = None
    pub.sync()
    assert pub.state["procs"] == []


def test_publisher_stop(pub):
    pub.sync()
    pub.stop()
    assert all(p.terminated for p in pub.state["procs"])
    assert pub.children == {}


def test_publisher_survives_missing_avahi(pub):
    def missing(cmd, **_kw):
        raise FileNotFoundError("avahi-publish")

    pub.spawn = missing
    pub.sync()
    assert set(pub.children) == {"whoami", "jellyfin"}
    pub.state["now"] = 61
    pub.sync()
    pub.stop()
