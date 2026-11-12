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
    client.get("/api/updates/progress")
    client.post("/api/system/hostname", json={"hostname": "nas"}, headers=CSRF)
    assert agent.calls == [
        ("storage.add", {"uuid": "1234-ABCD", "role": "data"}),
        ("storage.remove", {"uuid": "1234-ABCD", "force": True}),
        ("storage.format", {"device": "/dev/sdb", "filesystem": "xfs", "confirm": "sdb"}),
        ("storage.import", {"uuid": "1234-ABCD", "target": "old"}),
        ("storage.import_status", {}),
        ("job.start", {"job": "mover"}),
        ("updates.apply", {}),
        ("updates.progress", {}),
        ("system.set_hostname", {"hostname": "nas"}),
    ]


def test_job_list_and_unknown_job(env):
    client, agent, _ = logged_in(env)
    jobs = client.get("/api/jobs").json()
    assert set(jobs) == {"mover", "snapraid-sync", "snapraid-scrub", "update", "backup",
                         "smart-short", "smart-long"}
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


def test_sessions_survive_restart(tmp_path):
    path = tmp_path / "sessions.json"
    now = [1000.0]
    store = auth.SessionStore(max_age=100, idle=10, clock=lambda: now[0], path=path)
    token = store.create("a")
    other = store.create("b")
    assert path.stat().st_mode & 0o777 == 0o600
    # Only hashes on disk.
    assert token not in path.read_text() and other not in path.read_text()
    store.drop(other)
    now[0] = 1005
    again = auth.SessionStore(max_age=100, idle=10, clock=lambda: now[0], path=path)
    assert again.get(token).username == "a"
    assert again.get(other) is None
    now[0] = 1100
    assert auth.SessionStore(max_age=100, idle=10, clock=lambda: now[0], path=path).get(
        token) is None


def test_sessions_last_seen_saved_now_and_then(tmp_path):
    path = tmp_path / "sessions.json"
    now = [0.0]
    store = auth.SessionStore(max_age=1000, idle=100, clock=lambda: now[0], path=path)
    token = store.create("a")
    for t in range(10, 200, 10):
        now[0] = t
        assert store.get(token)
    # The last write is at most SAVE_EVERY seconds old, so a restart keeps the session.
    again = auth.SessionStore(max_age=1000, idle=100, clock=lambda: now[0], path=path)
    assert again.get(token)


