import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from dahlia_agent.protocol import AgentError  # noqa: E402
from dahlia_dashboard import auth, setupcode  # noqa: E402
from dahlia_dashboard.app import Config, create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WWW = ROOT / "packages/dahlia-dashboard/usr/share/dahlia-dashboard/www"
CSRF = {"X-Dahlia-Request": "1"}
PASSWORD = "correct horse battery"


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.errors = {}

    def __call__(self, action, params):
        self.calls.append((action, params))
        if action in self.errors:
            raise self.errors[action]
        if action == "system.info":
            return {"hostname": "dahlia", "version": "0.2.0"}
        if action in ("apps.list", "apps.catalog"):
            return {"apps": [{"id": "whoami", "refresh": params.get("refresh")}]}
        if action == "job.status":
            return {"job": params["job"], "active": "inactive"}
        return {"action": action}


@pytest.fixture
def env(tmp_path):
    agent = FakeAgent()
    cfg = Config(
        state_dir=tmp_path / "state",
        run_dir=tmp_path / "run",
        secure_cookie=False,
        agent_call=agent,
    )
    cfg.state_dir.mkdir()
    code = setupcode.prepare(cfg.run_dir)
    client = TestClient(create_app(cfg))
    return client, agent, cfg, code


def do_setup(client, code, **extra):
    body = {"code": code, "username": "fabian", "password": PASSWORD, **extra}
    return client.post("/api/setup", json=body, headers=CSRF)


def logged_in(env):
    client, agent, cfg, code = env
    assert do_setup(client, code).status_code == 200
    return client, agent, cfg


# --- setup --------------------------------------------------------------------


def test_state_before_setup(env):
    client, *_ = env
    assert client.get("/api/state").json() == {"setup_required": True, "user": None}


def test_setup_needs_code(env):
    client, _, cfg, _ = env
    r = do_setup(client, "WRONG-CODE")
    assert r.status_code == 403
    assert r.json()["error"] == "error.setup_code"
    assert not (cfg.state_dir / "users.json").exists()


def test_setup_creates_account_once(env):
    client, agent, cfg, code = env
    r = do_setup(client, code.lower().replace("-", " "), hostname="nas")
    assert r.status_code == 200
    assert ("system.set_hostname", {"hostname": "nas"}) in agent.calls
    assert client.get("/api/state").json() == {"setup_required": False, "user": "fabian"}
    assert setupcode.current(cfg.run_dir) is None
    assert not (cfg.run_dir / setupcode.ISSUE_FILE).exists()
    assert (cfg.state_dir / "users.json").stat().st_mode & 0o777 == 0o600
    assert PASSWORD not in (cfg.state_dir / "users.json").read_text()
    again = do_setup(client, code)
    assert again.status_code == 409


def test_setup_rejects_weak_credentials(env):
    client, _, _, code = env
    r = client.post(
        "/api/setup", json={"code": code, "username": "x", "password": PASSWORD}, headers=CSRF
    )
    assert r.json()["error"] == "error.username"
    r = client.post(
        "/api/setup", json={"code": code, "username": "fabian", "password": "short"}, headers=CSRF
    )
    assert r.json()["error"] == "error.password"
    assert client.get("/api/state").json()["setup_required"] is True


def test_setup_hostname_error_keeps_account(env):
    client, agent, _, code = env
    agent.errors["system.set_hostname"] = AgentError("invalid_params", "bad format")
    r = do_setup(client, code, hostname="Bad.Name")
    assert r.status_code == 200
    assert r.json()["hostname_error"] == "agent.invalid_params"
    assert client.get("/api/state").json()["user"] == "fabian"


def test_setup_code_attempts_are_limited(env):
    client, _, _, code = env
    for _ in range(5):
        do_setup(client, "AAAAA-AAAAA")
    r = do_setup(client, code)
    assert r.status_code == 429


# --- login and sessions -------------------------------------------------------


def test_csrf_header_required(env):
    client, _, _, code = env
    body = {"code": code, "username": "fabian", "password": PASSWORD}
    r = client.post("/api/setup", json=body)
    assert r.status_code == 403
    assert r.json()["error"] == "error.csrf"


def test_login_logout(env):
    client, _, _ = logged_in(env)
    client.post("/api/logout", headers=CSRF)
    assert client.get("/api/state").json()["user"] is None
    assert client.get("/api/system").status_code == 401
    r = client.post("/api/login", json={"username": "fabian", "password": "nope"}, headers=CSRF)
    assert r.status_code == 401
    assert r.json()["error"] == "error.invalid_credentials"
    r = client.post("/api/login", json={"username": "fabian", "password": PASSWORD}, headers=CSRF)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert client.get("/api/system").json()["hostname"] == "dahlia"


