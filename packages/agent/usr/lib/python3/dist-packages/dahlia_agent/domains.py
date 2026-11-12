"""Names, the local CA, app proxies and mDNS aliases.

Names for the server and its apps, all without a DNS server:

    https://<host>.local                 server (Avahi host name)
    https://<app>.local                  app, single-label mDNS alias (optional)
    http://<host>.local:<public_port>    app port link

Multi-level .local names are not used: mDNS resolvers only answer single
labels reliably. The placeholder {DOMAIN} in app templates is "local", so
"https://<app>.{DOMAIN}" is the app's mDNS alias.

A local CA signs the certificates. The server's own certificate names only
the server, so installing or removing an app never replaces it: a browser
that accepted it once keeps working. The <app>.local aliases go into a
second certificate used by the app proxies. The CA carries name constraints:
it can only sign names under .local and private IPv4 addresses, so
importing it into a browser does not let it vouch for other sites. (CAs
made before 1.1 may also sign home.arpa; they are kept, so nobody has to
import a new one.) The CA key never leaves /etc/dahlia/tls.

Layout:
    /etc/dahlia/domains.json               mdns_aliases
    /etc/dahlia/tls/ca.key, ca.crt         local CA (key readable by root only)
    /etc/dahlia/tls/ca.json                names the CA may sign
    /etc/dahlia/tls/server.key, server.crt certificate of the server itself
    /etc/dahlia/tls/server.json            names in server.crt
    /etc/dahlia/tls/apps.key, apps.crt     certificate of the app proxies
    /etc/dahlia/tls/apps.json              names in apps.crt
    /var/lib/dahlia/mdns-aliases.json      names mdns publishes
    /etc/nginx/conf.d/dahlia-app-<id>.conf generated per app
"""

import argparse
import contextlib
import ipaddress
import json
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = Path("/etc/dahlia/domains.json")
TLS_DIR = Path("/etc/dahlia/tls")
NGINX_DIR = Path("/etc/nginx/conf.d")
ALIAS_FILE = Path("/var/lib/dahlia/mdns-aliases.json")

# What {DOMAIN} in app templates stands for: <app>.local is the mDNS alias.
APP_DOMAIN = "local"
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOST_RE = re.compile(rf"^{LABEL}$")

# Names the CA may sign.
BASE_DNS = ("local",)
PRIVATE_NETS = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
# Interfaces whose addresses never go into the certificate or mDNS.
SKIP_IFACES = ("lo", "docker", "br-", "veth", "virbr", "incusbr")

CA_DAYS = 7300
# Apple clients reject server certificates valid for more than 825 days.
CERT_DAYS = 825
CERT_RENEW_SECONDS = 30 * 86400
# Create a new CA once it would expire before a fresh server certificate.
CA_RENEW_SECONDS = (CERT_DAYS + 30) * 86400


class DomainError(Exception):
    pass


# --- settings -----------------------------------------------------------------


@dataclass
class Settings:
    mdns_aliases: bool = True

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        except json.JSONDecodeError:
            raise DomainError(f"{path} is not valid JSON") from None
        if not isinstance(data, dict):
            raise DomainError(f"{path} must contain an object")
        # "domain" from versions before 1.1 is ignored.
        s = cls()
        if "mdns_aliases" in data:
            if type(data["mdns_aliases"]) is not bool:
                raise DomainError("mdns_aliases must be true or false")
            s.mdns_aliases = data["mdns_aliases"]
        return s

    def save(self, path: Path) -> None:
        write_file(path, json.dumps(vars(self), indent=2) + "\n")


def write_file(path: Path, text: str, mode: int = 0o644) -> bool:
    """Write atomically. Returns False when the content was already the same."""
    with contextlib.suppress(FileNotFoundError):
        if path.read_text() == text:
            path.chmod(mode)
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)
    return True


# --- host facts ---------------------------------------------------------------


def run_json(cmd: list[str]):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def is_private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in PRIVATE_NETS)