def test_sessions_damaged_file(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("{not json")
    store = auth.SessionStore(path=path)
    assert store.sessions == {}
    token = store.create("a")
    assert auth.SessionStore(path=path).get(token)


def test_password_change_drops_saved_sessions(tmp_path):
    path = tmp_path / "sessions.json"
    store = auth.SessionStore(path=path)
    keep = store.create("a")
    gone = store.create("a")
    store.drop_user("a", keep=keep)
    again = auth.SessionStore(path=path)
    assert again.get(keep) and again.get(gone) is None


def test_login_survives_dashboard_restart(env):
    client, agent, cfg = logged_in(env)
    cookie = client.cookies.get("dahlia_session")
    restarted = TestClient(create_app(cfg))
    restarted.cookies.set("dahlia_session", cookie)
    assert restarted.get("/api/apps").status_code == 200
    assert (cfg.state_dir / "sessions.json").exists()


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
    client.post("/api/apps/whoami/install",
                json={"folders": {"media": "/mnt/user/Filme"}, "location": "/srv"}, headers=CSRF)
    client.get("/api/files?path=Filme")
    client.get("/api/files?root=/srv")
    client.post("/api/files/mkdir", json={"path": "Filme/Neu"}, headers=CSRF)
    client.post("/api/files/mkdir", json={"root": "/srv", "path": "Neu"}, headers=CSRF)
    assert agent.calls[2:] == [
        ("apps.catalog", {"refresh": True}),
        ("apps.install", {"id": "whoami"}),
        ("apps.remove", {"id": "whoami", "delete_data": True}),
        ("apps.stop", {"id": "whoami"}),
        ("apps.job", {"id": "whoami"}),
        ("apps.install", {"id": "whoami", "folders": {"media": "/mnt/user/Filme"},
                          "location": "/srv"}),
        ("files.browse", {"root": "/mnt/user", "path": "Filme"}),
        ("files.browse", {"root": "/srv", "path": ""}),
        ("files.mkdir", {"root": "/mnt/user", "path": "Filme/Neu"}),
        ("files.mkdir", {"root": "/srv", "path": "Neu"}),
    ]
    assert client.post("/api/apps/whoami/exec", headers=CSRF).status_code == 404


def test_custom_container_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    spec = {"name": "Uptime Kuma", "image": "louislam/uptime-kuma:1.23.16"}
    client.post("/api/apps/kuma/install", json={"spec": spec, "location": "/srv"}, headers=CSRF)
    client.post("/api/apps/kuma/update", json={"spec": spec}, headers=CSRF)
    client.post("/api/apps/kuma/update", headers=CSRF)
    assert agent.calls == [
        ("apps.custom_install", {"id": "kuma", "spec": spec, "location": "/srv"}),
        ("apps.custom_update", {"id": "kuma", "spec": spec}),
        ("apps.update", {"id": "kuma"}),
    ]
    assert client.post("/api/apps/kuma/install", json={"spec": spec}).status_code == 403


def test_domains_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    assert client.get("/api/domains").json() == {"action": "domains.get"}
    body = {"mdns_aliases": False}
    assert client.post("/api/domains", json=body, headers=CSRF).status_code == 200
    assert agent.calls == [("domains.get", {}), ("domains.set", body)]
    # Without the CSRF header nothing reaches the agent.
    assert client.post("/api/domains", json=body).status_code == 403
    assert client.post("/api/domains", json={}, headers=CSRF).status_code == 422
    assert len(agent.calls) == 2


def test_domains_need_login(env):
    client, agent, _, _ = env
    assert client.get("/api/domains").status_code == 401
    assert client.post("/api/domains", json={"mdns_aliases": True},
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
    client.get("/api/settings/firewall")
    client.post("/api/settings/firewall", json={"enabled": True, "networks": ["10.8.0.0/24"]},
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
        ("settings.firewall", {}),
        ("settings.set_firewall", {"enabled": True, "networks": ["10.8.0.0/24"]}),
        ("settings.schedules", {}),
        ("settings.set_schedule", {"job": "mover", "frequency": "weekly", "time": "02:00",
                                   "weekday": "sat", "monthday": 1}),
    ]
    assert client.post("/api/settings/network", json={"mode": "dhcp"}).status_code == 403


def test_health_endpoint(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    client.get("/api/health")
    assert agent.calls == [("system.health", {})]


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
                        "users": {"anna": "rw"}, "time_machine": False,
                        "time_machine_size": 0, "nfs": False}),
        ("shares.remove", {"name": "media"}),
        ("shares.user_add", {"name": "anna", "password": "geheim123"}),
        ("shares.user_password", {"name": "anna", "password": "neu12345"}),
        ("shares.user_remove", {"name": "anna"}),
    ]
    assert client.post("/api/shares/media/remove").status_code == 403


def test_history_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    client.get("/api/history?range=week")
    client.get("/api/history/influx")
    client.post("/api/history/influx", json={"enabled": True, "url": "http://nas:8086",
                                             "org": "home", "bucket": "dahlia"}, headers=CSRF)
    assert agent.calls == [
        ("history.get", {"range": "week"}),
        ("history.influx", {}),
        ("history.set_influx", {"enabled": True, "url": "http://nas:8086", "version": 2,
                                "org": "home", "bucket": "dahlia", "database": "",
                                "username": ""}),
    ]
    assert client.post("/api/history/influx", json={"enabled": False}).status_code == 403


def test_remotes_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    client.get("/api/remotes")
    client.post("/api/remotes/nas", json={"type": "smb", "host": "nas.local", "share": "Fotos",
                                          "username": "anna", "password": "geheim"},
                headers=CSRF)
    # No password: the agent keeps the saved one.
    client.post("/api/remotes/nas", json={"type": "smb", "host": "nas.local", "share": "Fotos",
                                          "username": "anna", "readonly": True}, headers=CSRF)
    client.post("/api/remotes/nas/remove", json={"force": True}, headers=CSRF)
    base = {"name": "nas", "type": "smb", "host": "nas.local", "share": "Fotos",
            "username": "anna"}
    assert agent.calls == [
        ("remotes.list", {}),
        ("remotes.set", {**base, "password": "geheim", "readonly": False}),
        ("remotes.set", {**base, "readonly": True}),
        ("remotes.remove", {"name": "nas", "force": True}),
    ]
    assert client.post("/api/remotes/nas/remove", json={}).status_code == 403


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


