"""HTTP API of the dashboard.

nginx serves the static frontend and proxies /api/ to this app on
127.0.0.1:8080. Everything that needs root goes through agent.

Errors are returned as {"error": "<key>", "detail": "..."}. The frontend
translates the key via locales/*.json; detail is the untranslated message
from the agent or the system, shown as additional information.
"""

import asyncio
import contextlib
import grp
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import AfterValidator, BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from . import home, pairing, setupcode
from .auth import (
    REMEMBER_AGE,
    USERNAME_RE,
    AuthError,
    RateLimiter,
    Session,
    SessionStore,
    TokenStore,
    UserStore,
    totp_uri,
)

COOKIE = "dahlia_session"
CSRF_HEADER = "x-dahlia-request"

JOBS = ("mover", "snapraid-sync", "snapraid-scrub", "update", "backup", "smart-short",
        "smart-long")
STARTABLE_JOBS = ("mover", "snapraid-sync", "snapraid-scrub", "backup", "smart-short",
                  "smart-long")
APP_OPS = ("install", "update", "start", "stop", "remove")
CONTAINER_OPS = ("start", "stop", "restart")

# Agent error code -> HTTP status.
AGENT_STATUS = {
    "invalid_params": 400,
    "busy": 409,
    "failed": 422,
    "unknown_action": 500,
    "bad_request": 500,
    "internal": 500,
    "forbidden": 503,
    "unavailable": 503,
    "timeout": 504,
}

AgentCall = Callable[[str, dict], object]
# Like AgentCall, for actions that hand over a file descriptor: returns (result, fd).
AgentOpen = Callable[[str, dict], tuple[object, int]]


def default_agent_call(socket_path: str) -> AgentCall:
    from dahlia_agent.client import call

    return lambda action, params: call(action, params, path=socket_path)


def default_agent_open(socket_path: str) -> AgentOpen:
    from dahlia_agent.client import call_fd

    return lambda action, params: call_fd(action, params, path=socket_path)


@dataclass
class Config:
    state_dir: Path = Path("/var/lib/dashboard")
    run_dir: Path = Path("/run/dashboard")
    agent_socket: str = "/run/dahlia/agent.sock"
    terminal_socket: str = "/run/terminal/terminal.sock"
    secure_cookie: bool = True
    agent_call: AgentCall | None = None
    agent_open: AgentOpen | None = None
    # None: sessions.json in state_dir.
    sessions: SessionStore | None = None
    login_limiter: RateLimiter = field(default_factory=RateLimiter)
    # Clock for TOTP codes; tests set it.
    clock: Callable[[], float] = time.time
    # Name of the account the installer created; tests set it.
    system_account: Callable[[], str | None] = field(default=lambda: installer_account())
    # Public part of the local CA (domains); the desktop app pins its fingerprint.
    ca_cert: Path = Path("/etc/dahlia/tls/ca.crt")
    # url -> JSON for the weather service; tests set it.
    weather_fetch: home.Fetch = home.http_fetch


def installer_account(group: str = "sudo") -> str | None:
    """The installer's account: the first member of group sudo that is also
    a valid dashboard name (d-i puts it there when root has no password)."""
    try:
        members = grp.getgrnam(group).gr_mem
    except KeyError:
        return None
    return next((m for m in members if USERNAME_RE.match(m)), None)


def qr_svg(text: str) -> str | None:
    """QR code as SVG from qrencode; the text goes in on stdin, not the command line."""
    try:
        proc = subprocess.run(["qrencode", "-t", "SVG", "-o", "-"], input=text,
                              capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 and proc.stdout.lstrip().startswith("<") else None


def ca_der(path: Path) -> bytes | None:
    """The CA certificate (first one in the PEM file) in DER form."""
    import base64

    try:
        pem = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    m = re.search(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", pem, re.S)
    if not m:
        return None
    try:
        return base64.b64decode("".join(m.group(1).split()), validate=True)
    except ValueError:
        return None


def ca_fingerprint(path: Path) -> str | None:
    """SHA-256 of the CA certificate (DER) as AB:CD:..., as the desktop app shows it."""
    import hashlib

    der = ca_der(path)
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest()) if der else None


def lan_address(stats: dict) -> str | None:
    """First IPv4 address of the server, for links a phone opens."""
    return next((a.split("/")[0] for n in stats.get("network", [])
                 for a in n.get("addresses", []) if ":" not in a), None)


FILE_CHUNK = 1024 * 1024


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def attachment(name: str) -> str:
    """Content-Disposition for a download, with an ASCII fallback name."""
    plain = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name)
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}"


class ApiError(Exception):
    def __init__(self, status: int, key: str, detail: str = ""):
        self.status = status
        self.key = key
        self.detail = detail


# --- request bodies -----------------------------------------------------------

# Account names are Linux user names and stored in lower case; "Fabian" in a
# form means the account fabian. The UI shows them with a capital letter.
AccountName = Annotated[str, AfterValidator(lambda v: v.strip().lower())]


class SetupBody(BaseModel):
    code: str
    username: AccountName
    password: str
    hostname: str | None = None


class LoginBody(BaseModel):
    username: AccountName
    password: str
    code: str = ""
    remember: bool = False


class TotpBeginBody(BaseModel):
    password: str


class TotpCodeBody(BaseModel):
    code: str


class TotpDisableBody(BaseModel):
    password: str
    code: str


class PasswordBody(BaseModel):
    old: str
    new: str


class UserBody(BaseModel):
    name: AccountName
    password: str
    role: str = "viewer"


class RoleBody(BaseModel):
    role: str


class ResetPasswordBody(BaseModel):
    password: str


class HostnameBody(BaseModel):
    hostname: str


class DiskAddBody(BaseModel):
    uuid: str
    role: str


class AppOpBody(BaseModel):
    delete_data: bool = False
    folders: dict[str, str] = {}
    location: str = ""
    # Custom container form; apps checks its fields.
    spec: dict | None = None


class FileNameBody(BaseModel):
    root: str
    path: str
    name: str


class FileStartBody(BaseModel):
    op: str
    root: str
    paths: list[str]
    dest_root: str = "/mnt/user"
    dest: str = ""


