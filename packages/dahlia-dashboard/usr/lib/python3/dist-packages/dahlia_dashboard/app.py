"""HTTP API of the dashboard.

nginx serves the static frontend and proxies /api/ to this app on
127.0.0.1:8080. Everything that needs root goes through dahlia-agent.

Errors are returned as {"error": "<key>", "detail": "..."}. The frontend
translates the key via locales/*.json; detail is the untranslated message
from the agent or the system, shown as additional information.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from . import setupcode
from .auth import AuthError, RateLimiter, Session, SessionStore, UserStore

COOKIE = "dahlia_session"
CSRF_HEADER = "x-dahlia-request"

JOBS = ("mover", "snapraid-sync", "snapraid-scrub", "update")
STARTABLE_JOBS = ("mover", "snapraid-sync", "snapraid-scrub")
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
    secure_cookie: bool = True
    agent_call: AgentCall | None = None
    sessions: SessionStore = field(default_factory=SessionStore)
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


class AppRemoveBody(BaseModel):
    delete_data: bool = False


class DomainsBody(BaseModel):
    domain: str
    mdns_aliases: bool


class DiskRemoveBody(BaseModel):
    uuid: str
    force: bool = False


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    users = UserStore(cfg.state_dir / "users.json")
    sessions = cfg.sessions
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

    @app.post("/api/storage/remove", dependencies=changing)
    def storage_remove(body: DiskRemoveBody):
        return agent("storage.remove", {"uuid": body.uuid, "force": body.force})

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

    # --- apps -----------------------------------------------------------------

    @app.get("/api/apps", dependencies=authed)
    def apps():
        installed = agent("apps.list")["apps"]
        catalog = agent("apps.catalog", {"refresh": False})
        return {
            "installed": installed,
            "catalog": catalog["apps"],
            "catalog_refreshed": catalog.get("refreshed"),
        }

    @app.post("/api/apps/catalog/refresh", dependencies=changing)
    def apps_refresh():
        return agent("apps.catalog", {"refresh": True})

    @app.get("/api/apps/{app_id}/job", dependencies=authed)
    def app_job(app_id: str):
        return agent("apps.job", {"id": app_id})

    @app.post("/api/apps/{app_id}/{op}", dependencies=changing)
    def app_op(app_id: str, op: str, body: AppRemoveBody | None = None):
        if op not in APP_OPS:
            raise ApiError(404, "error.not_found")
        params = {"id": app_id}
        if op == "remove" and body is not None:
            params["delete_data"] = body.delete_data
        return agent(f"apps.{op}", params)

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

    @app.get("/api/log", dependencies=authed)
    def system_log(lines: int = 300, priority: str = "info", unit: str = ""):
        return agent("system.log", {"lines": lines, "priority": priority, "unit": unit})

    # --- domains --------------------------------------------------------------

    @app.get("/api/domains", dependencies=authed)
    def domains():
        return agent("domains.get")

    @app.post("/api/domains", dependencies=changing)
    def domains_set(body: DomainsBody):
        return agent("domains.set", {"domain": body.domain, "mdns_aliases": body.mdns_aliases})

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
