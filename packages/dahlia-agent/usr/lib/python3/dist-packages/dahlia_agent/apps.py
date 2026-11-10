"""Apps: Docker containers from Dahlia app templates.

A template (YAML, schema 1) describes one container. Only the fields below
exist; everything else is rejected, so a template cannot ask for privileged
mode, host networking, devices or extra capabilities.

    schema: 1
    id: whoami
    name: Whoami
    description: {de: "...", en: "..."}
    image: traefik/whoami:v1.10.3        # fixed tag, never "latest"
    web: {port: 80, public_port: 8099}   # optional; port links via nginx
    lan_ports:                           # optional, reachable from the LAN
      - {container: 7359, host: 7359, protocol: udp}
    volumes:
      - {container: /config, appdata: config}           # /mnt/user/appdata/<id>/config
      - {container: /media, share: media, readonly: true}  # /mnt/user/media
    environment: {TZ: "{TZ}", PUID: "{PUID}", PGID: "{PGID}"}

The web port is published on 127.0.0.1 only (a port from HOST_PORTS);
nginx listens on public_port in the LAN and proxies to it. Data lives on
the array under /mnt/user, never on the system SSD. Containers are not
restarted by Docker itself: dahlia-apps.service starts the enabled apps
after the disks are mounted.

Layout:
    /etc/dahlia/apps.json                    catalog URL
    /var/lib/dahlia/apps-catalog/            last downloaded catalog
    /var/lib/dahlia/apps/<id>/app.yaml       template the app was installed from
    /var/lib/dahlia/apps/<id>/compose.json   generated, used by docker compose
    /var/lib/dahlia/apps/<id>/state.json     host port, enabled flag
"""

import argparse
import contextlib
import grp
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILE = Path("/etc/dahlia/apps.json")
APPS_DIR = Path("/var/lib/dahlia/apps")
CATALOG_DIR = Path("/var/lib/dahlia/apps-catalog")
USER_POOL = Path("/mnt/user")
NGINX_DIR = Path("/etc/nginx/conf.d")
APPS_USER = "dahlia-apps"
APPS_GROUP = "dahlia"

# Kept out of the ephemeral port pool by sysctl.d/50-dahlia-apps.conf.
HOST_PORTS = range(41000, 42000)
# Ports Dahlia itself uses or that would break the system when taken.
RESERVED_PORTS = {22, 53, 80, 443, 5353, 8080}
MAX_FILE = 256 * 1024
DOWNLOAD_TIMEOUT = 30

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
NAME_RE = re.compile(r"^[\w .+-]{1,40}$")
SUBDIR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# registry/name:tag or name:tag@sha256:..., tag required.
IMAGE_RE = re.compile(
    r"^(?:[a-z0-9.-]+(?::[0-9]+)?/)?[a-z0-9]+(?:[._/-][a-z0-9]+)*"
    r":[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}(?:@sha256:[0-9a-f]{64})?$"
)
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
PLACEHOLDERS = ("TZ", "PUID", "PGID")
PROTOCOLS = ("tcp", "udp")


class AppError(Exception):
    pass


# --- templates ----------------------------------------------------------------


@dataclass
class Volume:
    container: str
    appdata: str | None = None
    share: str | None = None
    readonly: bool = False


@dataclass
class LanPort:
    container: int
    host: int
    protocol: str = "tcp"


@dataclass
class Template:
    id: str
    name: str
    description: dict[str, str]
    image: str
    web_port: int | None = None
    public_port: int | None = None
    lan_ports: list[LanPort] = field(default_factory=list)
    volumes: list[Volume] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "image": self.image,
            "public_port": self.public_port,
            "lan_ports": [vars(p) for p in self.lan_ports],
        }


def _fields(obj, allowed: set[str], required: set[str], where: str) -> dict:
    if not isinstance(obj, dict):
        raise AppError(f"{where} must be a mapping")
    unknown = set(obj) - allowed
    if unknown:
        raise AppError(f"{where}: unknown fields {', '.join(sorted(map(str, unknown)))}")
    missing = required - set(obj)
    if missing:
        raise AppError(f"{where}: missing {', '.join(sorted(missing))}")
    return obj


def _port(value, where: str) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise AppError(f"{where} must be a port number")
    return value