class MkdirBody(BaseModel):
    root: str = "/mnt/user"
    path: str


class DomainsBody(BaseModel):
    mdns_aliases: bool


class DiskImportBody(BaseModel):
    uuid: str
    target: str


class DiskFormatBody(BaseModel):
    device: str
    filesystem: str
    confirm: str
    encrypt: bool = False
    passphrase: str | None = None


class UnlockBody(BaseModel):
    passphrase: str


class AutoUnlockBody(BaseModel):
    enabled: bool
    passphrase: str | None = None


class PassphraseBody(BaseModel):
    old: str
    new: str


class SmartTestBody(BaseModel):
    device: str
    kind: str


class DeviceBody(BaseModel):
    device: str


class NetLayoutBody(BaseModel):
    nics: list[str]
    bond_mode: str = ""
    bridge: bool = False
    vlans: list[dict] = []


class WolBody(BaseModel):
    port: str
    enabled: bool


class SyslogBody(BaseModel):
    enabled: bool
    host: str = ""
    port: int = 514
    protocol: str = "udp"
    severity: str = "info"


class BurninStartBody(BaseModel):
    device: str
    confirm: str


class DiskRemoveBody(BaseModel):
    uuid: str
    force: bool = False


class TimeBody(BaseModel):
    timezone: str
    ntp: bool


class NetworkBody(BaseModel):
    mode: str
    address: str = ""
    gateway: str = ""
    dns: str = ""


class BackupBody(BaseModel):
    target: dict
    stop_apps: bool = True
    folders: list[str] = []


class RestoreBody(BaseModel):
    snapshot: str
    what: str


class SpindownBody(BaseModel):
    default: int
    disks: dict[str, int] = {}


class GovernorBody(BaseModel):
    governor: str


class FirewallBody(BaseModel):
    enabled: bool
    networks: list[str] = []


class FirewallRulesBody(BaseModel):
    rules: list[dict]


class ShareBody(BaseModel):
    comment: str = ""
    security: str
    users: dict[str, str] = {}
    time_machine: bool = False
    time_machine_size: int = 0
    nfs: bool = False
    recycle_days: int = 0


class RemoteBody(BaseModel):
    type: str
    host: str
    share: str
    username: str = ""
    # None keeps the saved password.
    password: str | None = None
    readonly: bool = False


class RemoveBody(BaseModel):
    force: bool = False


class SmbUserBody(BaseModel):
    name: str
    password: str


class SmbPasswordBody(BaseModel):
    password: str


class NotifyChannelBody(BaseModel):
    settings: dict


class NotifyEventsBody(BaseModel):
    events: dict[str, bool]


class TokenBody(BaseModel):
    name: str


class InfluxBody(BaseModel):
    enabled: bool
    url: str = ""
    version: int = 2
    org: str = ""
    bucket: str = ""
    database: str = ""
    username: str = ""
    # None keeps the saved secret.
    token: str | None = None
    password: str | None = None


class UpsBody(BaseModel):
    mode: str
    host: str = ""
    port: int = 3493
    name: str = "ups"
    user: str = ""
    password: str = ""
    shutdown_minutes: int = 0


class AutoUpdatesBody(BaseModel):
    enabled: bool
    reboot: bool
    reboot_time: str


class ParitySyncBody(BaseModel):
    force: bool = False


class ParityFixBody(BaseModel):
    what: str


class ParitySettingsBody(BaseModel):
    snapraid_delete_threshold: int
    scrub_percent: int
    scrub_older_than_days: int


class SnapshotBody(BaseModel):
    description: str


class OldRootBody(BaseModel):
    name: str


class StorageSettingsBody(BaseModel):
    minfreespace: str
    mover_min_age_minutes: int
    snapraid_delete_threshold: int


class WireguardBody(BaseModel):
    enabled: bool
    port: int
    endpoint: str
    route: str
    dns: str = ""
    subnet: str = "10.253.0.0/24"


class PeerBody(BaseModel):
    name: str


class TailscaleBody(BaseModel):
    routes: bool
    exit_node: bool


class ScheduleBody(BaseModel):
    frequency: str
    time: str = "03:00"
    weekday: str = "sun"
    monthday: int = 1


class PlaceBody(BaseModel):
    name: str
    latitude: float
    longitude: float
    country: str = ""


class PairingProofBody(BaseModel):
    nonce: str


