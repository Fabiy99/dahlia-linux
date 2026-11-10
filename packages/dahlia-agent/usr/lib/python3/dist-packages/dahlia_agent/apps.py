"""Apps: Docker containers from Dahlia app templates.

A template (YAML) describes one app. Schema 2 allows a small stack: the main
service "app" plus helpers such as a database, installed, updated and
removed together. Only the fields below exist; everything else is rejected,
so a template cannot ask for privileged mode, host networking, devices or
extra capabilities.

    schema: 2
    id: paperless
    name: Paperless-ngx
    category: productivity               # see CATEGORIES
    description: {de: "...", en: "..."}
    website: https://docs.paperless-ngx.com/   # optional
    notes: {de: "...", en: "..."}       # optional, shown after install
    web: {port: 8000, public_port: 8010} # optional, on service "app"
    login: {user: admin, password: "{SECRET_ADMIN}"}  # optional, shown in the UI
    services:
      app:                               # required, container dahlia-<id>
        image: ghcr.io/paperless-ngx/paperless-ngx:2.18.4   # fixed tag, never "latest"
        depends_on: [db, redis]
        lan_ports:                       # optional, reachable from the LAN
          - {container: 7359, host: 7359, protocol: udp}
        volumes:
          - {container: /data, appdata: data}           # /mnt/user/appdata/<id>/data
          - {container: /media, share: media, readonly: true}  # /mnt/user/media
        environment: {TZ: "{TZ}", PAPERLESS_DBPASS: "{SECRET_DB}"}
        command: [--flag, value]         # optional
        user: "{PUID}:{PGID}"            # optional
      db:                                # container dahlia-<id>-db, host name "db"
        image: postgres:17.6
        environment: {POSTGRES_PASSWORD: "{SECRET_DB}"}
        volumes:
          - {container: /var/lib/postgresql/data, appdata: db}

Schema 1 is the single-container form (image, lan_ports, volumes and
environment at the top level); it is read as a stack with only "app".

Placeholders in environment, command and login: {TZ}, {PUID}, {PGID},
{HOSTNAME}, {DOMAIN} and {SECRET_<NAME>}. Secrets are random, made at
install and kept in secrets.json, so they stay the same across updates.

The web port is published on 127.0.0.1 only (a port from HOST_PORTS);
nginx listens on public_port in the LAN and on https://<id>.<domain>
(see domains.py) and proxies to it. Helpers publish nothing unless they
list lan_ports; the services of a stack reach each other by service name
on the stack's own network. Data lives on the array under /mnt/user, never
on the system SSD. Containers are not restarted by Docker itself:
dahlia-apps.service starts the enabled apps after the disks are mounted.

Updates: the catalog is downloaded again by dahlia-apps-catalog.timer.
An installed app has an update when its template differs from the
catalog's; tags are pinned, so a new version is a new template.

Layout:
    /etc/dahlia/apps.json                    catalog URL
    /var/lib/dahlia/apps-catalog/            last downloaded catalog, meta.json
    /var/lib/dahlia/apps/<id>/app.yaml       template the app was installed from
    /var/lib/dahlia/apps/<id>/compose.json   generated, used by docker compose
    /var/lib/dahlia/apps/<id>/state.json     host port, enabled flag
    /var/lib/dahlia/apps/<id>/secrets.json   generated secrets (0600)
"""

import argparse
import contextlib
import datetime
import grp
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import domains

CONFIG_FILE = Path("/etc/dahlia/apps.json")
APPS_DIR = Path("/var/lib/dahlia/apps")
CATALOG_DIR = Path("/var/lib/dahlia/apps-catalog")
USER_POOL = Path("/mnt/user")
APPS_USER = "dahlia-apps"
APPS_GROUP = "dahlia"

# Kept out of the ephemeral port pool by sysctl.d/50-dahlia-apps.conf.
HOST_PORTS = range(41000, 42000)
# Ports Dahlia itself uses or that would break the system when taken.
RESERVED_PORTS = {22, 80, 443, 5353, 8080}
# Not a web port, but a LAN port for a DNS server app (AdGuard Home, Pi-hole).
RESERVED_PUBLIC = RESERVED_PORTS | {53}
MAX_FILE = 256 * 1024
MAX_BUNDLE = 16 * 1024 * 1024
DOWNLOAD_TIMEOUT = 30
SCHEMAS = (1, 2)
BUNDLE_FORMAT = 1
MAX_SERVICES = 8
MAIN = "app"

