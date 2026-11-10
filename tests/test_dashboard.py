import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from dahlia_agent.apps import CATEGORIES  # noqa: E402
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
            return {"apps": [{"id": "whoami", "refresh": params.get("refresh")}],
                    "refreshed": "2026-11-10T12:00:00Z"}
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
    for path in ("system", "stats", "storage", "jobs"):
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
    client.post("/api/storage/format", json={"device": "/dev/sdb", "filesystem": "xfs",
                                             "confirm": "sdb"}, headers=CSRF)
    client.post("/api/storage/import", json={"uuid": "1234-ABCD", "target": "old"}, headers=CSRF)
    client.get("/api/storage/import")
    client.post("/api/jobs/mover/start", headers=CSRF)
    client.post("/api/updates/apply", headers=CSRF)
    client.post("/api/system/hostname", json={"hostname": "nas"}, headers=CSRF)
    assert agent.calls == [
        ("storage.add", {"uuid": "1234-ABCD", "role": "data"}),
        ("storage.remove", {"uuid": "1234-ABCD", "force": True}),
        ("storage.format", {"device": "/dev/sdb", "filesystem": "xfs", "confirm": "sdb"}),
        ("storage.import", {"uuid": "1234-ABCD", "target": "old"}),
        ("storage.import_status", {}),
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
    keys = set(re.findall(r'data-i18n(?:-placeholder|-title)?="([^"]+)"', html))
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
    keys |= {f"category.{c}" for c in CATEGORIES}
    keys |= {"app_status.partial"}
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
    assert data["catalog_refreshed"] == "2026-11-10T12:00:00Z"
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


def test_stats_endpoint(env):
    client, agent, _ = logged_in(env)
    assert client.get("/api/stats").json() == {"action": "system.stats"}
    assert ("system.stats", {}) in agent.calls


def test_docker_and_hardware_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    client.get("/api/docker")
    client.get("/api/docker/stats")
    client.get("/api/docker/disk")
    client.get("/api/docker/web/logs?lines=50")
    client.post("/api/docker/web/restart", headers=CSRF)
    client.post("/api/docker/prune", headers=CSRF)
    client.get("/api/hardware")
    client.get("/api/log?lines=100&priority=err&unit=ssh.service")
    assert agent.calls == [
        ("docker.list", {}),
        ("docker.stats", {}),
        ("docker.disk", {}),
        ("docker.logs", {"name": "web", "lines": 50}),
        ("docker.container", {"name": "web", "op": "restart"}),
        ("docker.prune", {}),
        ("system.hardware", {}),
        ("system.log", {"lines": 100, "priority": "err", "unit": "ssh.service"}),
    ]
    assert client.post("/api/docker/web/rm", headers=CSRF).status_code == 404
    assert client.post("/api/docker/web/stop").status_code == 403


def test_docker_needs_login(env):
    client, agent, _, _ = env
    assert client.get("/api/docker").status_code == 401
    assert client.post("/api/docker/web/stop", headers=CSRF).status_code == 401
    assert agent.calls == []


def test_web_app_manifest():
    manifest = json.loads((WWW / "manifest.webmanifest").read_text())
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        assert (WWW / icon["src"].lstrip("/")).is_file(), icon["src"]
    html = (WWW / "index.html").read_text()
    for ref in ("/manifest.webmanifest", "/apple-touch-icon.png", "/favicon.svg"):
        assert f'href="{ref}"' in html
        assert (WWW / ref.lstrip("/")).is_file()


def test_settings_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    client.get("/api/settings/time")
    client.post("/api/settings/time", json={"timezone": "Europe/Berlin", "ntp": True},
                headers=CSRF)
    client.get("/api/settings/network")
    client.post("/api/settings/network", json={"mode": "static", "address": "10.0.0.5/24",
                                                "gateway": "10.0.0.1", "dns": "10.0.0.1"},
                headers=CSRF)
    client.post("/api/settings/network/confirm", headers=CSRF)
    client.get("/api/settings/spindown")
    client.post("/api/settings/spindown", json={"default": 30, "disks": {"WD-1": 60}},
                headers=CSRF)
    client.get("/api/settings/schedules")
    client.post("/api/settings/schedules/mover", json={"frequency": "weekly", "time": "02:00",
                                                       "weekday": "sat"}, headers=CSRF)
    assert agent.calls == [
        ("settings.time", {}),
        ("settings.set_time", {"timezone": "Europe/Berlin", "ntp": True}),
        ("settings.network", {}),
        ("settings.set_network", {"mode": "static", "address": "10.0.0.5/24",
                                  "gateway": "10.0.0.1", "dns": "10.0.0.1"}),
        ("settings.confirm_network", {}),
        ("settings.spindown", {}),
        ("settings.set_spindown", {"default": 30, "disks": {"WD-1": 60}}),
        ("settings.schedules", {}),
        ("settings.set_schedule", {"job": "mover", "frequency": "weekly", "time": "02:00",
                                   "weekday": "sat", "monthday": 1}),
    ]
    assert client.post("/api/settings/network", json={"mode": "dhcp"}).status_code == 403


def test_shares_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    client.get("/api/shares")
    client.post("/api/shares/media", json={"security": "private", "users": {"anna": "rw"}},
                headers=CSRF)
    client.post("/api/shares/media/remove", headers=CSRF)
    client.post("/api/smb-users", json={"name": "anna", "password": "geheim123"},
                headers=CSRF)
    client.post("/api/smb-users/anna/password", json={"password": "neu12345"}, headers=CSRF)
    client.post("/api/smb-users/anna/remove", headers=CSRF)
    assert agent.calls == [
        ("shares.list", {}),
        ("shares.set", {"name": "media", "comment": "", "security": "private",
                        "users": {"anna": "rw"}, "time_machine": False}),
        ("shares.remove", {"name": "media"}),
        ("shares.user_add", {"name": "anna", "password": "geheim123"}),
        ("shares.user_password", {"name": "anna", "password": "neu12345"}),
        ("shares.user_remove", {"name": "anna"}),
    ]
    assert client.post("/api/shares/media/remove").status_code == 403


def test_notify_and_ups_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    client.get("/api/notify")
    client.post("/api/notify/ntfy", json={"settings": {"enabled": True, "topic": "x"}},
                headers=CSRF)
    client.post("/api/notify/events", json={"events": {"usage": False}}, headers=CSRF)
    client.post("/api/notify/test", headers=CSRF)
    client.get("/api/ups")
    client.post("/api/ups", json={"mode": "usb", "shutdown_minutes": 5}, headers=CSRF)
    assert agent.calls == [
        ("notify.get", {}),
        ("notify.set", {"channel": "ntfy", "settings": {"enabled": True, "topic": "x"}}),
        ("notify.set_events", {"events": {"usage": False}}),
        ("notify.test", {}),
        ("ups.get", {}),
        ("ups.set", {"mode": "usb", "host": "", "port": 3493, "name": "ups", "user": "",
                     "password": "", "shutdown_minutes": 5}),
    ]
    assert client.post("/api/notify/test").status_code == 403


def test_nginx_ui_is_revalidated_and_keeps_security_headers():
    conf = (ROOT / "packages/dahlia-dashboard/etc/nginx/sites-available/dahlia").read_text()
    root = conf[conf.index("location / {"):]
    root = root[:root.index("}")]
    assert "Cache-Control no-cache" in root
    # add_header in a location drops the server-level ones; they must be repeated.
    for header in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy",
                   "Strict-Transport-Security"):
        assert header in root