def _container_path(value, where: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 200:
        raise AppError(f"{where} must be an absolute path")
    parts = value.split("/")[1:]
    if any(p in ("", ".", "..") for p in parts) or ":" in value or "\0" in value:
        raise AppError(f"{where} is not a clean path")
    if value in ("/", "/proc", "/sys", "/dev") or parts[0] in ("proc", "sys", "dev"):
        raise AppError(f"{where} may not be a system path")
    return value


def parse_template(text: str, expected_id: str | None = None) -> Template:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise AppError(f"template is not valid YAML: {e}") from None
    top = {"schema", "id", "name", "description", "image", "web", "lan_ports", "volumes",
           "environment"}
    _fields(data, top, {"schema", "id", "name", "image"}, "template")
    if data["schema"] != 1:
        raise AppError("unsupported template schema")
    app_id = data["id"]
    if not isinstance(app_id, str) or not ID_RE.fullmatch(app_id):
        raise AppError("id must be 2-31 characters: a-z, 0-9, -")
    if expected_id is not None and app_id != expected_id:
        raise AppError(f"template id {app_id} does not match {expected_id}")
    if not isinstance(data["name"], str) or not NAME_RE.fullmatch(data["name"]):
        raise AppError("name is invalid")
    image = data["image"]
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        raise AppError("image must be name:tag")
    if image.split("@")[0].rsplit(":", 1)[1] == "latest":
        raise AppError("image tag 'latest' is not allowed, pin a version")

    desc = data.get("description") or {}
    _fields(desc, {"de", "en"}, set(), "description")
    if not all(isinstance(v, str) and len(v) <= 500 for v in desc.values()):
        raise AppError("description texts must be strings up to 500 characters")

    t = Template(id=app_id, name=data["name"], description=dict(desc), image=image)

    if "web" in data:
        web = _fields(data["web"], {"port", "public_port"}, {"port", "public_port"}, "web")
        t.web_port = _port(web["port"], "web.port")
        t.public_port = _port(web["public_port"], "web.public_port")
        if t.public_port in RESERVED_PORTS or t.public_port in HOST_PORTS:
            raise AppError(f"web.public_port {t.public_port} is reserved")

    for i, p in enumerate(data.get("lan_ports") or []):
        where = f"lan_ports[{i}]"
        _fields(p, {"container", "host", "protocol"}, {"container", "host"}, where)
        lp = LanPort(_port(p["container"], f"{where}.container"), _port(p["host"], f"{where}.host"),
                     p.get("protocol", "tcp"))
        if lp.protocol not in PROTOCOLS:
            raise AppError(f"{where}.protocol must be tcp or udp")
        if lp.host in RESERVED_PORTS or lp.host in HOST_PORTS or lp.host == t.public_port:
            raise AppError(f"{where}.host {lp.host} is reserved")
        t.lan_ports.append(lp)

    targets = set()
    for i, v in enumerate(data.get("volumes") or []):
        where = f"volumes[{i}]"
        _fields(v, {"container", "appdata", "share", "readonly"}, {"container"}, where)
        vol = Volume(_container_path(v["container"], f"{where}.container"))
        if ("appdata" in v) == ("share" in v):
            raise AppError(f"{where} needs exactly one of appdata or share")
        for key in ("appdata", "share"):
            if key in v:
                if not isinstance(v[key], str) or not SUBDIR_RE.fullmatch(v[key]):
                    raise AppError(f"{where}.{key} must be a simple directory name")
                if key == "share" and v[key] == "appdata":
                    raise AppError(f"{where}: appdata is not a share")
                setattr(vol, key, v[key])
        ro = v.get("readonly", False)
        if type(ro) is not bool:
            raise AppError(f"{where}.readonly must be true or false")
        vol.readonly = ro
        if vol.container in targets:
            raise AppError(f"{where}: {vol.container} is mounted twice")
        targets.add(vol.container)
        t.volumes.append(vol)

    env = data.get("environment") or {}
    if not isinstance(env, dict):
        raise AppError("environment must be a mapping")
    for key, value in env.items():
        if not isinstance(key, str) or not ENV_KEY_RE.fullmatch(key):
            raise AppError(f"environment key {key!r} is invalid")
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise AppError(f"environment {key} must be a string or number")
        value = str(value)
        if len(value) > 1000 or "\0" in value:
            raise AppError(f"environment {key} is too long")
        for name in re.findall(r"\{([A-Za-z_]+)\}", value):
            if name not in PLACEHOLDERS:
                raise AppError(f"environment {key}: unknown placeholder {{{name}}}")
        t.environment[key] = value
    return t


# --- system facts -------------------------------------------------------------


@dataclass
class Host:
    """Values filled into templates. Tests use fixed ones."""

    tz: str
    uid: int
    gid: int

    @classmethod
    def detect(cls) -> "Host":
        try:
            tz = Path("/etc/timezone").read_text().strip()
        except FileNotFoundError:
            tz = "Etc/UTC"
        try:
            uid = pwd.getpwnam(APPS_USER).pw_uid
        except KeyError:
            raise AppError(f"user {APPS_USER} is missing, reinstall dahlia-agent") from None
        # Group dahlia comes from dahlia-base and may not exist yet right
        # after a fresh install; fall back to the user's own group.
        try:
            gid = grp.getgrnam(APPS_GROUP).gr_gid
        except KeyError:
            gid = pwd.getpwnam(APPS_USER).pw_gid
        return cls(tz=tz or "Etc/UTC", uid=uid, gid=gid)


@dataclass
class Paths:
    apps: Path = APPS_DIR
    catalog: Path = CATALOG_DIR
    pool: Path = USER_POOL
    nginx: Path = NGINX_DIR
    config: Path = CONFIG_FILE

    def app_dir(self, app_id: str) -> Path:
        if not ID_RE.fullmatch(app_id):
            raise AppError("invalid app id")
        return self.apps / app_id


# --- compose and nginx --------------------------------------------------------


def fill(value: str, host: Host) -> str:
    return (
        value.replace("{TZ}", host.tz)
        .replace("{PUID}", str(host.uid))
        .replace("{PGID}", str(host.gid))
    )


def volume_source(t: Template, v: Volume, paths: Paths) -> Path:
    if v.appdata is not None:
        return paths.pool / "appdata" / t.id / v.appdata
    return paths.pool / v.share


def compose(t: Template, host_port: int | None, paths: Paths, host: Host) -> dict:
    ports = []
    if t.web_port is not None and host_port is not None:
        ports.append(f"127.0.0.1:{host_port}:{t.web_port}/tcp")
    for p in t.lan_ports:
        ports.append(f"0.0.0.0:{p.host}:{p.container}/{p.protocol}")
    service = {
        "image": t.image,
        "container_name": f"dahlia-{t.id}",
        # dahlia-apps.service starts enabled apps once the array is mounted.
        "restart": "no",
        "security_opt": ["no-new-privileges:true"],
        "environment": {k: fill(v, host) for k, v in t.environment.items()},
        "volumes": [
            {
                "type": "bind",
                "source": str(volume_source(t, v, paths)),
                "target": v.container,
                "read_only": v.readonly,
                "bind": {"create_host_path": False},
            }
            for v in t.volumes
        ],
        "logging": {"driver": "local", "options": {"max-size": "10m", "max-file": "3"}},
        "labels": {"org.dahlia.app": t.id},
    }
    if ports:
        service["ports"] = ports
    return {"name": f"dahlia-{t.id}", "services": {"app": service}}


def nginx_conf(t: Template, host_port: int) -> str:
    return f"""# Generated by dahlia-apps for {t.id}. Do not edit.
server {{
    listen {t.public_port};
    listen [::]:{t.public_port};
    server_tokens off;
    client_max_body_size 0;

    location / {{
        proxy_pass http://127.0.0.1:{host_port};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade_dahlia;
        proxy_read_timeout 3600s;
        proxy_buffering off;
    }}
}}
"""


NGINX_MAP = """# Generated by dahlia-apps. WebSocket upgrade for app proxies.
map $http_upgrade $connection_upgrade_dahlia {
    default upgrade;
    ''      close;
}
"""


# --- state --------------------------------------------------------------------


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write_file(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)


def installed(paths: Paths) -> list[str]:
    if not paths.apps.is_dir():
        return []
    return sorted(p.name for p in paths.apps.iterdir() if (p / "state.json").is_file())


def load_installed(paths: Paths, app_id: str) -> tuple[Template, dict]:
    d = paths.app_dir(app_id)
    state = read_json(d / "state.json")
    if state is None:
        raise AppError(f"app {app_id} is not installed")
    return parse_template((d / "app.yaml").read_text(), app_id), state


def used_ports(paths: Paths, skip: str | None = None) -> tuple[set[int], set[int]]:
    """Host ports and public ports taken by installed apps."""
    host, public = set(), set()
    for app_id in installed(paths):
        if app_id == skip:
            continue
        t, state = load_installed(paths, app_id)
        if state.get("host_port"):
            host.add(state["host_port"])
        if t.public_port:
            public.add(t.public_port)
        public.update(p.host for p in t.lan_ports)
    return host, public


def port_free(port: int, protocol: str = "tcp") -> bool:
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as s:
        try:
            # Probe only: is the port free on all interfaces?
            s.bind(("0.0.0.0", port))  # noqa: S104
        except OSError:
            return False
    return True


def pick_host_port(taken: set[int], free=port_free) -> int:
    for port in HOST_PORTS:
        if port not in taken and free(port):
            return port
    raise AppError("no free internal port left")


# --- catalog ------------------------------------------------------------------


def catalog_url(paths: Paths) -> str:
    cfg = read_json(paths.config, {}) or {}
    url = cfg.get("catalog", "")
    if not isinstance(url, str) or not url.startswith(("https://", "file:///")):
        raise AppError(f"catalog in {paths.config} must be an https:// or file:/// URL")
    return url if url.endswith("/") else url + "/"


def fetch(url: str) -> str:
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme not in ("https", "file"):
        raise AppError(f"refusing to fetch {url}")
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as r:  # noqa: S310 - scheme checked
            data = r.read(MAX_FILE + 1)
    except OSError as e:
        raise AppError(f"download failed: {url}: {e}") from None
    if len(data) > MAX_FILE:
        raise AppError(f"{url} is larger than {MAX_FILE} bytes")
    return data.decode()


def refresh_catalog(paths: Paths, fetcher=fetch) -> list[Template]:
    base = catalog_url(paths)
    try:
        index = json.loads(fetcher(base + "index.json"))
    except json.JSONDecodeError:
        raise AppError("catalog index.json is not valid JSON") from None
    ids = index.get("apps") if isinstance(index, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) and ID_RE.fullmatch(i) for i in ids):
        raise AppError("catalog index.json must contain {\"apps\": [ids]}")
    templates, texts = [], {}
    for app_id in ids:
        text = fetcher(f"{base}apps/{app_id}.yaml")
        templates.append(parse_template(text, app_id))
        texts[app_id] = text
    # Replace the cache only after everything validated.
    new = paths.catalog.with_name(paths.catalog.name + ".new")
    if new.exists():
        for f in new.iterdir():
            f.unlink()
    new.mkdir(parents=True, exist_ok=True)
    for app_id, text in texts.items():
        (new / f"{app_id}.yaml").write_text(text)
    old = paths.catalog.with_name(paths.catalog.name + ".old")
    if paths.catalog.exists():
        paths.catalog.replace(old)
    new.replace(paths.catalog)
    if old.exists():
        for f in old.iterdir():
            f.unlink()
        old.rmdir()
    return templates