CATEGORIES = (
    "media", "downloads", "photos", "books", "cloud", "productivity", "home", "network",
    "security", "monitoring", "communication", "development", "tools", "other",
)

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
NAME_RE = re.compile(r"^[\w .+-]{1,40}$")
SERVICE_RE = re.compile(r"^[a-z][a-z0-9-]{0,20}$")
SUBDIR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# registry/name:tag or name:tag@sha256:..., tag required.
IMAGE_RE = re.compile(
    r"^(?:[a-z0-9.-]+(?::[0-9]+)?/)?[a-z0-9]+(?:[._/-][a-z0-9]+)*"
    r":[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}(?:@sha256:[0-9a-f]{64})?$"
)
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
SECRET_RE = re.compile(r"^SECRET_[A-Z0-9_]{1,32}$")
FIXED_PLACEHOLDERS = ("TZ", "PUID", "PGID", "HOSTNAME", "DOMAIN")
USER_RE = re.compile(r"^(?:\{PUID\}|[0-9]{1,6})(?::(?:\{PGID\}|[0-9]{1,6}))?$")
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~%/?#=&+-]*)?$")
PROTOCOLS = ("tcp", "udp")


class AppError(Exception):
    pass


class NotFound(AppError):
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
class Service:
    name: str
    image: str
    lan_ports: list[LanPort] = field(default_factory=list)
    volumes: list[Volume] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    command: list[str] | None = None
    user: str | None = None
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Template:
    id: str
    name: str
    description: dict[str, str]
    services: dict[str, Service]
    schema: int = 2
    category: str = "other"
    website: str | None = None
    notes: dict[str, str] = field(default_factory=dict)
    login: dict[str, str] = field(default_factory=dict)
    web_port: int | None = None
    public_port: int | None = None

    @property
    def image(self) -> str:
        return self.services[MAIN].image

    @property
    def version(self) -> str:
        return image_tag(self.image)

    @property
    def lan_ports(self) -> list[LanPort]:
        return [p for s in self.services.values() for p in s.lan_ports]

    @property
    def volumes(self) -> list[Volume]:
        return [v for s in self.services.values() for v in s.volumes]

    @property
    def secrets(self) -> set[str]:
        texts = list(self.login.values())
        for s in self.services.values():
            texts += list(s.environment.values()) + (s.command or [])
        return {n for text in texts for n in PLACEHOLDER_RE.findall(text) if SECRET_RE.fullmatch(n)}

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "website": self.website,
            "notes": self.notes,
            "image": self.image,
            "version": self.version,
            "services": [
                {"name": s.name, "image": s.image} for s in self.services.values()
            ],
            "public_port": self.public_port,
            "lan_ports": [vars(p) for p in self.lan_ports],
            "shares": self.share_list(),
        }

    def share_list(self) -> list[dict]:
        """Shares the app mounts, each with its container paths, for the install dialog."""
        out: dict[str, dict] = {}
        for v in self.volumes:
            if v.share is None:
                continue
            e = out.setdefault(v.share, {"name": v.share, "containers": [], "readonly": True})
            if v.container not in e["containers"]:
                e["containers"].append(v.container)
            e["readonly"] = e["readonly"] and v.readonly
        return list(out.values())


def image_tag(image: str) -> str:
    return image.split("@")[0].rsplit(":", 1)[1]


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


def _text(value, where: str, limit: int = 1000) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise AppError(f"{where} must be a string or number")
    value = str(value)
    if len(value) > limit or "\0" in value:
        raise AppError(f"{where} is too long")
    for name in PLACEHOLDER_RE.findall(value):
        if name not in FIXED_PLACEHOLDERS and not SECRET_RE.fullmatch(name):
            raise AppError(f"{where}: unknown placeholder {{{name}}}")
    return value


def _texts(obj, where: str, limit: int = 500) -> dict[str, str]:
    _fields(obj, {"de", "en"}, set(), where)
    if not all(isinstance(v, str) and len(v) <= limit for v in obj.values()):
        raise AppError(f"{where} texts must be strings up to {limit} characters")
    return dict(obj)


def _image(value, where: str) -> str:
    if not isinstance(value, str) or not IMAGE_RE.fullmatch(value):
        raise AppError(f"{where} must be name:tag")
    if image_tag(value) == "latest":
        raise AppError(f"{where}: tag 'latest' is not allowed, pin a version")
    return value


def template_schema(text: str):
    """The schema number of a template, without validating anything else."""
    with contextlib.suppress(yaml.YAMLError):
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data.get("schema")
    return None