def lan_addresses() -> list[str]:
    """Private IPv4 addresses of LAN interfaces, primary address first."""
    data = run_json(["ip", "-4", "-j", "addr", "show", "scope", "global"]) or []
    addrs = []
    for iface in data:
        if str(iface.get("ifname", "")).startswith(SKIP_IFACES):
            continue
        for a in iface.get("addr_info", []):
            local = a.get("local")
            if is_private(local) and local not in addrs:
                addrs.append(local)
    route = run_json(["ip", "-4", "-j", "route", "get", "1.1.1.1"]) or []
    src = route[0].get("prefsrc") if route else None
    if src in addrs:
        addrs.remove(src)
        addrs.insert(0, src)
    return addrs


@dataclass
class Host:
    """Host name and addresses. Tests use fixed ones."""

    name: str
    addresses: list[str]

    @classmethod
    def detect(cls) -> "Host":
        name = socket.gethostname().split(".")[0].lower()
        if not HOST_RE.fullmatch(name):
            name = "dahlia"
        return cls(name=name, addresses=lan_addresses())

    @property
    def primary(self) -> str | None:
        return self.addresses[0] if self.addresses else None


# --- apps ---------------------------------------------------------------------


@dataclass(frozen=True)
class AppProxy:
    """What the proxy needs to know about an installed app with a web UI."""

    id: str
    host_port: int
    public_port: int


def aliases(apps: list[AppProxy], settings: Settings, host: Host) -> list[str]:
    if not settings.mdns_aliases:
        return []
    return sorted(a.id for a in apps if a.id != host.name)


def app_urls(app_id: str, public_port: int | None, settings: Settings, host: Host) -> dict:
    urls = {
        "mdns": f"https://{app_id}.local" if settings.mdns_aliases and app_id != host.name
        else None,
        "port": f"http://{host.name}.local:{public_port}" if public_port else None,
    }
    return urls


# --- certificates -------------------------------------------------------------


@dataclass
class TlsPaths:
    dir: Path = TLS_DIR

    @property
    def ca_key(self) -> Path:
        return self.dir / "ca.key"

    @property
    def ca_crt(self) -> Path:
        return self.dir / "ca.crt"

    @property
    def ca_meta(self) -> Path:
        return self.dir / "ca.json"

    @property
    def key(self) -> Path:
        return self.dir / "server.key"

    @property
    def crt(self) -> Path:
        return self.dir / "server.crt"

    @property
    def meta(self) -> Path:
        return self.dir / "server.json"

    @property
    def apps_key(self) -> Path:
        return self.dir / "apps.key"

    @property
    def apps_crt(self) -> Path:
        return self.dir / "apps.crt"

    @property
    def apps_meta(self) -> Path:
        return self.dir / "apps.json"