def cached_catalog(paths: Paths) -> list[Template]:
    if not paths.catalog.is_dir():
        return []
    return [
        parse_template(f.read_text(), f.stem)
        for f in sorted(paths.catalog.glob("*.yaml"))
        if ID_RE.fullmatch(f.stem)
    ]


# --- docker -------------------------------------------------------------------


PLUGIN_DIRS = ("/usr/libexec/docker/cli-plugins", "/usr/lib/docker/cli-plugins")


def compose_command() -> list[str]:
    """`docker compose` (plugin) or the standalone docker-compose v2 binary."""
    if any(Path(d, "docker-compose").exists() for d in PLUGIN_DIRS):
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    raise AppError("docker compose is not installed")


class Docker:
    """docker compose calls. Tests replace it."""

    def compose(self, app_dir: Path, *args: str, timeout: int = 1800) -> str:
        cmd = [*compose_command(), "-f", str(app_dir / "compose.json"), *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise AppError(f"docker compose {args[0]} timed out") from None
        if proc.returncode != 0:
            lines = [ln for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
            raise AppError(f"docker compose {args[0]}: {lines[-1] if lines else proc.returncode}")
        return proc.stdout

    def state(self, app_id: str) -> str:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", f"dahlia-{app_id}"],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "missing"


def reload_nginx() -> None:
    if not Path("/usr/sbin/nginx").exists():
        return
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if test.returncode != 0:
        raise AppError(f"nginx configuration invalid: {test.stderr.strip().splitlines()[-1]}")
    subprocess.run(["systemctl", "reload-or-restart", "nginx.service"], check=False,
                   capture_output=True)


@dataclass
class Apps:
    paths: Paths = field(default_factory=Paths)
    docker: Docker = field(default_factory=Docker)
    host: Host | None = None
    nginx_reload: object = reload_nginx
    port_check: object = port_free
    mounted: object = os.path.ismount

    def _host(self) -> Host:
        if self.host is None:
            self.host = Host.detect()
        return self.host

    def _require_pool(self) -> None:
        if not self.mounted(self.paths.pool):
            raise AppError(f"{self.paths.pool} is not mounted; add a data disk first")

    def _write_proxy(self, t: Template, host_port: int | None) -> None:
        conf = self.paths.nginx / f"dahlia-app-{t.id}.conf"
        if t.public_port is None or host_port is None:
            conf.unlink(missing_ok=True)
        else:
            write_file(self.paths.nginx / "dahlia-apps-map.conf", NGINX_MAP)
            write_file(conf, nginx_conf(t, host_port))
        self.nginx_reload()

    def _prepare_dirs(self, t: Template) -> None:
        host = self._host()
        for v in t.volumes:
            src = volume_source(t, v, self.paths)
            if v.appdata is not None:
                for d in (self.paths.pool / "appdata", self.paths.pool / "appdata" / t.id, src):
                    if not d.exists():
                        d.mkdir(mode=0o775)
                        os.chown(d, host.uid, host.gid)
            elif not src.exists():
                src.mkdir(mode=0o2775)
                os.chown(src, 0, host.gid)

    def catalog(self, refresh: bool = False) -> list[dict]:
        templates = refresh_catalog(self.paths) if refresh else cached_catalog(self.paths)
        have = set(installed(self.paths))
        return [{**t.summary(), "installed": t.id in have} for t in templates]

    def install(self, app_id: str) -> dict:
        """Install from the cached catalog and start the app."""
        self._require_pool()
        d = self.paths.app_dir(app_id)
        if (d / "state.json").exists():
            raise AppError(f"{app_id} is already installed")
        src = self.paths.catalog / f"{app_id}.yaml"
        if not src.is_file():
            raise AppError(f"{app_id} is not in the catalog")
        text = src.read_text()
        t = parse_template(text, app_id)
        taken_host, taken_public = used_ports(self.paths)
        wanted = ([t.public_port] if t.public_port else []) + [p.host for p in t.lan_ports]
        for port in wanted:
            if port in taken_public:
                raise AppError(f"port {port} is used by another app")
        host_port = pick_host_port(taken_host, self.port_check) if t.web_port else None
        self._prepare_dirs(t)
        d.mkdir(parents=True, exist_ok=True)
        write_file(d / "app.yaml", text)
        write_file(d / "compose.json",
                   json.dumps(compose(t, host_port, self.paths, self._host()), indent=2))
        state = {"host_port": host_port, "enabled": True}
        write_file(d / "state.json", json.dumps(state))
        try:
            self.docker.compose(d, "up", "-d", "--pull", "missing", "--remove-orphans")
        except AppError:
            # Keep nothing half-installed; app data on the array stays.
            with contextlib.suppress(AppError):
                self.docker.compose(d, "down")
            for name in ("compose.json", "app.yaml", "state.json"):
                (d / name).unlink(missing_ok=True)
            d.rmdir()
            raise
        self._write_proxy(t, host_port)
        return {"id": app_id, **state}

    def update(self, app_id: str) -> dict:
        """Take the catalog's current template and pull the image."""
        t_old, state = load_installed(self.paths, app_id)
        src = self.paths.catalog / f"{app_id}.yaml"
        if not src.is_file():
            src = self.paths.app_dir(app_id) / "app.yaml"
        text = src.read_text()
        t = parse_template(text, app_id)
        if t.public_port != t_old.public_port or [vars(p) for p in t.lan_ports] != [
            vars(p) for p in t_old.lan_ports
        ]:
            _, taken_public = used_ports(self.paths, skip=app_id)
            for port in ([t.public_port] if t.public_port else []) + [p.host for p in t.lan_ports]:
                if port in taken_public:
                    raise AppError(f"port {port} is used by another app")
        host_port = state.get("host_port")
        if t.web_port and not host_port:
            host_port = pick_host_port(used_ports(self.paths, skip=app_id)[0], self.port_check)
        self._require_pool()
        self._prepare_dirs(t)
        d = self.paths.app_dir(app_id)
        write_file(d / "app.yaml", text)
        write_file(d / "compose.json",
                   json.dumps(compose(t, host_port, self.paths, self._host()), indent=2))
        state = {**state, "host_port": host_port}
        write_file(d / "state.json", json.dumps(state))
        self.docker.compose(d, "pull")
        if state.get("enabled", True):
            self.docker.compose(d, "up", "-d", "--remove-orphans")
        self._write_proxy(t, host_port)
        return {"id": app_id, **state}

    def _set_enabled(self, app_id: str, enabled: bool) -> dict:
        _, state = load_installed(self.paths, app_id)
        state["enabled"] = enabled
        write_file(self.paths.app_dir(app_id) / "state.json", json.dumps(state))
        return state

    def start(self, app_id: str) -> dict:
        self._require_pool()
        load_installed(self.paths, app_id)
        self.docker.compose(self.paths.app_dir(app_id), "up", "-d")
        return {"id": app_id, **self._set_enabled(app_id, True)}

    def stop(self, app_id: str) -> dict:
        load_installed(self.paths, app_id)
        self.docker.compose(self.paths.app_dir(app_id), "stop")
        return {"id": app_id, **self._set_enabled(app_id, False)}

    def remove(self, app_id: str, delete_data: bool = False) -> dict:
        t, _ = load_installed(self.paths, app_id)
        d = self.paths.app_dir(app_id)
        self.docker.compose(d, "down", "--rmi", "all")
        for name in ("compose.json", "app.yaml", "state.json"):
            (d / name).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            d.rmdir()
        self._write_proxy(t, None)
        if delete_data:
            data = self.paths.pool / "appdata" / app_id
            if data.is_dir() and self.mounted(self.paths.pool):
                subprocess.run(["rm", "-rf", "--one-file-system", "--", str(data)], check=True)
        return {"id": app_id, "data_deleted": delete_data}

    def list(self) -> list[dict]:
        out = []
        for app_id in installed(self.paths):
            t, state = load_installed(self.paths, app_id)
            out.append({
                **t.summary(),
                "enabled": state.get("enabled", True),
                "status": self.docker.state(app_id),
            })
        return out

    def start_enabled(self) -> int:
        """Boot: start every enabled app. Returns the number of failures."""
        failures = 0
        if not self.mounted(self.paths.pool):
            print(f"dahlia-apps: {self.paths.pool} not mounted, apps stay stopped", file=sys.stderr)
            return 1
        for app_id in installed(self.paths):
            _, state = load_installed(self.paths, app_id)
            if not state.get("enabled", True):
                continue
            try:
                self.docker.compose(self.paths.app_dir(app_id), "up", "-d")
                print(f"dahlia-apps: started {app_id}")
            except AppError as e:
                failures += 1
                print(f"dahlia-apps: {app_id}: {e}", file=sys.stderr)
        return failures

    def stop_all(self) -> None:
        for app_id in installed(self.paths):
            with contextlib.suppress(AppError):
                self.docker.compose(self.paths.app_dir(app_id), "stop")


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dahlia-apps", description="Manage Dahlia apps.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("catalog", help="show the catalog")
    s.add_argument("--refresh", action="store_true", help="download it first")
    sub.add_parser("list", help="installed apps")
    for name in ("install", "update", "start", "stop"):
        sub.add_parser(name).add_argument("id")
    s = sub.add_parser("remove")
    s.add_argument("id")
    s.add_argument("--delete-data", action="store_true",
                   help="also delete /mnt/user/appdata/<id>")
    sub.add_parser("start-enabled", help="boot: start all enabled apps")
    sub.add_parser("stop-all", help="shutdown: stop all apps")
    s = sub.add_parser("check", help="validate a template file")
    s.add_argument("file")
    args = p.parse_args(argv)

    apps = Apps()
    try:
        if args.command == "catalog":
            result = apps.catalog(refresh=args.refresh)
        elif args.command == "list":
            result = apps.list()
        elif args.command == "remove":
            result = apps.remove(args.id, delete_data=args.delete_data)
        elif args.command == "start-enabled":
            return 1 if apps.start_enabled() else 0
        elif args.command == "stop-all":
            apps.stop_all()
            return 0
        elif args.command == "check":
            result = parse_template(Path(args.file).read_text()).summary()
        else:
            result = getattr(apps, args.command)(args.id)
    except AppError as e:
        print(f"dahlia-apps: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0