def _service(name: str, data: dict, where: str) -> Service:
    s = Service(name=name, image=_image(data["image"], f"{where}.image"))
    for i, p in enumerate(data.get("lan_ports") or []):
        w = f"{where}.lan_ports[{i}]"
        _fields(p, {"container", "host", "protocol"}, {"container", "host"}, w)
        lp = LanPort(_port(p["container"], f"{w}.container"), _port(p["host"], f"{w}.host"),
                     p.get("protocol", "tcp"))
        if lp.protocol not in PROTOCOLS:
            raise AppError(f"{w}.protocol must be tcp or udp")
        if lp.host in RESERVED_PORTS or lp.host in HOST_PORTS:
            raise AppError(f"{w}.host {lp.host} is reserved")
        s.lan_ports.append(lp)

    targets = set()
    for i, v in enumerate(data.get("volumes") or []):
        w = f"{where}.volumes[{i}]"
        _fields(v, {"container", "appdata", "share", "readonly"}, {"container"}, w)
        vol = Volume(_container_path(v["container"], f"{w}.container"))
        if ("appdata" in v) == ("share" in v):
            raise AppError(f"{w} needs exactly one of appdata or share")
        for key in ("appdata", "share"):
            if key in v:
                if not isinstance(v[key], str) or not SUBDIR_RE.fullmatch(v[key]):
                    raise AppError(f"{w}.{key} must be a simple directory name")
                if key == "share" and v[key] == "appdata":
                    raise AppError(f"{w}: appdata is not a share")
                setattr(vol, key, v[key])
        ro = v.get("readonly", False)
        if type(ro) is not bool:
            raise AppError(f"{w}.readonly must be true or false")
        vol.readonly = ro
        if vol.container in targets:
            raise AppError(f"{w}: {vol.container} is mounted twice")
        targets.add(vol.container)
        s.volumes.append(vol)

    env = data.get("environment") or {}
    if not isinstance(env, dict):
        raise AppError(f"{where}.environment must be a mapping")
    for key, value in env.items():
        if not isinstance(key, str) or not ENV_KEY_RE.fullmatch(key):
            raise AppError(f"{where}.environment key {key!r} is invalid")
        s.environment[key] = _text(value, f"{where}.environment {key}")

    if "command" in data:
        cmd = data["command"]
        if not isinstance(cmd, list) or not 1 <= len(cmd) <= 32:
            raise AppError(f"{where}.command must be a list of 1 to 32 arguments")
        s.command = [_text(a, f"{where}.command[{i}]", 500) for i, a in enumerate(cmd)]

    if "user" in data:
        if not isinstance(data["user"], str) or not USER_RE.fullmatch(data["user"]):
            raise AppError(f"{where}.user must be uid[:gid] or {{PUID}}:{{PGID}}")
        s.user = data["user"]

    deps = data.get("depends_on") or []
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise AppError(f"{where}.depends_on must be a list of service names")
    s.depends_on = list(deps)
    return s


def _check_dependencies(services: dict[str, Service]) -> None:
    for s in services.values():
        for dep in s.depends_on:
            if dep not in services or dep == s.name:
                raise AppError(f"services.{s.name}.depends_on: unknown service {dep}")
    done: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            raise AppError(f"services: dependency loop {' -> '.join((*path, name))}")
        if name in done:
            return
        for dep in services[name].depends_on:
            visit(dep, (*path, name))
        done.add(name)

    for name in services:
        visit(name, ())


SERVICE_FIELDS = {"image", "lan_ports", "volumes", "environment", "command", "user", "depends_on"}