def test_login_rate_limit(env):
    client, _, _ = logged_in(env)
    client.post("/api/logout", headers=CSRF)
    for _ in range(5):
        client.post("/api/login", json={"username": "fabian", "password": "x"}, headers=CSRF)
    r = client.post("/api/login", json={"username": "fabian", "password": PASSWORD}, headers=CSRF)
    assert r.status_code == 429


def test_change_password(env):
    client, _, cfg = logged_in(env)
    other = TestClient(client.app)
    other.post("/api/login", json={"username": "fabian", "password": PASSWORD}, headers=CSRF)
    assert other.get("/api/system").status_code == 200
    new = "another long password"
    r = client.post("/api/account/password", json={"old": "wrong", "new": new}, headers=CSRF)
    assert r.json()["error"] == "error.old_password"
    r = client.post("/api/account/password", json={"old": PASSWORD, "new": new}, headers=CSRF)
    assert r.status_code == 200
    assert other.get("/api/system").status_code == 401
    assert client.get("/api/system").status_code == 200
    store = auth.UserStore(cfg.state_dir / "users.json")
    assert store.verify("fabian", new)
    assert not store.verify("fabian", PASSWORD)


def test_everything_needs_login(env):
    client, agent, _, _ = env
    for path in ("system", "storage", "jobs"):
        assert client.get(f"/api/{path}").status_code == 401
    for path in ("system/reboot", "storage/add", "jobs/mover/start", "updates/apply"):
        assert client.post(f"/api/{path}", json={}, headers=CSRF).status_code in (401, 422)
    assert agent.calls == []


# --- agent actions ------------------------------------------------------------


