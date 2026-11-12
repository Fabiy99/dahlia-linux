import json
import urllib.parse

import pytest
from dahlia_dashboard import home

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from dahlia_dashboard import setupcode  # noqa: E402
from dahlia_dashboard.app import Config, create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CSRF = {"X-Dahlia-Request": "1"}

FORECAST = {
    "current": {"temperature_2m": 8.6, "relative_humidity_2m": 91, "wind_speed_10m": 23.9,
                "weather_code": 61, "is_day": 0},
    "daily": {"time": ["2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15"],
              "weather_code": [61, 3, 0, 71],
              "temperature_2m_max": [9.1, 10.2, 12.0, 1.5],
              "temperature_2m_min": [4.0, 5.1, 6.3, -2.0]},
}
GEOCODE = {"results": [
    {"name": "Hamburg", "latitude": 53.55073, "longitude": 9.99302, "admin1": "Hamburg",
     "country": "Deutschland"},
    {"name": "Broken", "latitude": 200, "longitude": 0},
]}
HAMBURG = {"name": "Hamburg", "latitude": 53.5507, "longitude": 9.993, "country": "Deutschland"}


class FakeFetch:
    def __init__(self):
        self.urls = []
        self.fail = False

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise home.WeatherError("offline")
        return GEOCODE if url.startswith(home.GEOCODE_URL) else FORECAST


# --- module -------------------------------------------------------------------


def test_kind_maps_wmo_codes():
    assert [home.kind(c) for c in (0, 1, 2, 3, 45, 53, 65, 81, 73, 86, 95, 99, None, 42)] == [
        "clear", "clear", "partly", "cloudy", "fog", "rain", "rain", "rain", "snow", "snow",
        "storm", "storm", "cloudy", "cloudy"]


def test_clean_place_validates():
    assert home.clean_place({"name": " Hamburg ", "latitude": "53.550731", "longitude": 9.99})[
        "latitude"] == 53.5507
    for bad in ({"name": "", "latitude": 1, "longitude": 1},
                {"name": "x" * 81, "latitude": 1, "longitude": 1},
                {"name": "x", "latitude": 91, "longitude": 1},
                {"name": "x", "latitude": 1},
                {"name": "x", "latitude": "north", "longitude": 1}):
        with pytest.raises(ValueError):
            home.clean_place(bad)


def test_store_defaults_and_roundtrip(tmp_path):
    store = home.HomeStore(tmp_path / "home.json")
    assert store.get() == {"widgets": home.DEFAULT_WIDGETS, "places": []}
    store.set({"history": False}, [HAMBURG])
    data = store.get()
    assert data["widgets"]["history"] is False and data["widgets"]["weather"] is True
    assert data["places"] == [HAMBURG]
    with pytest.raises(ValueError):
        store.set({"bogus": True}, [])
    with pytest.raises(ValueError):
        store.set({}, [HAMBURG] * (home.MAX_PLACES + 1))


def test_store_ignores_broken_file(tmp_path):
    path = tmp_path / "home.json"
    path.write_text(json.dumps({"widgets": {"cpu": "no", "bogus": False},
                                "places": [{"name": "x"}, HAMBURG]}))
    data = home.HomeStore(path).get()
    assert data["widgets"]["cpu"] is True and "bogus" not in data["widgets"]
    assert data["places"] == [HAMBURG]
    path.write_text("{not json")
    assert home.HomeStore(path).get()["places"] == []


def test_search_filters_and_builds_query():
    fetch = FakeFetch()
    assert home.search("Ham", "de", fetch) == [
        {"name": "Hamburg", "latitude": 53.5507, "longitude": 9.993,
         "country": "Hamburg, Deutschland"}]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(fetch.urls[0]).query)
    assert query["name"] == ["Ham"] and query["language"] == ["de"]
    assert home.search("H", "de", fetch) == []
    home.search("Paris", "xx", fetch)
    assert "language=en" in fetch.urls[-1]