def parse_template(text: str, expected_id: str | None = None) -> Template:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise AppError(f"template is not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise AppError("template must be a mapping")
    schema = data.get("schema")
    if schema not in SCHEMAS:
        raise AppError("unsupported template schema")
    if schema == 1:
        top = {"schema", "id", "name", "description", "image", "web", "lan_ports", "volumes",
               "environment"}
        _fields(data, top, {"schema", "id", "name", "image"}, "template")
    else:
        top = {"schema", "id", "name", "description", "category", "website", "notes", "web",
               "login", "services"}
        _fields(data, top, {"schema", "id", "name", "description", "category", "services"},
                "template")
    app_id = data["id"]
    if not isinstance(app_id, str) or not ID_RE.fullmatch(app_id):
        raise AppError("id must be 2-31 characters: a-z, 0-9, -")
    if expected_id is not None and app_id != expected_id:
        raise AppError(f"template id {app_id} does not match {expected_id}")
    if not isinstance(data["name"], str) or not NAME_RE.fullmatch(data["name"]):
        raise AppError("name is invalid")
    desc = _texts(data.get("description") or {}, "description")

    if schema == 1:
        main = {k: data[k] for k in ("image", "lan_ports", "volumes", "environment") if k in data}
        services = {MAIN: _service(MAIN, main, "template")}
        t = Template(id=app_id, name=data["name"], description=desc, services=services,
                     schema=1)
    else:
        if data["category"] not in CATEGORIES:
            raise AppError(f"category must be one of: {', '.join(CATEGORIES)}")
        raw = data["services"]
        if not isinstance(raw, dict) or MAIN not in raw:
            raise AppError("services must be a mapping with a service 'app'")
        if len(raw) > MAX_SERVICES:
            raise AppError(f"at most {MAX_SERVICES} services")
        services = {}
        # The main service first, the helpers in template order.
        for name in [MAIN] + [n for n in raw if n != MAIN]:
            if not isinstance(name, str) or not SERVICE_RE.fullmatch(name):
                raise AppError(f"service name {name!r} is invalid")
            where = f"services.{name}"
            services[name] = _service(name, _fields(raw[name], SERVICE_FIELDS, {"image"}, where),
                                      where)
        _check_dependencies(services)
        t = Template(id=app_id, name=data["name"], description=desc, services=services,
                     category=data["category"])
        if "website" in data:
            if not isinstance(data["website"], str) or not URL_RE.fullmatch(data["website"]):
                raise AppError("website must be an https:// URL")
            t.website = data["website"]
        if "notes" in data:
            t.notes = _texts(data["notes"], "notes", 1000)
        if "login" in data:
            login = _fields(data["login"], {"user", "password"}, set(), "login")
            t.login = {k: _text(v, f"login.{k}", 200) for k, v in login.items()}

    if "web" in data:
        web = _fields(data["web"], {"port", "public_port"}, {"port", "public_port"}, "web")
        t.web_port = _port(web["port"], "web.port")
        t.public_port = _port(web["public_port"], "web.public_port")
        if t.public_port in RESERVED_PUBLIC or t.public_port in HOST_PORTS:
            raise AppError(f"web.public_port {t.public_port} is reserved")

    seen = set()
    for p in t.lan_ports:
        if p.host == t.public_port:
            raise AppError(f"lan port {p.host} is the web port")
        if (p.host, p.protocol) in seen:
            raise AppError(f"lan port {p.host}/{p.protocol} is used twice")
        seen.add((p.host, p.protocol))
    return t


# --- system facts -------------------------------------------------------------


@dataclass
class Host:
    """Values filled into templates. Tests use fixed ones."""

    tz: str
    uid: int
    gid: int
    hostname: str = "dahlia"
    domain: str = domains.DEFAULT_DOMAIN

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
        return cls(tz=tz or "Etc/UTC", uid=uid, gid=gid, hostname=socket.gethostname())


@dataclass
class Paths:
    apps: Path = APPS_DIR
    catalog: Path = CATALOG_DIR
    pool: Path = USER_POOL
    config: Path = CONFIG_FILE

    def app_dir(self, app_id: str) -> Path:
        if not ID_RE.fullmatch(app_id):
            raise AppError("invalid app id")
        return self.apps / app_id


# --- compose --------------------------------------------------------


def fill(value: str, host: Host, secret_values: dict[str, str] | None = None) -> str:
    fixed = {"TZ": host.tz, "PUID": str(host.uid), "PGID": str(host.gid),
             "HOSTNAME": host.hostname, "DOMAIN": host.domain}
    values = secret_values or {}

    def one(m: re.Match) -> str:
        name = m.group(1)
        if name in fixed:
            return fixed[name]
        if name in values:
            return values[name]
        raise AppError(f"no value for {{{name}}}")

    return PLACEHOLDER_RE.sub(one, value)


def new_secrets(t: Template, existing: dict[str, str]) -> dict[str, str]:
    """Keep the secrets an app has, add the ones its template now needs."""
    out = dict(existing)
    for name in sorted(t.secrets - set(existing)):
        out[name] = secrets.token_hex(16)
    return out


def container_name(app_id: str, service: str) -> str:
    return f"dahlia-{app_id}" if service == MAIN else f"dahlia-{app_id}-{service}"


# A folder below /mnt/user chosen for a share: path segments without "..".
FOLDER_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9 ._()+,-]{0,99}"
FOLDER_RE = re.compile(rf"^{FOLDER_SEGMENT}(/{FOLDER_SEGMENT}){{0,9}}$")


