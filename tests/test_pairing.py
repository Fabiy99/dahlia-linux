import base64
import hashlib
import hmac

import pytest
from dahlia_dashboard import pairing

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from dahlia_dashboard import app as appmod  # noqa: E402
from dahlia_dashboard import setupcode  # noqa: E402
from dahlia_dashboard.app import Config, create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CSRF = {"X-Dahlia-Request": "1"}
DER = bytes(range(50))
NONCE = bytes(range(32))


def test_code_format():
    code = pairing.Codes().create()
    assert len(code) == pairing.LENGTH and set(code) <= set(pairing.ALPHABET)
    shown = pairing.display(code)
    assert shown[5] == "-" and pairing.normalize(shown.lower()) == code


def test_code_used_once_and_expires():
    now = [0.0]
    codes = pairing.Codes(clock=lambda: now[0])
    code = codes.create()
    assert codes.take() == code
    assert codes.take() is None
    codes.create()
    now[0] += pairing.TTL + 1
    assert codes.take() is None


def test_new_code_replaces_old():
    codes = pairing.Codes()
    codes.create()
    second = codes.create()
    assert codes.take() == second


def test_proof_matches_desktop_app():
    # Same construction as desktop/src-tauri/src/pairing.rs (proof()).
    msg = b"dahlia-pairing\0" + NONCE + hashlib.sha256(DER).digest()
    expected = hmac.new(b"ABCDEFGHJK", msg, hashlib.sha256).hexdigest()
    assert expected == "f613c2eb6eadd6c08d622c35bb99c8980791618551b1d44c250fed92c5e47a46"
    assert pairing.proof("abcde-fghjk", NONCE, DER) == expected
    assert pairing.proof("abcde-fghjk", NONCE, DER + b"x") != expected


class Agent:
    def __call__(self, action, params):
        if action == "system.stats":
            return {"network": [{"addresses": ["fe80::1/64", "192.168.1.20/24"]}]}
        return {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "qr_svg", lambda text: f"<svg>{text}</svg>")
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n" + base64.b64encode(DER).decode()
                  + "\n-----END CERTIFICATE-----\n")
    cfg = Config(state_dir=tmp_path / "state", run_dir=tmp_path / "run", secure_cookie=False,
                 agent_call=Agent(), system_account=lambda: "fabian", ca_cert=ca)
    cfg.state_dir.mkdir()
    code = setupcode.prepare(cfg.run_dir)
    c = TestClient(create_app(cfg))
    r = c.post("/api/setup", json={"code": code, "username": "fabian",
                                   "password": "correct horse battery"}, headers=CSRF)
    assert r.status_code == 200
    return c


def test_pairing_info_has_phone_link(client):
    data = client.get("/api/pairing").json()
    assert data["url"] == "https://192.168.1.20/"
    assert data["qr"] == "<svg>https://192.168.1.20/</svg>"
    assert data["ca_fingerprint"] == ":".join(f"{b:02X}" for b in hashlib.sha256(DER).digest())


def test_pairing_code_and_proof(client):
    assert client.post("/api/pairing/code").status_code == 403
    code = client.post("/api/pairing/code", headers=CSRF).json()["code"]
    client.post("/api/logout", headers=CSRF)
    # The desktop app is not logged in.
    r = client.post("/api/pairing/proof", json={"nonce": NONCE.hex()}, headers=CSRF)
    assert r.status_code == 200
    assert r.json()["proof"] == pairing.proof(code, NONCE, DER)
    again = client.post("/api/pairing/proof", json={"nonce": NONCE.hex()}, headers=CSRF)
    assert again.status_code == 404 and again.json()["error"] == "error.pairing_code"


def test_pairing_proof_checks_input(client):
    client.post("/api/pairing/code", headers=CSRF)
    for nonce in ("zz", "00" * 8, ""):
        r = client.post("/api/pairing/proof", json={"nonce": nonce}, headers=CSRF)
        assert r.status_code == 400
    assert client.post("/api/pairing/proof", json={"nonce": NONCE.hex()}).status_code == 403


def test_pairing_needs_login(tmp_path):
    cfg = Config(state_dir=tmp_path, run_dir=tmp_path / "run", secure_cookie=False,
                 agent_call=Agent())
    c = TestClient(create_app(cfg))
    assert c.get("/api/pairing").status_code == 401
    assert c.post("/api/pairing/code", headers=CSRF).status_code in (401, 403)
    r = c.post("/api/pairing/proof", json={"nonce": NONCE.hex()}, headers=CSRF)
    assert r.status_code == 404