def openssl(*args: str) -> str:
    try:
        proc = subprocess.run(["openssl", *args], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise DomainError("openssl is not installed") from None
    if proc.returncode != 0:
        lines = [ln for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
        raise DomainError(f"openssl {args[0]}: {lines[-1] if lines else proc.returncode}")
    return proc.stdout


def new_key(path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    tmp.touch(mode=0o600)
    openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
            "-out", str(tmp))
    tmp.chmod(0o600)
    tmp.replace(path)


def valid_for(crt: Path, seconds: int) -> bool:
    if not crt.is_file():
        return False
    try:
        openssl("x509", "-noout", "-checkend", str(seconds), "-in", str(crt))
    except DomainError:
        return False
    return True


def cert_info(crt: Path) -> dict | None:
    if not crt.is_file():
        return None
    out = openssl("x509", "-noout", "-enddate", "-fingerprint", "-sha256", "-in", str(crt))
    info = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key == "notAfter":
            info["not_after"] = value.strip()
        elif key.lower().endswith("fingerprint"):
            info["sha256"] = value.strip()
    return info


def covers(permitted: list[str], name: str) -> bool:
    name = name.removeprefix("*.")
    return any(name == p or name.endswith("." + p) for p in permitted)


def read_meta(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def ensure_ca(tls: TlsPaths, host: Host) -> bool:
    """Create the CA unless a usable one exists. Returns True if it is new."""
    meta = read_meta(tls.ca_meta)
    permitted = meta.get("dns") if isinstance(meta.get("dns"), list) else []
    if (tls.ca_key.is_file() and valid_for(tls.ca_crt, CA_RENEW_SECONDS)
            and all(covers(permitted, n) for n in BASE_DNS)):
        return False
    tls.dir.mkdir(parents=True, exist_ok=True)
    tls.dir.chmod(0o755)
    dns = list(BASE_DNS)
    constraints = [f"permitted;DNS:{n}" for n in dns] + [
        f"permitted;IP:{net.network_address}/{net.netmask}" for net in PRIVATE_NETS
    ]
    config = f"""[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca
[dn]
O = Dahlia Linux
CN = Dahlia local CA {host.name} {secrets.token_hex(4)}
[v3_ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical,{",".join(constraints)}
"""
    new_key(tls.ca_key)
    with tempfile.TemporaryDirectory() as tmp:
        cnf = Path(tmp, "ca.cnf")
        cnf.write_text(config)
        out = Path(tmp, "ca.crt")
        openssl("req", "-x509", "-new", "-key", str(tls.ca_key), "-config", str(cnf),
                "-days", str(CA_DAYS), "-out", str(out))
        write_file(tls.ca_crt, out.read_text())
    write_file(tls.ca_meta, json.dumps({"dns": dns}) + "\n")
    # Certificates of the old CA are worthless now.
    tls.meta.unlink(missing_ok=True)
    tls.apps_meta.unlink(missing_ok=True)
    return True


def server_names(host: Host) -> list[str]:
    return [f"{host.name}.local"]


def app_names(settings: Settings, host: Host, apps: list[AppProxy]) -> list[str]:
    return [f"{a}.local" for a in aliases(apps, settings, host)]


def ensure_server_cert(tls: TlsPaths, names: list[str], addresses: list[str]) -> bool:
    """Issue server.crt when names, addresses, CA or validity changed."""
    return ensure_cert(tls, tls.key, tls.crt, tls.meta, names, addresses)


def ensure_apps_cert(tls: TlsPaths, names: list[str]) -> bool:
    """Issue apps.crt when names, CA or validity changed."""
    return ensure_cert(tls, tls.apps_key, tls.apps_crt, tls.apps_meta, names, [])


def ensure_cert(tls: TlsPaths, key: Path, crt: Path, meta: Path, names: list[str],
                addresses: list[str]) -> bool:
    ca_fpr = cert_info(tls.ca_crt)["sha256"]
    want = {"names": names, "addresses": addresses, "ca": ca_fpr}
    if (read_meta(meta) == want and key.is_file()
            and valid_for(crt, CERT_RENEW_SECONDS)):
        return False
    if not key.is_file():
        new_key(key)
    san = ",".join([f"DNS:{n}" for n in names] + [f"IP:{a}" for a in addresses])
    ext = f"""[v3]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
subjectAltName = {san}
"""
    with tempfile.TemporaryDirectory() as tmp:
        extfile = Path(tmp, "ext.cnf")
        extfile.write_text(ext)
        csr = Path(tmp, "server.csr")
        out = Path(tmp, "server.crt")
        openssl("req", "-new", "-key", str(key), "-subj", f"/CN={names[0]}",
                "-out", str(csr))
        openssl("x509", "-req", "-in", str(csr), "-CA", str(tls.ca_crt),
                "-CAkey", str(tls.ca_key), "-set_serial", "0x" + secrets.token_hex(16),
                "-days", str(CERT_DAYS), "-extfile", str(extfile), "-extensions", "v3",
                "-out", str(out))
        # nginx sends the chain; clients that trust the CA verify it.
        write_file(crt, out.read_text() + tls.ca_crt.read_text())
    write_file(meta, json.dumps(want) + "\n")
    return True


# --- nginx --------------------------------------------------------------------


NGINX_MAP = """# Generated by domains. WebSocket upgrade for app proxies.
map $http_upgrade $connection_upgrade_dahlia {
    default upgrade;
    ''      close;
}
"""


def proxy_location(host_port: int) -> str:
    return f"""    location / {{
        proxy_pass http://127.0.0.1:{host_port};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade_dahlia;
        proxy_read_timeout 3600s;
        proxy_buffering off;
    }}"""


def nginx_conf(app: AppProxy, alias: bool, tls: TlsPaths) -> str:
    """Port link, plus https://<app>.local when the app has an mDNS alias."""
    loc = proxy_location(app.host_port)
    port_link = f"""# Generated by domains for {app.id}. Do not edit.
server {{
    listen {app.public_port};
    listen [::]:{app.public_port};
    server_tokens off;
    client_max_body_size 0;

{loc}
}}
"""
    if not alias:
        return port_link
    return port_link + f"""
server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {app.id}.local;
    server_tokens off;
    client_max_body_size 0;

    ssl_certificate     {tls.apps_crt};
    ssl_certificate_key {tls.apps_key};
    ssl_protocols TLSv1.2 TLSv1.3;

{loc}
}}
"""


def write_proxies(apps: list[AppProxy], alias_ids: list[str], nginx: Path,
                  tls: TlsPaths) -> bool:
    """Write one conf per app and drop stale ones. Returns True on change."""
    changed = False
    wanted = {f"dahlia-app-{a.id}.conf": nginx_conf(a, a.id in alias_ids, tls) for a in apps}
    if nginx.is_dir():
        for f in nginx.glob("dahlia-app-*.conf"):
            if f.name not in wanted:
                f.unlink()
                changed = True
    if not apps:
        return changed
    changed |= write_file(nginx / "apps-map.conf", NGINX_MAP)
    for name, text in wanted.items():
        changed |= write_file(nginx / name, text)
    return changed


def reload_nginx() -> None:
    if not Path("/usr/sbin/nginx").exists():
        return
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    if test.returncode != 0:
        lines = test.stderr.strip().splitlines()
        raise DomainError(f"nginx configuration invalid: {lines[-1] if lines else ''}")
    subprocess.run(["systemctl", "reload-or-restart", "nginx.service"], check=False,
                   capture_output=True, stdin=subprocess.DEVNULL)


def reload_mdns() -> None:
    subprocess.run(["systemctl", "try-reload-or-restart", "mdns.service"], check=False,
                   capture_output=True, stdin=subprocess.DEVNULL)


# --- apply --------------------------------------------------------------------


@dataclass
class Paths:
    config: Path = CONFIG_FILE
    tls: TlsPaths = field(default_factory=TlsPaths)
    nginx: Path = NGINX_DIR
    aliases: Path = ALIAS_FILE


@dataclass
class Domains:
    paths: Paths = field(default_factory=Paths)
    host: Host | None = None
    nginx_reload: Callable = reload_nginx
    mdns_reload: Callable = reload_mdns

    def _host(self) -> Host:
        if self.host is None:
            self.host = Host.detect()
        return self.host

    def settings(self) -> Settings:
        return Settings.load(self.paths.config)

    def apply(self, apps: list[AppProxy]) -> dict:
        """Bring CA, certificate, app proxies and mDNS aliases in line with the settings."""
        s = self.settings()
        host = self._host()
        tls = self.paths.tls
        ca_new = ensure_ca(tls, host)
        cert_new = ensure_server_cert(tls, server_names(host), host.addresses)
        names = app_names(s, host, apps)
        # Without aliases no app proxy listens on 443; the old apps.crt stays unused.
        apps_new = ensure_apps_cert(tls, names) if names else False
        alias_ids = aliases(apps, s, host)
        proxies = write_proxies(apps, alias_ids, self.paths.nginx, tls)
        alias_text = json.dumps({"aliases": alias_ids}) + "\n"
        alias_changed = write_file(self.paths.aliases, alias_text)
        if ca_new or cert_new or apps_new or proxies:
            self.nginx_reload()
        if alias_changed:
            self.mdns_reload()
        return {"ca_renewed": ca_new, "cert_renewed": cert_new, "apps_cert_renewed": apps_new}

    def set(self, apps: list[AppProxy], mdns_aliases: bool | None = None) -> dict:
        s = self.settings()
        old = Settings(**vars(s))
        if mdns_aliases is not None:
            s.mdns_aliases = mdns_aliases
        s.save(self.paths.config)
        try:
            result = self.apply(apps)
        except DomainError:
            old.save(self.paths.config)
            self.apply(apps)
            raise
        return {**self.show(apps), **result}

    def show(self, apps: list[AppProxy]) -> dict:
        s = self.settings()
        host = self._host()
        return {
            "mdns_aliases": s.mdns_aliases,
            "hostname": host.name,
            "address": host.primary,
            "ca": cert_info(self.paths.tls.ca_crt),
            "cert": cert_info(self.paths.tls.crt),
            "names": list(dict.fromkeys(
                read_meta(self.paths.tls.meta).get("names", [])
                + read_meta(self.paths.tls.apps_meta).get("names", []))),
            "aliases": aliases(apps, s, host),
        }


# --- mDNS aliases -------------------------------------------------------------


def load_aliases(path: Path) -> list[str]:
    data = read_meta(path)
    names = data.get("aliases", [])
    if not isinstance(names, list):
        return []
    return [n for n in names if isinstance(n, str) and HOST_RE.fullmatch(n)]


def primary_address() -> str | None:
    addrs = lan_addresses()
    return addrs[0] if addrs else None


@dataclass
class Publisher:
    """Publishes <alias>.local -> primary address with one avahi-publish per alias.

    A child that exits (name collision, avahi restart) is started again
    after RETRY seconds.
    """

    alias_file: Path = ALIAS_FILE
    address: Callable = primary_address
    spawn: Callable = subprocess.Popen
    clock: Callable = time.monotonic
    retry: float = 60.0
    children: dict = field(default_factory=dict)
    current: tuple = ()

    def _start(self, name: str, addr: str) -> None:
        try:
            proc = self.spawn(["avahi-publish", "-a", "-R", f"{name}.local", addr],
                              stdin=subprocess.DEVNULL)
        except OSError as e:
            print(f"mdns: cannot run avahi-publish: {e}", file=sys.stderr, flush=True)
            proc = None
        self.children[name] = (proc, self.clock())

    def stop(self) -> None:
        procs = [p for p, _ in self.children.values() if p is not None]
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
        self.children = {}

    def sync(self, force: bool = False) -> None:
        names = load_aliases(self.alias_file)
        addr = self.address()
        want = (tuple(names), addr)
        if force or want != self.current:
            self.stop()
            self.current = want
            if addr is None:
                return
            for name in names:
                print(f"mdns: {name}.local -> {addr}", flush=True)
                self._start(name, addr)
            return
        for name, (proc, started) in list(self.children.items()):
            if proc is not None and proc.poll() is None:
                continue
            if self.clock() - started >= self.retry:
                code = "not started" if proc is None else proc.returncode
                print(f"mdns: avahi-publish for {name}.local exited ({code}), "
                      "starting again", file=sys.stderr, flush=True)
                self._start(name, addr)


def run_publisher(pub: Publisher, interval: float = 10.0) -> int:
    wake = threading.Event()
    state = {"stop": False, "force": False}

    def on_term(_sig, _frame):
        state["stop"] = True
        wake.set()

    def on_hup(_sig, _frame):
        state["force"] = True
        wake.set()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    signal.signal(signal.SIGHUP, on_hup)
    try:
        while not state["stop"]:
            force, state["force"] = state["force"], False
            pub.sync(force=force)
            wake.wait(interval)
            wake.clear()
    finally:
        pub.stop()
    return 0


# --- CLI ----------------------------------------------------------------------


def installed_proxies() -> list[AppProxy]:
    # Imported here: apps imports this module.
    from . import apps

    return apps.proxies(apps.Paths())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="domains",
                                description="Certificates, app proxies and mDNS aliases.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="settings, names and certificates")
    sub.add_parser("apply", help="renew CA/certificate if needed, write proxies and aliases")
    s = sub.add_parser("set", help="change settings and apply them")
    s.add_argument("--mdns", choices=("on", "off"))
    sub.add_parser("mdns", help="run the mDNS alias publisher (service)")
    args = p.parse_args(argv)

    if args.command == "mdns":
        return run_publisher(Publisher())
    from .apps import AppError

    d = Domains()
    try:
        apps = installed_proxies()
        if args.command == "show":
            result = d.show(apps)
        elif args.command == "apply":
            result = d.apply(apps)
        else:
            mdns = None if args.mdns is None else args.mdns == "on"
            result = d.set(apps, mdns_aliases=mdns)
    except (DomainError, AppError) as e:
        print(f"domains: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0