def check_folders(t: Template, folders: dict[str, str] | None, pool: Path) -> dict[str, str]:
    """Folders chosen in the install dialog: share name -> path relative to the pool."""
    folders = dict(folders or {})
    shares = {v.share for v in t.volumes if v.share is not None}
    for share, rel in folders.items():
        if share not in shares:
            raise AppError(f"{t.id} has no share {share}")
        if not isinstance(rel, str) or not FOLDER_RE.match(rel) or ".." in rel.split("/"):
            raise AppError(f"folder for {share}: not a path below {pool}")
        if rel.split("/")[0] == "appdata":
            raise AppError("app data folders cannot be given to another app")
        real = (pool / rel).resolve()
        if real != pool.resolve() and pool.resolve() not in real.parents:
            raise AppError(f"folder for {share} leads outside {pool}")
    return {k: v for k, v in folders.items() if v != k}


def volume_source(t: Template, v: Volume, paths: Paths,
                  folders: dict[str, str] | None = None) -> Path:
    if v.appdata is not None:
        return paths.pool / "appdata" / t.id / v.appdata
    return paths.pool / (folders or {}).get(v.share, v.share)


def compose(t: Template, host_port: int | None, paths: Paths, host: Host,
            secret_values: dict[str, str] | None = None,
            folders: dict[str, str] | None = None) -> dict:
    services = {}
    for s in t.services.values():
        ports = []
        if s.name == MAIN and t.web_port is not None and host_port is not None:
            ports.append(f"127.0.0.1:{host_port}:{t.web_port}/tcp")
        for p in s.lan_ports:
            ports.append(f"0.0.0.0:{p.host}:{p.container}/{p.protocol}")
        svc = {
            "image": s.image,
            "container_name": container_name(t.id, s.name),
            # dahlia-apps.service starts enabled apps once the array is mounted.
            "restart": "no",
            "security_opt": ["no-new-privileges:true"],
            "environment": {k: fill(v, host, secret_values) for k, v in s.environment.items()},
            "volumes": [
                {
                    "type": "bind",
                    "source": str(volume_source(t, v, paths, folders)),
                    "target": v.container,
                    "read_only": v.readonly,
                    "bind": {"create_host_path": False},
                }
                for v in s.volumes
            ],
            "logging": {"driver": "local", "options": {"max-size": "10m", "max-file": "3"}},
            "labels": {"org.dahlia.app": t.id, "org.dahlia.service": s.name},
        }
        if ports:
            svc["ports"] = ports
        if s.command is not None:
            svc["command"] = [fill(a, host, secret_values) for a in s.command]
        if s.user is not None:
            svc["user"] = fill(s.user, host)
        if s.depends_on:
            svc["depends_on"] = list(s.depends_on)
        services[s.name] = svc
    return {"name": f"dahlia-{t.id}", "services": services}


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


APP_FILES = ("compose.json", "app.yaml", "state.json", "secrets.json")


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


def proxies(paths: Paths) -> list[domains.AppProxy]:
    """Installed apps with a web UI, for nginx and mDNS."""
    out = []
    for app_id in installed(paths):
        t, state = load_installed(paths, app_id)
        if t.public_port and state.get("host_port"):
            out.append(domains.AppProxy(app_id, state["host_port"], t.public_port))
    return out


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


def update_info(current: Template, offered: Template | None) -> dict | None:
    """What the catalog's template would change, or None."""
    if offered is None or offered == current:
        return None
    images = []
    for name, s in offered.services.items():
        old = current.services.get(name)
        if old is None or old.image != s.image:
            images.append({"service": name, "from": old.image if old else None, "to": s.image})
    for name, s in current.services.items():
        if name not in offered.services:
            images.append({"service": name, "from": s.image, "to": None})
    return {"version": offered.version, "images": images}


# --- catalog ------------------------------------------------------------------


def catalog_url(paths: Paths) -> str:
    cfg = read_json(paths.config, {}) or {}
    url = cfg.get("catalog", "")
    if not isinstance(url, str) or not url.startswith(("https://", "file:///")):
        raise AppError(f"catalog in {paths.config} must be an https:// or file:/// URL")
    return url if url.endswith("/") else url + "/"


def fetch(url: str, limit: int = MAX_FILE) -> str:
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme not in ("https", "file"):
        raise AppError(f"refusing to fetch {url}")
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as r:  # noqa: S310 - scheme checked
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound(f"not found: {url}") from None
        raise AppError(f"download failed: {url}: {e}") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, FileNotFoundError):
            raise NotFound(f"not found: {url}") from None
        raise AppError(f"download failed: {url}: {e}") from None
    except OSError as e:
        raise AppError(f"download failed: {url}: {e}") from None
    if len(data) > limit:
        raise AppError(f"{url} is larger than {limit} bytes")
    return data.decode()