def test_actions_go_to_agent(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    client.post("/api/storage/add", json={"uuid": "1234-ABCD", "role": "data"}, headers=CSRF)
    client.post("/api/storage/remove", json={"uuid": "1234-ABCD", "force": True}, headers=CSRF)
    client.post("/api/jobs/mover/start", headers=CSRF)
    client.post("/api/updates/apply", headers=CSRF)
    client.post("/api/system/hostname", json={"hostname": "nas"}, headers=CSRF)
    assert agent.calls == [
        ("storage.add", {"uuid": "1234-ABCD", "role": "data"}),
        ("storage.remove", {"uuid": "1234-ABCD", "force": True}),
        ("job.start", {"job": "mover"}),
        ("updates.apply", {}),
        ("system.set_hostname", {"hostname": "nas"}),
    ]


def test_job_list_and_unknown_job(env):
    client, agent, _ = logged_in(env)
    jobs = client.get("/api/jobs").json()
    assert set(jobs) == {"mover", "snapraid-sync", "snapraid-scrub", "update"}
    r = client.post("/api/jobs/update/start", headers=CSRF)
    assert r.status_code == 404


@pytest.mark.parametrize(
    ("code", "status"),
    [("busy", 409), ("failed", 422), ("unavailable", 503), ("invalid_params", 400)],
)
def test_agent_errors(env, code, status):
    client, agent, _ = logged_in(env)
    agent.errors["job.start"] = AgentError(code, "detail text")
    r = client.post("/api/jobs/mover/start", headers=CSRF)
    assert r.status_code == status
    assert r.json() == {"error": f"agent.{code}", "detail": "detail text"}


def test_validation_error_format(env):
    client, _, _ = logged_in(env)
    r = client.post("/api/storage/add", json={"uuid": 5}, headers=CSRF)
    assert r.status_code == 422
    assert r.json()["error"] == "error.request"


def test_no_api_docs(env):
    client, *_ = env
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(path).status_code == 404


# --- auth helpers -------------------------------------------------------------


def test_password_hash_roundtrip():
    h = auth.hash_password("secret value")
    assert h.startswith("scrypt$")
    assert auth.verify_password("secret value", h)
    assert not auth.verify_password("other", h)
    assert not auth.verify_password("x", "garbage")


def test_session_expiry():
    now = [0.0]
    store = auth.SessionStore(max_age=100, idle=10, clock=lambda: now[0])
    token = store.create("a")
    now[0] = 5
    assert store.get(token)
    now[0] = 14
    assert store.get(token)
    now[0] = 30
    assert store.get(token) is None
    token = store.create("a")
    for t in range(31, 140, 9):
        now[0] = t
        alive = store.get(token)
    assert alive is None


def test_setup_code_format(tmp_path):
    code = setupcode.prepare(tmp_path)
    assert re.fullmatch(r"[2-9A-HJKMNP-Z]{5}-[2-9A-HJKMNP-Z]{5}", code)
    assert (tmp_path / setupcode.CODE_FILE).stat().st_mode & 0o777 == 0o600
    assert code in (tmp_path / setupcode.ISSUE_FILE).read_text()
    assert setupcode.check(tmp_path, code.lower())
    assert not setupcode.check(tmp_path, "ä" * 10)


# --- locales ------------------------------------------------------------------


def load_locale(name):
    return json.loads((WWW / "locales" / f"{name}.json").read_text(encoding="utf-8"))


def test_locales_have_same_keys():
    assert set(load_locale("de")) == set(load_locale("en"))


def used_keys():
    html = (WWW / "index.html").read_text()
    js = (WWW / "app.js").read_text()
    py = (ROOT / "packages/dahlia-dashboard/usr/lib/python3/dist-packages/dahlia_dashboard/app.py")
    keys = set(re.findall(r'data-i18n="([^"]+)"', html))
    keys |= set(re.findall(r'\bt\("([a-z_.-]+)"', js))
    keys |= set(re.findall(r'\["(overview\.[a-z_]+)",', js))
    keys |= set(re.findall(r'"(error\.[a-z_]+)"', py.read_text()))
    keys |= {f"error.{k}" for k in ("username", "password", "old_password", "exists")}
    keys |= {f"agent.{k}" for k in ("invalid_params", "busy", "failed", "unavailable", "forbidden",
                                    "timeout", "unknown_action", "bad_request", "internal")}
    keys |= {f"role.{r}" for r in ("data", "parity", "cache", "external")}
    keys |= {f"job.{j}" for j in ("mover", "snapraid-sync", "snapraid-scrub", "update")}
    keys |= {f"overview.{a}_started" for a in ("reboot", "poweroff")}
    keys |= {f"apps.{op}_{s}" for op in ("install", "update") for s in ("running", "done")}
    return keys


@pytest.mark.parametrize("lang", ["de", "en"])
def test_every_used_key_is_translated(lang):
    missing = used_keys() - set(load_locale(lang))
    assert not missing


def test_no_hardcoded_text_in_html():
    html = (WWW / "index.html").read_text()
    body = re.sub(r"<(script|style|template)[^>]*>.*?</\1>", "", html, flags=re.S)
    body = re.sub(r"<select id=\"lang\".*?</select>", "", body, flags=re.S)
    body = re.sub(r"<title>.*?</title>|<span class=\"brand\">.*?</span>", "", body)
    text = re.sub(r"<[^>]+>", " ", body)
    assert text.split() == []


def test_apps_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    data = client.get("/api/apps").json()
    assert data["installed"][0]["id"] == "whoami"
    assert ("apps.catalog", {"refresh": False}) in agent.calls
    client.post("/api/apps/catalog/refresh", headers=CSRF)
    client.post("/api/apps/whoami/install", headers=CSRF)
    client.post("/api/apps/whoami/remove", json={"delete_data": True}, headers=CSRF)
    client.post("/api/apps/whoami/stop", headers=CSRF)
    client.get("/api/apps/whoami/job")
    assert agent.calls[2:] == [
        ("apps.catalog", {"refresh": True}),
        ("apps.install", {"id": "whoami"}),
        ("apps.remove", {"id": "whoami", "delete_data": True}),
        ("apps.stop", {"id": "whoami"}),
        ("apps.job", {"id": "whoami"}),
    ]
    assert client.post("/api/apps/whoami/exec", headers=CSRF).status_code == 404


def test_domains_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    assert client.get("/api/domains").json() == {"action": "domains.get"}
    body = {"domain": "lab.home.arpa", "mdns_aliases": False}
    assert client.post("/api/domains", json=body, headers=CSRF).status_code == 200
    assert agent.calls == [("domains.get", {}), ("domains.set", body)]
    # Without the CSRF header nothing reaches the agent.
    assert client.post("/api/domains", json=body).status_code == 403
    assert client.post("/api/domains", json={"domain": "x"}, headers=CSRF).status_code == 422
    assert len(agent.calls) == 2


def test_domains_need_login(env):
    client, agent, _, _ = env
    assert client.get("/api/domains").status_code == 401
    assert client.post("/api/domains", json={"domain": "a.b", "mdns_aliases": True},
                       headers=CSRF).status_code == 401
    assert agent.calls == []