def test_weather_parses_and_caches():
    fetch = FakeFetch()
    now = [1000.0]
    weather = home.Weather(fetch=fetch, clock=lambda: now[0])
    [p] = weather.all([HAMBURG])
    assert p["kind"] == "rain" and p["is_day"] is False and p["temperature"] == 8.6
    assert p["days"][3] == {"date": "2026-11-15", "max": 1.5, "min": -2.0, "kind": "snow"}
    weather.all([HAMBURG])
    assert len(fetch.urls) == 1
    now[0] += home.CACHE_SECONDS + 1
    weather.all([HAMBURG])
    assert len(fetch.urls) == 2


def test_weather_reports_errors_per_place():
    fetch = FakeFetch()
    fetch.fail = True
    [p] = home.Weather(fetch=fetch).all([HAMBURG])
    assert p["error"] == "offline" and p["name"] == "Hamburg"


# --- endpoints ----------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    fetch = FakeFetch()
    cfg = Config(state_dir=tmp_path / "state", run_dir=tmp_path / "run", secure_cookie=False,
                 agent_call=lambda action, params: {}, system_account=lambda: "fabian",
                 weather_fetch=fetch)
    cfg.state_dir.mkdir()
    code = setupcode.prepare(cfg.run_dir)
    c = TestClient(create_app(cfg))
    r = c.post("/api/setup", json={"code": code, "username": "fabian",
                                   "password": "correct horse battery"}, headers=CSRF)
    assert r.status_code == 200
    return c, fetch


def test_home_endpoints(client):
    c, _ = client
    assert c.get("/api/home").json()["places"] == []
    body = {"widgets": {"history": False}, "places": [HAMBURG]}
    assert c.post("/api/home", json=body).status_code == 403
    r = c.post("/api/home", json=body, headers=CSRF)
    assert r.status_code == 200 and r.json()["widgets"]["history"] is False
    assert c.get("/api/home").json()["places"] == [HAMBURG]
    bad = c.post("/api/home", json={"widgets": {"nope": True}, "places": []}, headers=CSRF)
    assert bad.status_code == 400


def test_weather_endpoints(client):
    c, fetch = client
    c.post("/api/home", json={"widgets": {}, "places": [HAMBURG]}, headers=CSRF)
    [p] = c.get("/api/weather").json()["places"]
    assert p["name"] == "Hamburg" and p["kind"] == "rain"
    res = c.get("/api/weather/search", params={"q": "Hamburg", "lang": "de"}).json()
    assert res["results"][0]["country"] == "Hamburg, Deutschland"
    fetch.fail = True
    r = c.get("/api/weather/search", params={"q": "Berlin"})
    assert r.status_code == 502 and r.json()["error"] == "error.weather"


def test_home_needs_login(tmp_path):
    cfg = Config(state_dir=tmp_path, run_dir=tmp_path / "run", secure_cookie=False,
                 agent_call=lambda action, params: {}, weather_fetch=FakeFetch())
    c = TestClient(create_app(cfg))
    assert c.get("/api/home").status_code == 401
    assert c.get("/api/weather").status_code == 401
    assert c.get("/api/weather/search", params={"q": "x"}).status_code == 401


# --- local screen ---------------------------------------------------------------

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
WWW = ROOT / "packages/dashboard/usr/share/dashboard/www"


class ScreenAgent:
    def __call__(self, action, params):
        if action == "system.info":
            return {"hostname": "dahlia", "version": "1.3.0"}
        if action == "system.stats":
            return {"network": [{"addresses": ["fe80::1/64", "192.168.1.20/24"]}],
                    "filesystems": [{"mountpoint": "/", "used": 1, "size": 4},
                                    {"mountpoint": "/mnt/user", "used": 5, "size": 10}],
                    "memory": {"total": 16, "available": 12}, "uptime_seconds": 99}
        if action == "apps.list":
            return {"apps": [{"status": "running"}, {"status": "exited"}]}
        if action == "system.health":
            return {"findings": [], "passed": ["disks"], "skipped": []}
        return {}


