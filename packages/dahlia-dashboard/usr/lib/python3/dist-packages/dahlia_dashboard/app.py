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
from .auth import AuthError, RateLimiter, Session, SessionStore, UserStore

COOKIE = "dahlia_session"
CSRF_HEADER = "x-dahlia-request"

JOBS = ("mover", "snapraid-sync", "snapraid-scrub", "update", "backup")
STARTABLE_JOBS = ("mover", "snapraid-sync", "snapraid-scrub", "backup")
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
    domain: str
    mdns_aliases: bool


class DiskImportBody(BaseModel):
    uuid: str
    target: str


class DiskFormatBody(BaseModel):
    device: str
    filesystem: str
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


class FirewallBody(BaseModel):
    enabled: bool
    networks: list[str] = []


class ShareBody(BaseModel):
    comment: str = ""
    security: str
    users: dict[str, str] = {}
    time_machine: bool = False


class SmbUserBody(BaseModel):
    name: str
    password: str


class SmbPasswordBody(BaseModel):
    password: str


class NotifyChannelBody(BaseModel):
    settings: dict


class NotifyEventsBody(BaseModel):
    events: dict[str, bool]


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


class ScheduleBody(BaseModel):
    frequency: str
    time: str = "03:00"
    weekday: str = "sun"
    monthday: int = 1


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    users = UserStore(cfg.state_dir / "users.json")
    sessions = cfg.sessions or SessionStore(path=cfg.state_dir / "sessions.json")
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

    # --- system ---------------------------------------------------------------

    authed = [Depends(require_user)]
    changing = [Depends(require_csrf), Depends(require_user)]

    @app.get("/api/system", dependencies=authed)
    def system():
        return agent("system.info")

    @app.get("/api/stats", dependencies=authed)
    def stats():
        return agent("system.stats")

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

    @app.get("/api/storage", dependencies=authed)
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
        return agent("storage.format", body.model_dump())

    @app.post("/api/storage/remove", dependencies=changing)
    def storage_remove(body: DiskRemoveBody):
        return agent("storage.remove", {"uuid": body.uuid, "force": body.force})

    # --- parity ---------------------------------------------------------------

    @app.get("/api/parity", dependencies=authed)
    def parity():
        return agent("parity.get")

    @app.post("/api/parity/sync", dependencies=changing)
    def parity_sync(body: ParitySyncBody):
        return agent("parity.sync", {"force": body.force})

    @app.post("/api/parity/fix", dependencies=changing)
    def parity_fix(body: ParityFixBody):
        return agent("parity.fix", {"what": body.what})

    @app.post("/api/parity/settings", dependencies=changing)
    def parity_settings(body: ParitySettingsBody):
        for name, value in body.model_dump().items():
            agent("storage.set_option", {"name": name, "value": value})
        return body.model_dump()

    # --- jobs and updates -----------------------------------------------------

    @app.get("/api/jobs", dependencies=authed)
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

    # --- apps -----------------------------------------------------------------

    @app.get("/api/apps", dependencies=authed)
    def apps():
        installed = agent("apps.list")["apps"]
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

    @app.get("/api/docker", dependencies=authed)
    def docker_list():
        return agent("docker.list")

    @app.get("/api/docker/stats", dependencies=authed)
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

    @app.get("/api/hardware", dependencies=authed)
    def hardware():
        return agent("system.hardware")

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

    @app.get("/api/settings/firewall", dependencies=authed)
    def settings_firewall():
        return agent("settings.firewall")

    @app.post("/api/settings/firewall", dependencies=changing)
    def settings_set_firewall(body: FirewallBody):
        return agent("settings.set_firewall", body.model_dump())

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
        return agent("domains.set", {"domain": body.domain, "mdns_aliases": body.mdns_aliases})

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