class HomeBody(BaseModel):
    widgets: dict[str, bool]
    places: list[PlaceBody]


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    users = UserStore(cfg.state_dir / "users.json", clock=cfg.clock)
    sessions = cfg.sessions or SessionStore(path=cfg.state_dir / "sessions.json")
    tokens = TokenStore(cfg.state_dir / "tokens.json", clock=cfg.clock)
    limiter = cfg.login_limiter
    agent_call = cfg.agent_call or default_agent_call(cfg.agent_socket)
    agent_open_call = cfg.agent_open or default_agent_open(cfg.agent_socket)
    setup_lock = threading.Lock()
    home_store = home.HomeStore(cfg.state_dir / "home.json")
    weather = home.Weather(fetch=cfg.weather_fetch, clock=cfg.clock)
    pairing_codes = pairing.Codes()

    app = FastAPI(title="Dahlia Dashboard", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ApiError)
    def api_error(_request: Request, exc: ApiError):
        return JSONResponse({"error": exc.key, "detail": exc.detail}, status_code=exc.status)

    def agent(action: str, params: dict | None = None):
        from dahlia_agent.protocol import AgentError

        try:
            return agent_call(action, params or {})
        except AgentError as e:
            raise ApiError(AGENT_STATUS.get(e.code, 500), f"agent.{e.code}", e.message) from None

    def agent_open(action: str, params: dict):
        from dahlia_agent.protocol import AgentError

        try:
            return agent_open_call(action, params)
        except AgentError as e:
            raise ApiError(AGENT_STATUS.get(e.code, 500), f"agent.{e.code}", e.message) from None

    # --- dependencies ---------------------------------------------------------

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def require_csrf(request: Request) -> None:
        # Browsers cannot send a custom header cross-site without a CORS
        # preflight, which this app never answers.
        if request.headers.get(CSRF_HEADER) != "1":
            raise ApiError(403, "error.csrf")

    def require_user(request: Request):
        session = sessions.get(request.cookies.get(COOKIE))
        if session is None:
            raise ApiError(401, "error.login_required")
        return session

    def require_admin(request: Request):
        session = require_user(request)
        if users.role(session.username) != "admin":
            raise ApiError(403, "error.admin_only")
        return session

    def require_reader(request: Request):
        """Session cookie, or an API token for the read-only endpoints."""
        session = sessions.get(request.cookies.get(COOKIE))
        if session is not None:
            return session
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and tokens.verify(value.strip()) is not None:
            return None
        raise ApiError(401, "error.login_required")

    def set_session(response: Response, username: str, remember: bool = False) -> None:
        token = sessions.create(username, remember)
        response.set_cookie(
            COOKIE,
            token,
            # Without "stay signed in" the cookie ends with the browser session.
            max_age=REMEMBER_AGE if remember else None,
            httponly=True,
            secure=cfg.secure_cookie,
            samesite="strict",
            path="/",
        )

    # --- session and setup ----------------------------------------------------

    @app.get("/api/state")
    def state(request: Request):
        session = sessions.get(request.cookies.get(COOKIE))
        setup_required = not users.has_users()
        out = {
            "setup_required": setup_required,
            "user": session.username if session else None,
            "role": users.role(session.username) if session else None,
        }
        if setup_required:
            # The wizard suggests the name of the account from the installer.
            out["account"] = cfg.system_account()
        return out

    @app.post("/api/setup", dependencies=[Depends(require_csrf)])
    def setup(body: SetupBody, request: Request, response: Response):
        with setup_lock:
            return do_setup(body, request, response)

    def do_setup(body: SetupBody, request: Request, response: Response):
        if users.has_users():
            raise ApiError(409, "error.setup_done")
        ip = client_ip(request)
        if limiter.blocked(ip):
            raise ApiError(429, "error.too_many_attempts")
        if not setupcode.check(cfg.run_dir, body.code):
            limiter.fail(ip)
            raise ApiError(403, "error.setup_code")
        try:
            users.create(body.username, body.password)
        except AuthError as e:
            raise ApiError(400, f"error.{e}") from None
        setupcode.clear(cfg.run_dir)
        limiter.reset(ip)
        set_session(response, body.username)
        # The account exists now; a failing hostname change is reported but
        # does not undo the setup.
        if body.hostname:
            try:
                agent("system.set_hostname", {"hostname": body.hostname})
            except ApiError as e:
                return {"user": body.username, "hostname_error": e.key, "detail": e.detail}
        return {"user": body.username}

    @app.post("/api/login", dependencies=[Depends(require_csrf)])
    def login(body: LoginBody, request: Request, response: Response):
        ip = client_ip(request)
        if limiter.blocked(ip):
            raise ApiError(429, "error.too_many_attempts")
        if not users.verify(body.username, body.password):
            limiter.fail(ip)
            raise ApiError(401, "error.invalid_credentials")
        if users.totp_enabled(body.username):
            # Only said after a correct password; the form then asks for the code.
            if not body.code.strip():
                raise ApiError(401, "error.totp_required")
            if not users.check_second_factor(body.username, body.code):
                limiter.fail(ip)
                raise ApiError(401, "error.totp_code")
        limiter.reset(ip)
        set_session(response, body.username, body.remember)
        return {"user": body.username}

    @app.post("/api/logout", dependencies=[Depends(require_csrf)])
    def logout(request: Request, response: Response):
        sessions.drop(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {}

    @app.post("/api/account/password", dependencies=[Depends(require_csrf)])
    def change_password(
        body: PasswordBody, request: Request, session: Annotated[Session, Depends(require_user)]
    ):
        try:
            users.change_password(session.username, body.old, body.new)
        except AuthError as e:
            raise ApiError(400, f"error.{e}") from None
        # Other browsers logged in with the old password are logged out.
        sessions.drop_user(session.username, keep=request.cookies.get(COOKIE))
        return {}

    # --- second factor --------------------------------------------------------

    def recheck_password(request: Request, username: str, password: str) -> None:
        ip = client_ip(request)
        if limiter.blocked(ip):
            raise ApiError(429, "error.too_many_attempts")
        if not users.verify(username, password):
            limiter.fail(ip)
            raise ApiError(400, "error.old_password")

    @app.get("/api/account/totp")
    def totp_status(session: Annotated[Session, Depends(require_user)]):
        return users.totp_status(session.username)

    @app.post("/api/account/totp/begin", dependencies=[Depends(require_csrf)])
    def totp_begin(body: TotpBeginBody, request: Request,
                   session: Annotated[Session, Depends(require_user)]):
        recheck_password(request, session.username, body.password)
        secret = users.totp_begin(session.username)
        try:
            host = agent("system.info").get("hostname") or socket.gethostname()
        except ApiError:
            host = socket.gethostname()
        uri = totp_uri(secret, f"{session.username}@{host}", "Dahlia")
        return {"secret": secret, "uri": uri, "qr_svg": qr_svg(uri)}

    @app.post("/api/account/totp/confirm", dependencies=[Depends(require_csrf)])
    def totp_confirm(body: TotpCodeBody, request: Request,
                     session: Annotated[Session, Depends(require_user)]):
        try:
            codes = users.totp_confirm(session.username, body.code)
        except AuthError as e:
            raise ApiError(400, f"error.{e}") from None
        sessions.drop_user(session.username, keep=request.cookies.get(COOKIE))
        return {"recovery_codes": codes}

    @app.post("/api/account/totp/disable", dependencies=[Depends(require_csrf)])
    def totp_disable(body: TotpDisableBody, request: Request,
                     session: Annotated[Session, Depends(require_user)]):
        recheck_password(request, session.username, body.password)
        if not users.check_second_factor(session.username, body.code):
            limiter.fail(client_ip(request))
            raise ApiError(400, "error.totp_code")
        users.totp_disable(session.username)
        return {}

    # --- system ---------------------------------------------------------------

    authed = [Depends(require_user)]
    # Changes, and what only an admin may see (tokens, diagnostics, accounts).
    changing = [Depends(require_csrf), Depends(require_admin)]
    admin = [Depends(require_admin)]
    # Values only, nothing secret: these also accept API tokens.
    readable = [Depends(require_reader)]

    # --- accounts ---------------------------------------------------------------

    @app.get("/api/users", dependencies=admin)
    def users_list():
        return {"users": users.list_users()}

    def user_error(e: AuthError):
        return ApiError(409 if str(e) in ("exists", "last_admin", "user_limit") else 400,
                        f"error.{e}")

    @app.post("/api/users", dependencies=changing)
    def users_create(body: UserBody):
        try:
            users.create(body.name, body.password, body.role)
        except AuthError as e:
            raise user_error(e) from None
        return {"name": body.name, "role": body.role}

    @app.post("/api/users/{name}/role", dependencies=changing)
    def users_role(name: str, body: RoleBody):
        try:
            users.set_role(name, body.role)
        except AuthError as e:
            raise user_error(e) from None
        return {"name": name, "role": body.role}

    @app.post("/api/users/{name}/password", dependencies=changing)
    def users_password(name: str, body: ResetPasswordBody):
        try:
            users.reset_password(name, body.password)
        except AuthError as e:
            raise user_error(e) from None
        sessions.drop_user(name)
        return {}

    @app.post("/api/users/{name}/remove", dependencies=changing)
    def users_remove(name: str, session: Annotated[Session, Depends(require_user)]):
        if name == session.username:
            raise ApiError(409, "error.remove_self")
        try:
            users.delete(name)
        except AuthError as e:
            raise user_error(e) from None
        sessions.drop_user(name)
        return {}

    @app.get("/api/tokens", dependencies=admin)
    def tokens_list():
        return {"tokens": tokens.list()}

    @app.post("/api/tokens", dependencies=changing)
    def tokens_create(body: TokenBody):
        try:
            entry, token = tokens.create(body.name)
        except AuthError as e:
            raise ApiError(400, f"error.{e}") from None
        return {**entry, "token": token}

    @app.post("/api/tokens/{token_id}/remove", dependencies=changing)
    def tokens_remove(token_id: str):
        if not tokens.delete(token_id):
            raise ApiError(404, "error.not_found")
        return {}

    @app.get("/api/system", dependencies=readable)
    def system():
        return agent("system.info")

    @app.get("/api/stats", dependencies=readable)
    def stats():
        return agent("system.stats")

    # --- start screen -----------------------------------------------------------

    @app.get("/api/home", dependencies=readable)
    def home_get():
        return home_store.get()

    @app.post("/api/home", dependencies=changing)
    def home_set(body: HomeBody):
        try:
            return home_store.set(body.widgets, [p.model_dump() for p in body.places])
        except ValueError as e:
            raise ApiError(400, "error.request", str(e)) from None

    @app.get("/api/weather", dependencies=readable)
    def weather_get():
        return {"places": weather.all(home_store.get()["places"])}

    @app.get("/api/weather/search", dependencies=changing[1:])
    def weather_search(q: str, lang: str = "en"):
        try:
            return {"results": home.search(q, lang, cfg.weather_fetch)}
        except home.WeatherError as e:
            raise ApiError(502, "error.weather", str(e)) from None

    # --- local screen -------------------------------------------------------------

    screen_cache: dict[str, tuple[float, object]] = {}

    def cached(key: str, seconds: float, fn: Callable[[], object]):
        hit = screen_cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < seconds:
            return hit[1]
        try:
            value = fn()
        except (ApiError, home.WeatherError, OSError):
            value = hit[1] if hit else None
        screen_cache[key] = (now, value)
        return value

    @app.get("/api/screen")
    def screen(request: Request):
        """Start screen on the monitor attached to the server (screen.service).

        No login: nginx refuses this path on the network site and serves it only
        on 127.0.0.1:8090 for the local browser; requests through nginx's
        network site carry X-Forwarded-For.
        """
        if "x-forwarded-for" in request.headers or client_ip(request) not in ("127.0.0.1", "::1"):
            raise ApiError(404, "error.not_found")
        info = cached("info", 300, lambda: agent("system.info")) or {}
        stats = cached("stats", 5, lambda: agent("system.stats")) or {}
        address = lan_address(stats)
        fs = {f["mountpoint"]: f for f in stats.get("filesystems", [])}
        storage = fs.get("/mnt/user") or fs.get("/")
        mem = stats.get("memory") or {}
        apps = cached("apps", 30, lambda: agent("apps.list")["apps"]) or []
        cfg_home = home_store.get()
        setup_code = setupcode.current(cfg.run_dir) if not users.has_users() else None
        url = f"https://{address}/" if address else None
        return {
            "hostname": info.get("hostname"),
            "version": info.get("version"),
            "address": address,
            "url": url,
            "qr": cached(f"qr:{url}", 3600, lambda: qr_svg(url)) if url else None,
            "setup_code": setup_code,
            "ca_fingerprint": ca_fingerprint(cfg.ca_cert),
            "health": cached("health", 60, lambda: agent("system.health")),
            "storage": {"used": storage["used"], "size": storage["size"]} if storage else None,
            "memory": {"used": mem["total"] - mem["available"], "size": mem["total"]}
            if mem.get("total") else None,
            "uptime_seconds": stats.get("uptime_seconds"),
            "apps": {"running": sum(a.get("status") == "running" for a in apps),
                     "total": len(apps)},
            "widgets": cfg_home["widgets"],
            "weather": weather.all(cfg_home["places"]),
        }

    # --- phone and desktop app ------------------------------------------------------

    @app.get("/api/pairing", dependencies=authed)
    def pairing_info():
        """Link and QR code for the phone, CA fingerprint for the desktop app."""
        stats = cached("stats", 5, lambda: agent("system.stats")) or {}
        address = lan_address(stats)
        url = f"https://{address}/" if address else None
        return {
            "ca_fingerprint": ca_fingerprint(cfg.ca_cert),
            "url": url,
            "qr": cached(f"qr:{url}", 3600, lambda: qr_svg(url)) if url else None,
        }

    @app.post("/api/pairing/code", dependencies=changing)
    def pairing_code():
        return {"code": pairing.display(pairing_codes.create()), "expires_in": pairing.TTL}

    @app.post("/api/pairing/proof", dependencies=[Depends(require_csrf)])
    def pairing_proof(body: PairingProofBody):
        """No login: the desktop app is not paired yet. See pairing.py."""
        try:
            nonce = bytes.fromhex(body.nonce)
        except ValueError:
            nonce = b""
        if len(nonce) != pairing.NONCE_BYTES:
            raise ApiError(400, "error.request")
        der = ca_der(cfg.ca_cert)
        code = pairing_codes.take()
        if not der or not code:
            raise ApiError(404, "error.pairing_code")
        return {"proof": pairing.proof(code, nonce, der)}

    @app.get("/api/history", dependencies=readable)
    def history(range: str = "day"):
        return agent("history.get", {"range": range})

    @app.get("/api/history/influx", dependencies=authed)
    def history_influx():
        return agent("history.influx")

    @app.post("/api/history/influx", dependencies=changing)
    def history_set_influx(body: InfluxBody):
        return agent("history.set_influx", body.model_dump(exclude_none=True))

    @app.post("/api/system/hostname", dependencies=changing)
    def hostname(body: HostnameBody):
        return agent("system.set_hostname", {"hostname": body.hostname})

    @app.post("/api/system/reboot", dependencies=changing)
    def reboot():
        return agent("system.reboot")

    @app.post("/api/system/poweroff", dependencies=changing)
    def poweroff():
        return agent("system.poweroff")

    # --- storage --------------------------------------------------------------

    @app.get("/api/storage", dependencies=readable)
    def storage():
        return agent("storage.list")

    @app.post("/api/storage/add", dependencies=changing)
    def storage_add(body: DiskAddBody):
        return agent("storage.add", {"uuid": body.uuid, "role": body.role})

    @app.get("/api/storage/import", dependencies=authed)
    def storage_import_status():
        return agent("storage.import_status")

    @app.post("/api/storage/import", dependencies=changing)
    def storage_import(body: DiskImportBody):
        return agent("storage.import", body.model_dump())

    @app.post("/api/storage/format", dependencies=changing)
    def storage_format(body: DiskFormatBody):
        return agent("storage.format", body.model_dump(exclude_defaults=True))

    @app.get("/api/storage/encryption", dependencies=authed)
    def storage_encryption():
        return agent("storage.encryption")

    @app.post("/api/storage/unlock", dependencies=changing)
    def storage_unlock(body: UnlockBody):
        return agent("storage.unlock", body.model_dump())

    @app.post("/api/storage/auto-unlock", dependencies=changing)
    def storage_auto_unlock(body: AutoUnlockBody):
        return agent("storage.auto_unlock", body.model_dump(exclude_none=True))

    @app.post("/api/storage/passphrase", dependencies=changing)
    def storage_passphrase(body: PassphraseBody):
        return agent("storage.change_passphrase", body.model_dump())

    @app.post("/api/disks/smart-test", dependencies=changing)
    def disks_smart_test(body: SmartTestBody):
        return agent("disks.smart_test", body.model_dump())

    @app.get("/api/disks/burnin", dependencies=authed)
    def disks_burnin():
        return agent("disks.burnin_status")

    @app.post("/api/disks/locate", dependencies=changing)
    def disks_locate(body: DeviceBody):
        return agent("disks.locate", body.model_dump())

    @app.post("/api/disks/locate/stop", dependencies=changing)
    def disks_locate_stop():
        return agent("disks.locate_stop")

    @app.post("/api/disks/burnin/start", dependencies=changing)
    def disks_burnin_start(body: BurninStartBody):
        return agent("disks.burnin_start", body.model_dump())

    @app.post("/api/disks/burnin/{op}", dependencies=changing)
    def disks_burnin_op(op: str, body: DeviceBody):
        if op not in ("stop", "clear"):
            raise ApiError(404, "error.not_found")
        return agent(f"disks.burnin_{op}", body.model_dump())

    @app.post("/api/storage/remove", dependencies=changing)
    def storage_remove(body: DiskRemoveBody):
        return agent("storage.remove", {"uuid": body.uuid, "force": body.force})

    # --- parity ---------------------------------------------------------------

    @app.get("/api/parity", dependencies=readable)
    def parity():
        return agent("parity.get")

    @app.post("/api/parity/sync", dependencies=changing)
    def parity_sync(body: ParitySyncBody):
        return agent("parity.sync", {"force": body.force})

    @app.post("/api/parity/fix", dependencies=changing)
    def parity_fix(body: ParityFixBody):
        return agent("parity.fix", {"what": body.what})

    @app.get("/api/storage/settings", dependencies=authed)
    def storage_settings():
        return agent("storage.settings", {})

    @app.post("/api/storage/settings", dependencies=changing)
    def storage_settings_set(body: StorageSettingsBody):
        agent("storage.set_minfreespace", {"size": body.minfreespace})
        for name in ("mover_min_age_minutes", "snapraid_delete_threshold"):
            agent("storage.set_option", {"name": name, "value": getattr(body, name)})
        return body.model_dump()

    @app.post("/api/parity/settings", dependencies=changing)
    def parity_settings(body: ParitySettingsBody):
        for name, value in body.model_dump().items():
            agent("storage.set_option", {"name": name, "value": value})
        return body.model_dump()

    # --- jobs and updates -----------------------------------------------------

    @app.get("/api/jobs", dependencies=readable)
    def jobs():
        return {job: agent("job.status", {"job": job}) for job in JOBS}

    @app.post("/api/jobs/{job}/start", dependencies=changing)
    def job_start(job: str):
        if job not in STARTABLE_JOBS:
            raise ApiError(404, "error.not_found")
        return agent("job.start", {"job": job})

    @app.post("/api/updates/check", dependencies=changing)
    def updates_check():
        return agent("updates.check")

    @app.post("/api/updates/apply", dependencies=changing)
    def updates_apply():
        return agent("updates.apply")

    @app.get("/api/updates/progress", dependencies=authed)
    def updates_progress():
        return agent("updates.progress")

    @app.get("/api/updates/auto", dependencies=authed)
    def updates_auto():
        return agent("updates.auto")

    @app.post("/api/updates/auto", dependencies=changing)
    def updates_set_auto(body: AutoUpdatesBody):
        return agent("updates.set_auto", body.model_dump())

    # --- snapshots ------------------------------------------------------------

    @app.get("/api/snapshots", dependencies=authed)
    def snapshots():
        return agent("snapshots.list")

    @app.post("/api/snapshots", dependencies=changing)
    def snapshot_create(body: SnapshotBody):
        return agent("snapshots.create", {"description": body.description})

    # Before /{number}/delete, which would take "old-root" as a number.
    @app.post("/api/snapshots/old-root/delete", dependencies=changing)
    def snapshot_delete_old_root(body: OldRootBody):
        return agent("snapshots.delete_old_root", {"name": body.name})

    @app.post("/api/snapshots/{number}/delete", dependencies=changing)
    def snapshot_delete(number: int):
        return agent("snapshots.delete", {"number": number})

    @app.post("/api/snapshots/{number}/rollback", dependencies=changing)
    def snapshot_rollback(number: int):
        return agent("snapshots.rollback", {"number": number})

    # --- apps -----------------------------------------------------------------

    @app.get("/api/apps", dependencies=readable)
    def apps(request: Request, catalog: bool = True):
        """Installed apps and the catalog. catalog=false leaves the catalog out
        (overview, polling while a job runs)."""
        installed = agent("apps.list")["apps"]
        session = sessions.get(request.cookies.get(COOKIE))
        if session is None or users.role(session.username) != "admin":
            # API token or read-only account: no generated app passwords,
            # no custom container settings.
            installed = [{**a, "login": None, "custom": None} for a in installed]
        if not catalog:
            return {"installed": installed}
        offered = agent("apps.catalog", {"refresh": False})
        return {
            "installed": installed,
            "catalog": offered["apps"],
            "catalog_refreshed": offered.get("refreshed"),
            "gpu_available": offered.get("gpu_available", False),
        }

    @app.post("/api/apps/catalog/refresh", dependencies=changing)
    def apps_refresh():
        return agent("apps.catalog", {"refresh": True})

    @app.get("/api/apps-all", dependencies=authed)
    def apps_all_job():
        return agent("apps.all_job")

    @app.post("/api/apps-all/{op}", dependencies=changing)
    def apps_all(op: str):
        if op not in ("start", "stop"):
            raise ApiError(404, "error.not_found")
        return agent("apps.all", {"op": op})

    @app.get("/api/apps/{app_id}/job", dependencies=authed)
    def app_job(app_id: str):
        return agent("apps.job", {"id": app_id})

    @app.post("/api/apps/{app_id}/{op}", dependencies=changing)
    def app_op(app_id: str, op: str, body: AppOpBody | None = None):
        if op not in APP_OPS:
            raise ApiError(404, "error.not_found")
        params = {"id": app_id}
        if op == "remove" and body is not None:
            params["delete_data"] = body.delete_data
        if op in ("install", "update") and body is not None and body.spec is not None:
            params["spec"] = body.spec
            if op == "install" and body.location:
                params["location"] = body.location
            return agent(f"apps.custom_{op}", params)
        if op == "install" and body is not None:
            if body.folders:
                params["folders"] = body.folders
            if body.location:
                params["location"] = body.location
        return agent(f"apps.{op}", params)

    @app.get("/api/files", dependencies=authed)
    def files_browse(root: str = "/mnt/user", path: str = ""):
        return agent("files.browse", {"root": root, "path": path})

    # File manager (admin only, reading too: it shows every file of the shares).
    @app.get("/api/files/list", dependencies=admin)
    def files_list(root: str = "/mnt/user", path: str = ""):
        return agent("files.list", {"root": root, "path": path})

    @app.get("/api/files/search", dependencies=admin)
    def files_search(root: str, query: str, path: str = ""):
        return agent("files.search", {"root": root, "path": path, "query": query})

    @app.get("/api/files/download", dependencies=admin)
    def files_download(root: str, path: str):
        info, fd = agent_open("files.open", {"root": root, "path": path})

        def chunks():
            try:
                while data := os.read(fd, FILE_CHUNK):
                    yield data
            finally:
                os.close(fd)

        return StreamingResponse(chunks(), media_type="application/octet-stream", headers={
            "Content-Length": str(info["size"]),
            "Content-Disposition": attachment(info["name"]),
        })

    @app.post("/api/files/upload", dependencies=changing)
    async def files_upload(request: Request, root: str, path: str, name: str,
                           overwrite: bool = False):
        where = {"root": root, "path": path}
        info, fd = await run_in_threadpool(
            agent_open, "files.upload", {**where, "name": name, "overwrite": overwrite})
        try:
            async for chunk in request.stream():
                await run_in_threadpool(write_all, fd, chunk)
        except BaseException:
            os.close(fd)
            await run_in_threadpool(agent, "files.upload_abort", {**where, "tmp": info["tmp"]})
            raise
        os.close(fd)
        return await run_in_threadpool(agent, "files.upload_finish", {
            **where, "name": name, "tmp": info["tmp"], "overwrite": overwrite})

    @app.post("/api/files/dir", dependencies=changing)
    def files_create_dir(body: FileNameBody):
        return agent("files.create_dir", body.model_dump())

    @app.post("/api/files/rename", dependencies=changing)
    def files_rename(body: FileNameBody):
        return agent("files.rename", body.model_dump())

    @app.post("/api/files/start", dependencies=changing)
    def files_start(body: FileStartBody):
        return agent("files.start", body.model_dump())

    @app.get("/api/files/job", dependencies=admin)
    def files_job():
        return agent("files.job")

    @app.post("/api/files/cancel", dependencies=changing)
    def files_cancel():
        return agent("files.cancel")

    @app.post("/api/files/mkdir", dependencies=changing)
    def files_mkdir(body: MkdirBody):
        return agent("files.mkdir", {"root": body.root, "path": body.path})

    # --- docker and hardware -------------------------------------------------

    @app.get("/api/docker", dependencies=readable)
    def docker_list():
        return agent("docker.list")

    @app.get("/api/docker/stats", dependencies=readable)
    def docker_stats():
        return agent("docker.stats")

    @app.get("/api/docker/disk", dependencies=authed)
    def docker_disk():
        return agent("docker.disk")

    @app.post("/api/docker/prune", dependencies=changing)
    def docker_prune():
        return agent("docker.prune")

    @app.get("/api/docker/{name}/logs", dependencies=authed)
    def docker_logs(name: str, lines: int = 200):
        return agent("docker.logs", {"name": name, "lines": lines})

    @app.post("/api/docker/{name}/{op}", dependencies=changing)
    def docker_op(name: str, op: str):
        if op not in CONTAINER_OPS:
            raise ApiError(404, "error.not_found")
        return agent("docker.container", {"name": name, "op": op})

    @app.get("/api/hardware", dependencies=readable)
    def hardware():
        return agent("system.hardware")

    @app.get("/api/devices", dependencies=authed)
    def devices():
        return agent("system.devices")

    @app.get("/api/health", dependencies=authed)
    def health():
        return agent("system.health")

    @app.post("/api/diagnostics", dependencies=changing)
    def diagnostics_create():
        return agent("system.diagnostics")

    @app.get("/api/diagnostics", dependencies=admin)
    def diagnostics_download():
        # The agent puts the bundle into our runtime directory; it is handed out once.
        path = cfg.run_dir / "diagnostics.tar.gz"
        if not path.is_file():
            raise ApiError(404, "error.not_found")
        host = re.sub(r"[^a-z0-9-]", "", agent("system.info").get("hostname", "").lower())
        name = f"diagnostics-{host or 'server'}-{time.strftime('%Y%m%d-%H%M')}.tar.gz"
        return FileResponse(path, media_type="application/gzip", filename=name,
                            background=BackgroundTask(path.unlink, missing_ok=True))

    @app.get("/api/log", dependencies=authed)
    def system_log(lines: int = 300, priority: str = "info", unit: str = ""):
        return agent("system.log", {"lines": lines, "priority": priority, "unit": unit})

    # --- settings -------------------------------------------------------------

    @app.get("/api/settings/time", dependencies=authed)
    def settings_time():
        return agent("settings.time")

    @app.post("/api/settings/time", dependencies=changing)
    def settings_set_time(body: TimeBody):
        return agent("settings.set_time", body.model_dump())

    @app.get("/api/settings/network", dependencies=authed)
    def settings_network():
        return agent("settings.network")

    @app.post("/api/settings/network", dependencies=changing)
    def settings_set_network(body: NetworkBody):
        return agent("settings.set_network", body.model_dump())

    @app.get("/api/settings/network/layout", dependencies=authed)
    def settings_network_layout():
        return agent("settings.network_layout")

    @app.post("/api/settings/network/layout", dependencies=changing)
    def settings_set_network_layout(body: NetLayoutBody):
        return agent("settings.set_network_layout", body.model_dump())

    @app.post("/api/settings/wol", dependencies=changing)
    def settings_set_wol(body: WolBody):
        return agent("settings.set_wol", body.model_dump())

    @app.post("/api/settings/network/confirm", dependencies=changing)
    def settings_confirm_network():
        return agent("settings.confirm_network")

    @app.get("/api/settings/spindown", dependencies=authed)
    def settings_spindown():
        return agent("settings.spindown")

    @app.post("/api/settings/spindown", dependencies=changing)
    def settings_set_spindown(body: SpindownBody):
        return agent("settings.set_spindown", body.model_dump())

    @app.get("/api/settings/power", dependencies=authed)
    def settings_power():
        return agent("power.get")

    @app.post("/api/settings/power", dependencies=changing)
    def settings_set_power(body: GovernorBody):
        return agent("power.set_governor", body.model_dump())

    @app.get("/api/settings/syslog", dependencies=authed)
    def settings_syslog():
        return agent("settings.syslog")

    @app.post("/api/settings/syslog", dependencies=changing)
    def settings_set_syslog(body: SyslogBody):
        return agent("settings.set_syslog", body.model_dump())

    @app.get("/api/settings/firewall", dependencies=authed)
    def settings_firewall():
        return agent("settings.firewall")

    @app.post("/api/settings/firewall", dependencies=changing)
    def settings_set_firewall(body: FirewallBody):
        return agent("settings.set_firewall", body.model_dump())

    @app.post("/api/settings/firewall/rules", dependencies=changing)
    def settings_set_firewall_rules(body: FirewallRulesBody):
        return agent("settings.set_firewall_rules", {"rules": body.rules})

    @app.get("/api/vpn", dependencies=authed)
    def vpn_status():
        return agent("vpn.status")

    @app.post("/api/vpn/wireguard", dependencies=changing)
    def vpn_wireguard(body: WireguardBody):
        return agent("vpn.wireguard_set", body.model_dump())

    @app.post("/api/vpn/wireguard/peers", dependencies=changing)
    def vpn_add_peer(body: PeerBody):
        return agent("vpn.wireguard_add_peer", body.model_dump())

    @app.post("/api/vpn/wireguard/peers/delete", dependencies=changing)
    def vpn_remove_peer(body: PeerBody):
        return agent("vpn.wireguard_remove_peer", body.model_dump())

    @app.post("/api/vpn/tailscale/install", dependencies=changing)
    def vpn_tailscale_install():
        return agent("vpn.tailscale_install")

    @app.post("/api/vpn/tailscale/up", dependencies=changing)
    def vpn_tailscale_up(body: TailscaleBody):
        return agent("vpn.tailscale_up", body.model_dump())

    @app.post("/api/vpn/tailscale/logout", dependencies=changing)
    def vpn_tailscale_logout():
        return agent("vpn.tailscale_logout")

    @app.get("/api/settings/schedules", dependencies=authed)
    def settings_schedules():
        return agent("settings.schedules")

    @app.post("/api/settings/schedules/{job}", dependencies=changing)
    def settings_set_schedule(job: str, body: ScheduleBody):
        return agent("settings.set_schedule", {"job": job, **body.model_dump()})

    # --- notifications and UPS ------------------------------------------------

    @app.get("/api/notify", dependencies=authed)
    def notify_get():
        return agent("notify.get")

    @app.post("/api/notify/events", dependencies=changing)
    def notify_events(body: NotifyEventsBody):
        return agent("notify.set_events", {"events": body.events})

    @app.post("/api/notify/test", dependencies=changing)
    def notify_test():
        return agent("notify.test")

    @app.post("/api/notify/{channel}", dependencies=changing)
    def notify_set(channel: str, body: NotifyChannelBody):
        return agent("notify.set", {"channel": channel, "settings": body.settings})

    @app.get("/api/ups", dependencies=authed)
    def ups_get():
        return agent("ups.get")

    @app.post("/api/ups", dependencies=changing)
    def ups_set(body: UpsBody):
        return agent("ups.set", body.model_dump())

    # --- shares ---------------------------------------------------------------

    @app.get("/api/shares", dependencies=authed)
    def shares_list():
        return agent("shares.list")

    @app.post("/api/smb-users", dependencies=changing)
    def smb_user_add(body: SmbUserBody):
        return agent("shares.user_add", {"name": body.name, "password": body.password})

    @app.post("/api/smb-users/{name}/password", dependencies=changing)
    def smb_user_password(name: str, body: SmbPasswordBody):
        return agent("shares.user_password", {"name": name, "password": body.password})

    @app.post("/api/smb-users/{name}/remove", dependencies=changing)
    def smb_user_remove(name: str):
        return agent("shares.user_remove", {"name": name})

    @app.post("/api/shares/{name}", dependencies=changing)
    def shares_set(name: str, body: ShareBody):
        return agent("shares.set", {"name": name, **body.model_dump()})

    @app.get("/api/remotes", dependencies=authed)
    def remotes_list():
        return agent("remotes.list")

    @app.post("/api/remotes/{name}", dependencies=changing)
    def remotes_set(name: str, body: RemoteBody):
        return agent("remotes.set", {"name": name, **body.model_dump(exclude_none=True)})

    @app.post("/api/remotes/{name}/remove", dependencies=changing)
    def remotes_remove(name: str, body: RemoveBody):
        return agent("remotes.remove", {"name": name, "force": body.force})

    @app.post("/api/shares/{name}/empty-recycle", dependencies=changing)
    def shares_empty_recycle(name: str):
        return agent("shares.empty_recycle", {"name": name})

    @app.post("/api/shares/{name}/remove", dependencies=changing)
    def shares_remove(name: str):
        return agent("shares.remove", {"name": name})

    # --- backup ---------------------------------------------------------------

    @app.get("/api/backup", dependencies=authed)
    def backup_get():
        return agent("backup.get")

    @app.post("/api/backup", dependencies=changing)
    def backup_set(body: BackupBody):
        return agent("backup.set", body.model_dump())

    @app.post("/api/backup/key", dependencies=changing)
    def backup_key():
        # POST with the CSRF header: the password is not a GET a foreign page could embed.
        return agent("backup.key")

    @app.get("/api/backup/snapshots", dependencies=authed)
    def backup_snapshots():
        return agent("backup.snapshots")

    @app.post("/api/backup/restore", dependencies=changing)
    def backup_restore(body: RestoreBody):
        return agent("backup.restore", body.model_dump())

    # --- domains --------------------------------------------------------------

    @app.get("/api/domains", dependencies=authed)
    def domains():
        return agent("domains.get")

    @app.post("/api/domains", dependencies=changing)
    def domains_set(body: DomainsBody):
        return agent("domains.set", {"mdns_aliases": body.mdns_aliases})

    # --- terminal -------------------------------------------------------------

    @app.websocket("/api/terminal")
    async def terminal(ws: WebSocket):
        """Relay between xterm.js and terminal (see terminal.py)."""
        from .terminal import frame

        session = sessions.get(ws.cookies.get(COOKIE))
        # A WebSocket is not covered by CORS: only pages of this server may open one.
        # nginx passes $host, which has no port; compare host names only.
        origin = urllib.parse.urlsplit(ws.headers.get("origin", "")).hostname
        host = urllib.parse.urlsplit("//" + ws.headers.get("host", "")).hostname
        if session is None or not origin or origin != host \
                or users.role(session.username) != "admin":
            await ws.close(code=4401)
            return
        await ws.accept()
        q = ws.query_params
        hello = {
            # Always the Linux account with the dashboard account's name.
            "user": session.username,
            "cols": int(q["cols"]) if q.get("cols", "").isdigit() else 80,
            "rows": int(q["rows"]) if q.get("rows", "").isdigit() else 24,
            "by": session.username,
            "ip": ws.headers.get("x-forwarded-for") or (ws.client.host if ws.client else ""),
        }
        try:
            reader, writer = await asyncio.open_unix_connection(cfg.terminal_socket)
        except OSError:
            await ws.send_text(json.dumps({"error": "terminal.unavailable"}))
            await ws.close()
            return
        writer.write(json.dumps(hello).encode() + b"\n")

        async def to_browser():
            while data := await reader.read(65536):
                await ws.send_bytes(data)

        async def to_terminal():
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes"):
                    writer.write(frame(b"d", msg["bytes"]))
                elif msg.get("text"):
                    writer.write(frame(b"r", msg["text"].encode()[:256]))
                await writer.drain()

        tasks = [asyncio.create_task(to_browser()), asyncio.create_task(to_terminal())]
        try:
            with contextlib.suppress(WebSocketDisconnect):
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            writer.close()
            if ws.client_state.name == "CONNECTED":
                with contextlib.suppress(RuntimeError):
                    await ws.close()

    @app.exception_handler(HTTPException)
    def http_error(_request: Request, exc: HTTPException):
        key = "error.not_found" if exc.status_code == 404 else "error.request"
        return JSONResponse({"error": key, "detail": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    def validation_error(_request: Request, exc: RequestValidationError):
        fields = sorted({".".join(map(str, e.get("loc", ())[1:])) for e in exc.errors()})
        return JSONResponse(
            {"error": "error.request", "detail": "invalid fields: " + ", ".join(fields)},
            status_code=422,
        )

    return app
