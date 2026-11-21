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
    data = store.get()
    assert data["widgets"] == home.DEFAULT_WIDGETS and data["places"] == []
    assert [t["id"] for t in data["tiles"]] == list(home.WIDGETS)
    assert data["look"] == home.DEFAULT_LOOK
    store.set(widgets={"history": True}, places=[HAMBURG])
    data = store.get()
    assert data["widgets"]["history"] is True and data["widgets"]["weather"] is True
    assert data["places"] == [HAMBURG]
    with pytest.raises(ValueError):
        store.set(widgets={"bogus": True})
    with pytest.raises(ValueError):
        store.set(places=[HAMBURG] * (home.MAX_PLACES + 1))


def test_store_keeps_order_size_and_look(tmp_path):
    store = home.HomeStore(tmp_path / "home.json")
    tiles = [{"id": "system", "size": "xl", "on": True}, {"id": "weather", "size": "s", "on": True}]
    look = {"theme": "dark", "accent": "teal", "background": "still", "weather_fx": False}
    data = store.set(tiles=tiles, look=look)
    assert data["tiles"][:2] == tiles
    # Widgets left out of the request are kept, switched off, at the end.
    rest = [w for w in home.WIDGETS if w not in ("system", "weather")]
    assert [t["id"] for t in data["tiles"][2:]] == rest
    assert not any(t["on"] for t in data["tiles"][2:])
    # A change of the places alone keeps tiles and look.
    store.set(places=[HAMBURG])
    again = home.HomeStore(tmp_path / "home.json").get()
    assert again["tiles"] == data["tiles"] and again["look"] == look
    assert again["places"] == [HAMBURG]
    for bad in ([{"id": "nope", "size": "s", "on": True}],
                [{"id": "cpu", "size": "huge", "on": True}],
                [{"id": "cpu", "size": "s", "on": True}, {"id": "cpu", "size": "m", "on": True}]):
        with pytest.raises(ValueError):
            store.set(tiles=bad)
    for bad in ({"theme": "neon"}, {"weather_fx": "yes"}, {"font": "comic"}):
        with pytest.raises(ValueError):
            store.set(look=bad)


def test_store_reset_keeps_places(tmp_path):
    store = home.HomeStore(tmp_path / "home.json")
    store.set(tiles=[{"id": "cpu", "size": "xl", "on": True}], look={"theme": "light"},
              places=[HAMBURG])
    data = store.reset()
    assert data["tiles"] == home.default_tiles() and data["look"] == home.DEFAULT_LOOK
    assert data["places"] == [HAMBURG]


def test_store_ignores_broken_file(tmp_path):
    path = tmp_path / "home.json"
    tiles = [{"id": "cpu", "size": "giant", "on": True}, {"id": "bogus", "size": "s", "on": True},
             {"id": "weather", "size": "m", "on": "no"}, "junk"]
    path.write_text(json.dumps({"layout": home.LAYOUT, "tiles": tiles, "look": {"theme": "neon"},
                                "places": [{"name": "x"}, HAMBURG]}))
    data = home.HomeStore(path).get()
    assert data["tiles"][0] == {"id": "cpu", "size": home.DEFAULT_SIZES["cpu"], "on": True}
    assert data["tiles"][1] == {"id": "weather", "size": "m", "on": False}
    assert "bogus" not in data["widgets"] and len(data["tiles"]) == len(home.WIDGETS)
    assert data["look"] == home.DEFAULT_LOOK
    assert data["places"] == [HAMBURG]
    for broken in ("{not json", "[1, 2]"):
        path.write_text(broken)
        assert home.HomeStore(path).get()["places"] == []


def test_store_takes_over_layout_2(tmp_path):
    # An update from layout 2 keeps which widgets the user switched on.
    path = tmp_path / "home.json"
    path.write_text(json.dumps({"layout": 2, "widgets": {"weather": False, "cpu": True},
                                "places": [HAMBURG]}))
    data = home.HomeStore(path).get()
    assert data["widgets"]["weather"] is False and data["widgets"]["cpu"] is True
    assert data["widgets"]["apps"] is True and data["places"] == [HAMBURG]