def _catalog_texts(base: str, fetcher) -> dict[str, str]:
    """Template texts by id: catalog.json (one file), else index.json + apps/<id>.yaml."""
    try:
        bundle = fetcher(base + "catalog.json", MAX_BUNDLE)
    except NotFound:
        bundle = None
    if bundle is not None:
        try:
            data = json.loads(bundle)
        except json.JSONDecodeError:
            raise AppError("catalog.json is not valid JSON") from None
        if not isinstance(data, dict) or data.get("format") != BUNDLE_FORMAT:
            raise AppError("catalog.json has an unknown format")
        texts = data.get("apps")
        if not isinstance(texts, dict) or not all(
            isinstance(k, str) and ID_RE.fullmatch(k) and isinstance(v, str) and len(v) <= MAX_FILE
            for k, v in texts.items()
        ):
            raise AppError("catalog.json must contain {\"apps\": {id: template}}")
        return dict(sorted(texts.items()))
    try:
        index = json.loads(fetcher(base + "index.json", MAX_FILE))
    except json.JSONDecodeError:
        raise AppError("catalog index.json is not valid JSON") from None
    ids = index.get("apps") if isinstance(index, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) and ID_RE.fullmatch(i) for i in ids):
        raise AppError("catalog index.json must contain {\"apps\": [ids]}")
    return {app_id: fetcher(f"{base}apps/{app_id}.yaml", MAX_FILE) for app_id in ids}


def _clear(d: Path) -> None:
    if d.exists():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()


def refresh_catalog(paths: Paths, fetcher=fetch) -> list[Template]:
    base = catalog_url(paths)
    templates, texts, skipped = [], {}, []
    for app_id, text in _catalog_texts(base, fetcher).items():
        schema = template_schema(text)
        # Templates for a newer Dahlia are left out, not an error.
        if type(schema) is int and schema > max(SCHEMAS):
            skipped.append(app_id)
            continue
        templates.append(parse_template(text, app_id))
        texts[app_id] = text
    # Replace the cache only after everything validated.
    new = paths.catalog.with_name(paths.catalog.name + ".new")
    _clear(new)
    new.mkdir(parents=True)
    for app_id, text in texts.items():
        (new / f"{app_id}.yaml").write_text(text)
    meta = {
        "refreshed": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": base,
        "skipped": skipped,
    }
    (new / "meta.json").write_text(json.dumps(meta) + "\n")
    old = paths.catalog.with_name(paths.catalog.name + ".old")
    _clear(old)
    if paths.catalog.exists():
        paths.catalog.replace(old)
    new.replace(paths.catalog)
    _clear(old)
    return templates


def cached_catalog(paths: Paths) -> list[Template]:
    if not paths.catalog.is_dir():
        return []
    return [
        parse_template(f.read_text(), f.stem)
        for f in sorted(paths.catalog.glob("*.yaml"))
        if ID_RE.fullmatch(f.stem)
    ]


def cached_template(paths: Paths, app_id: str) -> Template | None:
    f = paths.catalog / f"{app_id}.yaml"
    if not f.is_file():
        return None
    try:
        return parse_template(f.read_text(), app_id)
    except AppError:
        return None


def catalog_meta(paths: Paths) -> dict:
    return read_json(paths.catalog / "meta.json", {}) or {}


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

    def state(self, container: str) -> str:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", container],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "missing"

    def remove_image(self, image: str) -> None:
        """Best effort: an image another app still uses stays."""
        subprocess.run(["docker", "image", "rm", image], capture_output=True, timeout=120,
                       stdin=subprocess.DEVNULL, check=False)


def chown(path: Path, uid: int, gid: int) -> None:
    # Only root can hand files to other users; tests run unprivileged.
    if os.geteuid() == 0:
        os.chown(path, uid, gid)