def test_diagnostics_download_once(env):
    client, agent, cfg = logged_in(env)
    assert client.post("/api/diagnostics", headers=CSRF).json() == {"action": "system.diagnostics"}
    assert client.post("/api/diagnostics").status_code == 403
    assert client.get("/api/diagnostics").status_code == 404
    (cfg.run_dir / "diagnostics.tar.gz").write_bytes(b"\x1f\x8bbundle")
    r = client.get("/api/diagnostics")
    assert r.status_code == 200 and r.content == b"\x1f\x8bbundle"
    assert r.headers["content-type"] == "application/gzip"
    assert 'filename="dahlia-diagnostics-dahlia-' in r.headers["content-disposition"]
    assert not (cfg.run_dir / "diagnostics.tar.gz").exists()
    assert client.get("/api/diagnostics").status_code == 404


def test_diagnostics_needs_login(env):
    client, agent, cfg, _ = env
    (cfg.run_dir / "diagnostics.tar.gz").write_bytes(b"x")
    assert client.get("/api/diagnostics").status_code == 401
    assert client.post("/api/diagnostics", headers=CSRF).status_code == 401


def test_auto_updates_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    assert client.get("/api/updates/auto").json() == {"action": "updates.auto"}
    body = {"enabled": True, "reboot": True, "reboot_time": "03:30"}
    assert client.post("/api/updates/auto", json=body, headers=CSRF).status_code == 200
    assert client.post("/api/updates/auto", json=body).status_code == 403
    assert client.post("/api/updates/auto", json={"enabled": True}, headers=CSRF).status_code == 422
    assert agent.calls == [("updates.auto", {}), ("updates.set_auto", body)]


def test_snapshot_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    assert client.get("/api/snapshots").json() == {"action": "snapshots.list"}
    client.post("/api/snapshots", json={"description": "manual"}, headers=CSRF)
    client.post("/api/snapshots/5/delete", headers=CSRF)
    client.post("/api/snapshots/4/rollback", headers=CSRF)
    name = "@rootfs.pre-rollback-20261111-211354"
    client.post("/api/snapshots/old-root/delete", json={"name": name}, headers=CSRF)
    assert agent.calls == [
        ("snapshots.list", {}),
        ("snapshots.create", {"description": "manual"}),
        ("snapshots.delete", {"number": 5}),
        ("snapshots.rollback", {"number": 4}),
        ("snapshots.delete_old_root", {"name": name}),
    ]
    assert client.post("/api/snapshots/4/rollback").status_code == 403
    assert client.post("/api/snapshots/x/rollback", headers=CSRF).status_code == 422


def test_storage_settings_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    assert client.get("/api/storage/settings").json() == {"action": "storage.settings"}
    body = {"minfreespace": "100G", "mover_min_age_minutes": 1440,
            "snapraid_delete_threshold": 30}
    assert client.post("/api/storage/settings", json=body, headers=CSRF).json() == body
    assert agent.calls == [
        ("storage.settings", {}),
        ("storage.set_minfreespace", {"size": "100G"}),
        ("storage.set_option", {"name": "mover_min_age_minutes", "value": 1440}),
        ("storage.set_option", {"name": "snapraid_delete_threshold", "value": 30}),
    ]
    assert client.post("/api/storage/settings", json=body).status_code == 403
    assert client.post("/api/storage/settings", json={"minfreespace": "1G"},
                       headers=CSRF).status_code == 422