def test_store_new_widgets_stay_off(tmp_path, monkeypatch):
    # A widget added by a later version does not appear on an arranged start screen.
    path = tmp_path / "home.json"
    home.HomeStore(path).set(tiles=[{"id": w, "size": "s", "on": True} for w in home.WIDGETS])
    monkeypatch.setattr(home, "WIDGETS", (*home.WIDGETS, "clock"))
    monkeypatch.setitem(home.DEFAULT_SIZES, "clock", "s")
    tiles = home.HomeStore(path).get()["tiles"]
    assert tiles[-1] == {"id": "clock", "size": "s", "on": False}
    assert all(t["on"] for t in tiles[:-1])


def test_store_resets_widgets_of_old_layout(tmp_path):
    # Before layout 2 every widget was on; the new start screen shows the design's set.
    path = tmp_path / "home.json"
    path.write_text(json.dumps({"widgets": {w: True for w in home.WIDGETS}, "places": [HAMBURG]}))
    data = home.HomeStore(path).get()
    assert data["widgets"] == home.DEFAULT_WIDGETS and data["places"] == [HAMBURG]
    shown = [w for w, on in data["widgets"].items() if on]
    assert shown == ["weather", "apps", "storage", "system", "jobs", "letter"]


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


def test_weather_waits_before_retrying_a_failed_place():
    fetch = FakeFetch()
    fetch.fail = True
    now = [1000.0]
    weather = home.Weather(fetch=fetch, clock=lambda: now[0])
    weather.all([HAMBURG])
    [p] = weather.all([HAMBURG])
    assert p["error"] == "offline" and len(fetch.urls) == 1
    fetch.fail = False
    now[0] += home.RETRY_SECONDS + 1
    [p] = weather.all([HAMBURG])
    assert "error" not in p and len(fetch.urls) == 2


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
    tiles = [{"id": "jobs", "size": "xl", "on": True}]
    r = c.post("/api/home", json={"tiles": tiles, "look": {"accent": "blue"}}, headers=CSRF)
    assert r.status_code == 200 and r.json()["tiles"][0] == tiles[0]
    assert r.json()["look"]["accent"] == "blue" and r.json()["places"] == [HAMBURG]
    assert c.post("/api/home", json={"look": {"accent": "pink"}}, headers=CSRF).status_code == 400
    assert c.post("/api/home/reset").status_code == 403
    r = c.post("/api/home/reset", headers=CSRF)
    assert r.json()["tiles"] == home.default_tiles() and r.json()["places"] == [HAMBURG]


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
        if action == "tiles.frame":
            return {"state": "ok", "photos": [PHOTO], "album": params["album"]}
        if action == "tiles.image":
            return {"type": "image/jpeg", "data": "/9g="}
        if action == "tiles.albums":
            return {"state": "ok", "albums": [{"id": PHOTO, "name": "Urlaub", "count": 3}]}
        return {}


PHOTO = "0b2e3c4d-1111-2222-3333-444455556666"


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


def test_photo_frame_on_the_local_screen(tmp_path, monkeypatch):
    c, code = screen_client(tmp_path, monkeypatch)
    assert c.get("/api/screen").json()["frame"] is None
    c.post("/api/setup", json={"code": code, "username": "fabian",
                               "password": "correct horse battery"}, headers=CSRF)
    got = c.get("/api/frame").json()
    assert got["enabled"] is False and got["albums"]["albums"][0]["name"] == "Urlaub"
    r = c.post("/api/frame", json={"enabled": True, "interval": 45, "album": PHOTO},
               headers=CSRF)
    assert r.json() == {"enabled": True, "interval": 45, "album": PHOTO, "clock": True}
    assert c.post("/api/frame", json={"interval": 1}, headers=CSRF).status_code == 400
    assert c.post("/api/frame", json={"album": "../x"}, headers=CSRF).status_code == 400
    assert c.get("/api/screen").json()["frame"] == {"interval": 45, "clock": True,
                                                    "photos": [PHOTO]}
    r = c.get(f"/api/screen/photo/{PHOTO}")
    assert r.status_code == 200 and r.content == b"\xff\xd8" and r.headers["content-type"] == "image/jpeg"
    assert c.get("/api/screen/photo/nope").status_code == 404
    assert c.get(f"/api/screen/photo/{PHOTO}",
                 headers={"X-Forwarded-For": "192.168.1.50"}).status_code == 404


def test_nginx_keeps_screen_local():
    conf = (ROOT / "packages/dashboard/etc/nginx/sites-available/dahlia").read_text()
    network, _, local = conf.partition("listen 127.0.0.1:8090;")
    assert "location ^~ /api/screen {\n        return 404;" in network
    assert "location ^~ /api/screen/photo/ {" in local
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