@dataclass
class Apps:
    paths: Paths = field(default_factory=Paths)
    docker: Docker = field(default_factory=Docker)
    host: Host | None = None
    naming: domains.Domains = field(default_factory=domains.Domains)
    port_check: object = port_free
    mounted: object = os.path.ismount

    def _host(self) -> Host:
        if self.host is None:
            self.host = Host.detect()
            with contextlib.suppress(domains.DomainError):
                self.host.domain = self.naming.settings().domain
        return self.host

    def _require_pool(self) -> None:
        if not self.mounted(self.paths.pool):
            raise AppError(f"{self.paths.pool} is not mounted; add a data disk first")

    def _apply_proxies(self) -> None:
        try:
            self.naming.apply(proxies(self.paths))
        except domains.DomainError as e:
            raise AppError(str(e)) from None

    def _prepare_dirs(self, t: Template, folders: dict[str, str] | None = None) -> None:
        host = self._host()
        for v in t.volumes:
            src = volume_source(t, v, self.paths, folders)
            if v.appdata is not None:
                for d in (self.paths.pool / "appdata", self.paths.pool / "appdata" / t.id, src):
                    if not d.exists():
                        d.mkdir(mode=0o775)
                        chown(d, host.uid, host.gid)
            elif not src.exists():
                src.mkdir(mode=0o2775, parents=True)
                chown(src, 0, host.gid)

    def _secrets(self, app_id: str) -> dict[str, str]:
        return read_json(self.paths.app_dir(app_id) / "secrets.json", {}) or {}

    def _write_app(self, t: Template, text: str, host_port: int | None, sec: dict,
                   folders: dict[str, str] | None = None) -> None:
        d = self.paths.app_dir(t.id)
        d.mkdir(parents=True, exist_ok=True)
        write_file(d / "secrets.json", json.dumps(sec), mode=0o600)
        write_file(d / "app.yaml", text)
        write_file(d / "compose.json",
                   json.dumps(compose(t, host_port, self.paths, self._host(), sec, folders),
                              indent=2),
                   mode=0o600)

    def _check_public_ports(self, t: Template) -> None:
        _, taken_public = used_ports(self.paths, skip=t.id)
        for port in ([t.public_port] if t.public_port else []) + [p.host for p in t.lan_ports]:
            if port in taken_public:
                raise AppError(f"port {port} is used by another app")

    def status(self, t: Template) -> str:
        main = self.docker.state(container_name(t.id, MAIN))
        if main == "running" and any(
            self.docker.state(container_name(t.id, n)) != "running" for n in t.services if n != MAIN
        ):
            return "partial"
        return main

    def catalog(self, refresh: bool = False) -> dict:
        templates = refresh_catalog(self.paths) if refresh else cached_catalog(self.paths)
        have = set(installed(self.paths))
        meta = catalog_meta(self.paths)
        return {
            "apps": [{**t.summary(), "installed": t.id in have} for t in templates],
            "refreshed": meta.get("refreshed"),
            "skipped": meta.get("skipped", []),
        }

    def _install_template(self, app_id: str,
                          folders: dict[str, str] | None = None) -> tuple[Template, str]:
        self._require_pool()
        if (self.paths.app_dir(app_id) / "state.json").exists():
            raise AppError(f"{app_id} is already installed")
        src = self.paths.catalog / f"{app_id}.yaml"
        if not src.is_file():
            raise AppError(f"{app_id} is not in the catalog")
        text = src.read_text()
        t = parse_template(text, app_id)
        self._check_public_ports(t)
        check_folders(t, folders, self.paths.pool)
        return t, text

    def precheck(self, op: str, app_id: str, folders: dict[str, str] | None = None) -> dict:
        """Checks an install or update job would fail on, so the caller gets
        the reason right away instead of a failed background job."""
        if op == "install":
            self._install_template(app_id, folders)
        elif op == "update":
            load_installed(self.paths, app_id)
            self._require_pool()
        else:
            raise AppError(f"no precheck for {op}")
        return {"id": app_id, "ok": True}

    def install(self, app_id: str, folders: dict[str, str] | None = None) -> dict:
        """Install from the cached catalog and start the app with all its services."""
        t, text = self._install_template(app_id, folders)
        folders = check_folders(t, folders, self.paths.pool)
        d = self.paths.app_dir(app_id)
        taken_host, _ = used_ports(self.paths)
        host_port = pick_host_port(taken_host, self.port_check) if t.web_port else None
        self._prepare_dirs(t, folders)
        self._write_app(t, text, host_port, new_secrets(t, {}), folders)
        state = {"host_port": host_port, "enabled": True}
        if folders:
            state["folders"] = folders
        write_file(d / "state.json", json.dumps(state))
        try:
            self.docker.compose(d, "up", "-d", "--pull", "missing", "--remove-orphans")
        except AppError:
            # Keep nothing half-installed; app data on the array stays.
            with contextlib.suppress(AppError):
                self.docker.compose(d, "down")
            for name in APP_FILES:
                (d / name).unlink(missing_ok=True)
            d.rmdir()
            raise
        self._apply_proxies()
        return {"id": app_id, **state}

    def update(self, app_id: str) -> dict:
        """Take the catalog's current template and pull the images."""
        t_old, state = load_installed(self.paths, app_id)
        src = self.paths.catalog / f"{app_id}.yaml"
        if not src.is_file():
            src = self.paths.app_dir(app_id) / "app.yaml"
        text = src.read_text()
        t = parse_template(text, app_id)
        if t.public_port != t_old.public_port or [vars(p) for p in t.lan_ports] != [
            vars(p) for p in t_old.lan_ports
        ]:
            self._check_public_ports(t)
        host_port = state.get("host_port")
        if t.web_port and not host_port:
            host_port = pick_host_port(used_ports(self.paths, skip=app_id)[0], self.port_check)
        self._require_pool()
        # Folders of shares the new template no longer has are dropped.
        shares = {v.share for v in t.volumes if v.share is not None}
        folders = {k: v for k, v in (state.get("folders") or {}).items() if k in shares}
        self._prepare_dirs(t, folders)
        d = self.paths.app_dir(app_id)
        self._write_app(t, text, host_port, new_secrets(t, self._secrets(app_id)), folders)
        state = {**state, "host_port": host_port}
        write_file(d / "state.json", json.dumps(state))
        self.docker.compose(d, "pull")
        if state.get("enabled", True):
            self.docker.compose(d, "up", "-d", "--remove-orphans")
        else:
            # Containers of removed services go even while the app is stopped.
            self.docker.compose(d, "up", "--no-start", "--remove-orphans")
        new_images = {s.image for s in t.services.values()}
        for s in t_old.services.values():
            if s.image not in new_images:
                self.docker.remove_image(s.image)
        self._apply_proxies()
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
        self.docker.compose(d, "down")
        for s in t.services.values():
            self.docker.remove_image(s.image)
        for name in APP_FILES:
            (d / name).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            d.rmdir()
        self._apply_proxies()
        if delete_data:
            data = self.paths.pool / "appdata" / app_id
            if data.is_dir() and self.mounted(self.paths.pool):
                subprocess.run(["rm", "-rf", "--one-file-system", "--", str(data)], check=True)
        return {"id": app_id, "data_deleted": delete_data}

    def list(self) -> list[dict]:
        out = []
        settings = self.naming.settings()
        host = self.naming._host()
        for app_id in installed(self.paths):
            t, state = load_installed(self.paths, app_id)
            login = {}
            if t.login:
                sec = self._secrets(app_id)
                with contextlib.suppress(AppError):
                    login = {k: fill(v, self._host(), sec) for k, v in t.login.items()}
            out.append({
                **t.summary(),
                "enabled": state.get("enabled", True),
                "status": self.status(t),
                "urls": domains.app_urls(app_id, t.public_port, settings, host)
                if t.public_port else None,
                "update": update_info(t, cached_template(self.paths, app_id)),
                "login": login or None,
            })
        return out

    def updates(self) -> "list[dict]":
        """Installed apps whose catalog template is newer."""
        out = []
        for app_id in installed(self.paths):
            t, _ = load_installed(self.paths, app_id)
            info = update_info(t, cached_template(self.paths, app_id))
            if info:
                out.append({"id": app_id, "name": t.name, "from": t.version, **info})
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


