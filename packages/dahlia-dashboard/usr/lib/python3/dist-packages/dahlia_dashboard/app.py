"""HTTP API of the dashboard.

nginx serves the static frontend and proxies /api/ to this app on
127.0.0.1:8080. Everything that needs root goes through dahlia-agent.

Errors are returned as {"error": "<key>", "detail": "..."}. The frontend
translates the key via locales/*.json; detail is the untranslated message
from the agent or the system, shown as additional information.
"""

import asyncio
import contextlib
import json
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
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from . import setupcode
from .auth import (
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


def default_agent_call(socket_path: str) -> AgentCall:
    from dahlia_agent.client import call

    return lambda action, params: call(action, params, path=socket_path)


@dataclass
class Config:
    state_dir: Path = Path("/var/lib/dahlia-dashboard")
    run_dir: Path = Path("/run/dahlia-dashboard")
    agent_socket: str = "/run/dahlia/agent.sock"
    terminal_socket: str = "/run/dahlia-terminal/terminal.sock"
    secure_cookie: bool = True
    agent_call: AgentCall | None = None
    # None: sessions.json in state_dir.
    sessions: SessionStore | None = None
    login_limiter: RateLimiter = field(default_factory=RateLimiter)
    # Clock for TOTP codes; tests set it.
    clock: Callable[[], float] = time.time


def qr_svg(text: str) -> str | None:
    """QR code as SVG from qrencode; the text goes in on stdin, not the command line."""
    try:
        proc = subprocess.run(["qrencode", "-t", "SVG", "-o", "-"], input=text,
                              capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 and proc.stdout.lstrip().startswith("<") else None


class ApiError(Exception):
    def __init__(self, status: int, key: str, detail: str = ""):
        self.status = status
        self.key = key
        self.detail = detail


# --- request bodies -----------------------------------------------------------


class SetupBody(BaseModel):
    code: str
    username: str
    password: str
    hostname: str | None = None


class LoginBody(BaseModel):
    username: str
    password: str
    code: str = ""


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


class HostnameBody(BaseModel):
    hostname: str


class DiskAddBody(BaseModel):
    uuid: str
    role: str


class AppOpBody(BaseModel):
    delete_data: bool = False
    folders: dict[str, str] = {}
    location: str = ""
    # Custom container form; dahlia-apps checks its fields.
    spec: dict | None = None


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


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    users = UserStore(cfg.state_dir / "users.json", clock=cfg.clock)
    sessions = cfg.sessions or SessionStore(path=cfg.state_dir / "sessions.json")
    tokens = TokenStore(cfg.state_dir / "tokens.json", clock=cfg.clock)
    limiter = cfg.login_limiter
    agent_call = cfg.agent_call or default_agent_call(cfg.agent_socket)
    setup_lock = threading.Lock()

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

    def require_reader(request: Request):
        """Session cookie, or an API token for the read-only endpoints."""
        session = sessions.get(request.cookies.get(COOKIE))
        if session is not None:
            return session
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and tokens.verify(value.strip()) is not None:
            return None
        raise ApiError(401, "error.login_required")

    def set_session(response: Response, username: str) -> None:
        token = sessions.create(username)
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=cfg.secure_cookie,
            samesite="strict",
            path="/",
        )

    # --- session and setup ----------------------------------------------------

    @app.get("/api/state")
    def state(request: Request):
        session = sessions.get(request.cookies.get(COOKIE))
        return {
            "setup_required": not users.has_users(),
            "user": session.username if session else None,
        }

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
        set_session(response, body.username)
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
    changing = [Depends(require_csrf), Depends(require_user)]
    # Values only, nothing secret: these also accept API tokens.
    readable = [Depends(require_reader)]

    @app.get("/api/tokens", dependencies=authed)
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
    def apps(request: Request):
        installed = agent("apps.list")["apps"]
        if sessions.get(request.cookies.get(COOKIE)) is None:
            # API token: no generated app passwords, no custom container settings.
            installed = [{**a, "login": None, "custom": None} for a in installed]
        catalog = agent("apps.catalog", {"refresh": False})
        return {
            "installed": installed,
            "catalog": catalog["apps"],
            "catalog_refreshed": catalog.get("refreshed"),
            "gpu_available": catalog.get("gpu_available", False),
        }

    @app.post("/api/apps/catalog/refresh", dependencies=changing)
    def apps_refresh():
        return agent("apps.catalog", {"refresh": True})

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

    @app.get("/api/diagnostics", dependencies=authed)
    def diagnostics_download():
        # The agent puts the bundle into our runtime directory; it is handed out once.
        path = cfg.run_dir / "diagnostics.tar.gz"
        if not path.is_file():
            raise ApiError(404, "error.not_found")
        host = re.sub(r"[^a-z0-9-]", "", agent("system.info").get("hostname", "").lower())
        name = f"dahlia-diagnostics-{host or 'server'}-{time.strftime('%Y%m%d-%H%M')}.tar.gz"
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
        """Relay between xterm.js and dahlia-terminal (see terminal.py)."""
        from .terminal import frame

        session = sessions.get(ws.cookies.get(COOKIE))
        # A WebSocket is not covered by CORS: only pages of this server may open one.
        # nginx passes $host, which has no port; compare host names only.
        origin = urllib.parse.urlsplit(ws.headers.get("origin", "")).hostname
        host = urllib.parse.urlsplit("//" + ws.headers.get("host", "")).hostname
        if session is None or not origin or origin != host:
            await ws.close(code=4401)
            return
        await ws.accept()
        q = ws.query_params
        hello = {
            "user": q.get("user", "dahlia-admin"),
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