def screen_client(tmp_path, monkeypatch, host="127.0.0.1"):
    from dahlia_dashboard import app as appmod

    monkeypatch.setattr(appmod, "qr_svg", lambda text: f"<svg>{text}</svg>")
    cfg = Config(state_dir=tmp_path / "state", run_dir=tmp_path / "run", secure_cookie=False,
                 agent_call=ScreenAgent(), system_account=lambda: None, weather_fetch=FakeFetch())
    cfg.state_dir.mkdir(parents=True)
    code = setupcode.prepare(cfg.run_dir)
    return TestClient(create_app(cfg), client=(host, 50000)), code


def test_screen_on_loopback(tmp_path, monkeypatch):
    c, code = screen_client(tmp_path, monkeypatch)
    data = c.get("/api/screen").json()
    assert data["hostname"] == "dahlia" and data["address"] == "192.168.1.20"
    assert data["url"] == "https://192.168.1.20/"
    assert data["qr"] == "<svg>https://192.168.1.20/</svg>"
    assert data["setup_code"] == code
    assert data["storage"] == {"used": 5, "size": 10}
    assert data["memory"] == {"used": 4, "size": 16}
    assert data["apps"] == {"running": 1, "total": 2}
    assert data["health"]["passed"] == ["disks"]
    assert data["weather"] == [] and data["widgets"]["weather"] is True


def test_screen_hides_code_after_setup(tmp_path, monkeypatch):
    c, code = screen_client(tmp_path, monkeypatch)
    c.post("/api/setup", json={"code": code, "username": "fabian",
                               "password": "correct horse battery"}, headers=CSRF)
    assert c.get("/api/screen").json()["setup_code"] is None


def test_screen_refused_from_network(tmp_path, monkeypatch):
    c, _ = screen_client(tmp_path, monkeypatch, host="192.168.1.50")
    assert c.get("/api/screen").status_code == 404
    c, _ = screen_client(tmp_path / "b", monkeypatch)
    assert c.get("/api/screen", headers={"X-Forwarded-For": "192.168.1.50"}).status_code == 404


def test_nginx_keeps_screen_local():
    conf = (ROOT / "packages/dashboard/etc/nginx/sites-available/dahlia").read_text()
    network, _, local = conf.partition("listen 127.0.0.1:8090;")
    assert "location = /api/screen {\n        return 404;" in network
    assert "proxy_pass http://127.0.0.1:8080;" in local and "X-Forwarded-For" not in local


def test_screen_texts_are_translated():
    import re

    keys = set(re.findall(r'\bt\("([a-z_.-]+)"', (WWW / "screen.js").read_text()))
    keys |= set(re.findall(r'data-i18n="([^"]+)"', (WWW / "screen.html").read_text()))
    for name in ("de", "en"):
        assert keys <= set(json.loads((WWW / "locales" / f"{name}.json").read_text()))


def test_ca_fingerprint(tmp_path):
    import base64
    import hashlib

    from dahlia_dashboard.app import ca_fingerprint

    der = bytes(range(40))
    pem = tmp_path / "ca.crt"
    body = base64.b64encode(der).decode()
    pem.write_text("-----BEGIN CERTIFICATE-----\n" + body[:32] + "\n" + body[32:]
                   + "\n-----END CERTIFICATE-----\n")
    expected = ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())
    assert ca_fingerprint(pem) == expected
    assert ca_fingerprint(tmp_path / "missing.crt") is None
    pem.write_text("garbage")
    assert ca_fingerprint(pem) is None


def test_pairing_endpoint(client, tmp_path):
    c, _ = client
    data = c.get("/api/pairing").json()
    assert "ca_fingerprint" in data