def test_parity_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    assert client.get("/api/parity").json() == {"action": "parity.get"}
    assert client.post("/api/parity/sync", json={}, headers=CSRF).status_code == 200
    assert client.post("/api/parity/sync", json={"force": True}, headers=CSRF).status_code == 200
    assert client.post("/api/parity/fix", json={"what": "disk2"}, headers=CSRF).status_code == 200
    opts = {"snapraid_delete_threshold": 20, "scrub_percent": 10, "scrub_older_than_days": 7}
    assert client.post("/api/parity/settings", json=opts, headers=CSRF).json() == opts
    assert agent.calls == [
        ("parity.get", {}),
        ("parity.sync", {"force": False}),
        ("parity.sync", {"force": True}),
        ("parity.fix", {"what": "disk2"}),
        ("storage.set_option", {"name": "snapraid_delete_threshold", "value": 20}),
        ("storage.set_option", {"name": "scrub_percent", "value": 10}),
        ("storage.set_option", {"name": "scrub_older_than_days", "value": 7}),
    ]
    assert client.post("/api/parity/fix", json={"what": "bad"}).status_code == 403
    assert client.post("/api/parity/settings", json={"scrub_percent": 5},
                       headers=CSRF).status_code == 422
    assert len(agent.calls) == 7


def test_backup_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    target = {"type": "folder", "path": "/mnt/disk2/dahlia-backup"}
    client.get("/api/backup")
    client.post("/api/backup", json={"target": target, "stop_apps": True}, headers=CSRF)
    client.post("/api/backup/key", headers=CSRF)
    client.get("/api/backup/snapshots")
    client.post("/api/backup/restore", json={"snapshot": "abc12345", "what": "whoami"},
                headers=CSRF)
    assert agent.calls == [
        ("backup.get", {}),
        ("backup.set", {"target": target, "stop_apps": True, "folders": []}),
        ("backup.key", {}),
        ("backup.snapshots", {}),
        ("backup.restore", {"snapshot": "abc12345", "what": "whoami"}),
    ]
    # The password needs the CSRF header.
    assert client.post("/api/backup/key").status_code == 403


def test_vpn_endpoints(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    assert client.get("/api/vpn").json() == {"action": "vpn.status"}
    wg = {"enabled": True, "port": 51820, "endpoint": "home.example.org", "route": "lan",
          "dns": "", "subnet": "10.253.0.0/24"}
    client.post("/api/vpn/wireguard", json=wg, headers=CSRF)
    client.post("/api/vpn/wireguard/peers", json={"name": "Handy"}, headers=CSRF)
    client.post("/api/vpn/wireguard/peers/delete", json={"name": "Handy"}, headers=CSRF)
    client.post("/api/vpn/tailscale/install", headers=CSRF)
    client.post("/api/vpn/tailscale/up", json={"routes": True, "exit_node": False}, headers=CSRF)
    client.post("/api/vpn/tailscale/logout", headers=CSRF)
    assert agent.calls == [
        ("vpn.status", {}),
        ("vpn.wireguard_set", wg),
        ("vpn.wireguard_add_peer", {"name": "Handy"}),
        ("vpn.wireguard_remove_peer", {"name": "Handy"}),
        ("vpn.tailscale_install", {}),
        ("vpn.tailscale_up", {"routes": True, "exit_node": False}),
        ("vpn.tailscale_logout", {}),
    ]
    assert client.post("/api/vpn/tailscale/install").status_code == 403


def test_disk_tests_routes(env):
    client, agent, _ = logged_in(env)
    agent.calls.clear()
    assert client.post("/api/disks/smart-test", json={"device": "/dev/sda", "kind": "long"},
                       headers=CSRF).status_code == 200
    client.get("/api/disks/burnin")
    client.post("/api/disks/burnin/start", json={"device": "/dev/sdb", "confirm": "sdb"},
                headers=CSRF)
    client.post("/api/disks/burnin/stop", json={"device": "/dev/sdb"}, headers=CSRF)
    client.post("/api/disks/burnin/clear", json={"device": "/dev/sdb"}, headers=CSRF)
    assert client.post("/api/disks/burnin/format", json={"device": "/dev/sdb"},
                       headers=CSRF).status_code == 404
    assert client.post("/api/disks/smart-test", json={"device": "/dev/sda", "kind": "long"}
                       ).status_code == 403
    assert agent.calls == [
        ("disks.smart_test", {"device": "/dev/sda", "kind": "long"}),
        ("disks.burnin_status", {}),
        ("disks.burnin_start", {"device": "/dev/sdb", "confirm": "sdb"}),
        ("disks.burnin_stop", {"device": "/dev/sdb"}),
        ("disks.burnin_clear", {"device": "/dev/sdb"}),
    ]
    assert client.post("/api/jobs/smart-short/start", headers=CSRF).status_code == 200