def parse_folders(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        share, sep, rel = item.partition("=")
        if not sep:
            raise AppError(f"--folder needs SHARE=PATH, got {item!r}")
        out[share] = rel
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dahlia-apps", description="Manage Dahlia apps.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("catalog", help="show the catalog")
    s.add_argument("--refresh", action="store_true", help="download it first")
    s.add_argument("--quiet", action="store_true",
                   help="print one summary line instead of the catalog (timer)")
    sub.add_parser("list", help="installed apps")
    sub.add_parser("updates", help="installed apps with a newer template in the catalog")
    for name in ("update", "start", "stop"):
        sub.add_parser(name).add_argument("id")
    folder_help = "use /mnt/user/PATH for SHARE instead of /mnt/user/SHARE"
    s = sub.add_parser("install")
    s.add_argument("id")
    s.add_argument("--folder", action="append", default=[], metavar="SHARE=PATH", help=folder_help)
    s = sub.add_parser("precheck", help="fail early if install/update cannot work")
    s.add_argument("op", choices=("install", "update"))
    s.add_argument("id")
    s.add_argument("--folder", action="append", default=[], metavar="SHARE=PATH", help=folder_help)
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
            if args.quiet:
                pending = apps.updates()
                names = ", ".join(f"{u['id']} {u['from']} -> {u['version']}" for u in pending)
                print(f"dahlia-apps: catalog has {len(result['apps'])} apps, "
                      f"{len(pending)} updates available{': ' + names if names else ''}")
                return 0
        elif args.command == "list":
            result = apps.list()
        elif args.command == "updates":
            result = apps.updates()
        elif args.command == "precheck":
            result = apps.precheck(args.op, args.id, parse_folders(args.folder))
        elif args.command == "install":
            result = apps.install(args.id, parse_folders(args.folder))
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
