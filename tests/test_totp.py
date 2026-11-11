"""Second factor (TOTP) for dashboard accounts."""

import base64
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from dahlia_dashboard import app as app_mod  # noqa: E402
from dahlia_dashboard import auth, main, setupcode  # noqa: E402
from dahlia_dashboard.app import Config, create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_dashboard import CSRF, PASSWORD, FakeAgent  # noqa: E402

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


def test_hotp_rfc_vectors():
    # RFC 6238 appendix B (SHA-1), last six digits.
    assert auth.hotp(RFC_SECRET, 59 // 30) == "287082"
    assert auth.hotp(RFC_SECRET, 1111111109 // 30) == "081804"
    assert auth.hotp(RFC_SECRET, 2000000000 // 30) == "279037"


def test_totp_window_and_replay():
    now = 1111111109
    code = auth.hotp(RFC_SECRET, now // 30)
    assert auth.totp_match(RFC_SECRET, code, now) == now // 30
    assert auth.totp_match(RFC_SECRET, code, now + 30) == now // 30
    assert auth.totp_match(RFC_SECRET, code, now + 90) is None
    assert auth.totp_match(RFC_SECRET, code, now, after=now // 30) is None
    assert auth.totp_match(RFC_SECRET, "12345", now) is None
    assert auth.totp_match(RFC_SECRET, "abcdef", now) is None


def test_uri():
    uri = auth.totp_uri("ABC", "fabian@dahlia", "Dahlia")
    assert uri == ("otpauth://totp/Dahlia%3Afabian%40dahlia?secret=ABC&issuer=Dahlia"
                   "&algorithm=SHA1&digits=6&period=30")


class Clock:
    def __init__(self):
        self.now = 1_794_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "qr_svg", lambda text: "<svg>qr</svg>")
    clock = Clock()
    cfg = Config(state_dir=tmp_path / "state", run_dir=tmp_path / "run", secure_cookie=False,
                 agent_call=FakeAgent(), clock=clock)
    cfg.state_dir.mkdir()
    code = setupcode.prepare(cfg.run_dir)
    client = TestClient(create_app(cfg))
    body = {"code": code, "username": "fabian", "password": PASSWORD}
    assert client.post("/api/setup", json=body, headers=CSRF).status_code == 200
    return client, cfg, clock


def enable(client, clock):
    r = client.post("/api/account/totp/begin", json={"password": PASSWORD}, headers=CSRF)
    assert r.status_code == 200, r.json()
    secret = r.json()["secret"]
    code = auth.hotp(secret, int(clock.now // 30))
    r = client.post("/api/account/totp/confirm", json={"code": code}, headers=CSRF)
    assert r.status_code == 200, r.json()
    return secret, r.json()["recovery_codes"]


def login(client, **extra):
    return client.post("/api/login", json={"username": "fabian", "password": PASSWORD, **extra},
                       headers=CSRF)


def test_begin_needs_password(env):
    client, cfg, _ = env
    r = client.post("/api/account/totp/begin", json={"password": "wrong"}, headers=CSRF)
    assert r.json()["error"] == "error.old_password"
    r = client.post("/api/account/totp/begin", json={"password": PASSWORD}, headers=CSRF)
    data = r.json()
    assert data["uri"].startswith("otpauth://totp/Dahlia%3Afabian%40dahlia?secret=")
    assert data["qr_svg"] == "<svg>qr</svg>" and len(data["secret"]) == 32
    # Pending only: login still works without a code.
    assert client.get("/api/account/totp").json() == {"enabled": False, "recovery_left": 0}


def test_confirm_needs_right_code(env):
    client, _, clock = env
    r = client.post("/api/account/totp/confirm", json={"code": "123456"}, headers=CSRF)
    assert r.json()["error"] == "error.totp_not_started"
    secret = client.post("/api/account/totp/begin", json={"password": PASSWORD},
                         headers=CSRF).json()["secret"]
    wrong = auth.hotp(secret, int(clock.now // 30) + 5)
    r = client.post("/api/account/totp/confirm", json={"code": wrong}, headers=CSRF)
    assert r.json()["error"] == "error.totp_code"


def test_enable_and_login_with_code(env):
    client, cfg, clock = env
    other = TestClient(client.app)
    assert login(other).status_code == 200
    secret, codes = enable(client, clock)
    assert len(codes) == 10 and len(set(codes)) == 10
    # Other browsers are logged out, this one stays.
    assert other.get("/api/system").status_code == 401
    assert client.get("/api/account/totp").json() == {"enabled": True, "recovery_left": 10}
    stored = json.loads((cfg.state_dir / "users.json").read_text())["users"]["fabian"]
    assert stored["totp"] == secret and codes[0] not in json.dumps(stored)

    client.post("/api/logout", headers=CSRF)
    r = login(client)
    assert r.status_code == 401 and r.json()["error"] == "error.totp_required"
    assert "set-cookie" not in r.headers
    r = login(client, code="000000" if auth.hotp(secret, int(clock.now // 30)) != "000000"
              else "111111")
    assert r.json()["error"] == "error.totp_code"
    # The code used to confirm cannot be used again.
    r = login(client, code=auth.hotp(secret, int(clock.now // 30)))
    assert r.json()["error"] == "error.totp_code"
    clock.now += 30
    r = login(client, code=auth.hotp(secret, int(clock.now // 30)))
    assert r.status_code == 200
    assert client.get("/api/system").status_code == 200


def test_wrong_password_does_not_reveal_totp(env):
    client, _, clock = env
    enable(client, clock)
    client.post("/api/logout", headers=CSRF)
    r = client.post("/api/login", json={"username": "fabian", "password": "nope"}, headers=CSRF)
    assert r.json()["error"] == "error.invalid_credentials"


def test_recovery_code_works_once(env):
    client, _, clock = env
    _, codes = enable(client, clock)
    client.post("/api/logout", headers=CSRF)
    assert login(client, code=codes[3].upper()).status_code == 200
    client.post("/api/logout", headers=CSRF)
    assert login(client, code=codes[3]).json()["error"] == "error.totp_code"
    assert login(client, code=codes[4].replace("-", "")).status_code == 200
    assert client.get("/api/account/totp").json()["recovery_left"] == 8


def test_wrong_codes_are_rate_limited(env):
    client, _, clock = env
    enable(client, clock)
    client.post("/api/logout", headers=CSRF)
    for _ in range(5):
        login(client, code="99999x")
    assert login(client, code="123456").status_code == 429


def test_disable(env):
    client, _, clock = env
    secret, codes = enable(client, clock)
    clock.now += 30
    code = auth.hotp(secret, int(clock.now // 30))
    r = client.post("/api/account/totp/disable", json={"password": "wrong", "code": code},
                    headers=CSRF)
    assert r.json()["error"] == "error.old_password"
    r = client.post("/api/account/totp/disable", json={"password": PASSWORD, "code": "000001"},
                    headers=CSRF)
    assert r.json()["error"] == "error.totp_code"
    r = client.post("/api/account/totp/disable", json={"password": PASSWORD, "code": codes[0]},
                    headers=CSRF)
    assert r.status_code == 200
    client.post("/api/logout", headers=CSRF)
    assert login(client).status_code == 200


def test_totp_needs_login_and_csrf(env):
    client, _, _ = env
    client.post("/api/logout", headers=CSRF)
    assert client.get("/api/account/totp").status_code == 401
    assert client.post("/api/account/totp/begin", json={"password": PASSWORD},
                       headers=CSRF).status_code == 401
    login(client)
    assert client.post("/api/account/totp/begin",
                       json={"password": PASSWORD}).status_code == 403


def test_cli_disable_totp(env, monkeypatch, capsys):
    client, cfg, clock = env
    enable(client, clock)
    monkeypatch.setattr(main, "Config", lambda: cfg)
    assert main.main(["--disable-totp", "fabian"]) == 0
    assert "is off" in capsys.readouterr().out
    assert not auth.UserStore(cfg.state_dir / "users.json").totp_enabled("fabian")
    assert main.main(["--disable-totp", "nobody"]) == 1
