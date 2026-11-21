"""tiles: content of the start screen tiles from Immich, Jellyfin and download apps."""

import base64
import datetime
import json

import pytest
from dahlia_agent import actions, tiles
from test_agent import FakeRunner, run

TRANSMISSION = """\
schema: 2
id: transmission
name: Transmission
category: downloads
description: {de: T, en: T}
web: {port: 9091, public_port: 9091}
login: {user: "admin", password: "{SECRET_ADMIN}"}
services:
  app:
    image: ghcr.io/linuxserver/transmission:version-4.1.4-r0
    environment: {PASS: "{SECRET_ADMIN}"}
"""

PLAIN = """\
schema: 2
id: {id}
name: {id}
category: media
description: {{de: T, en: T}}
web: {{port: 80, public_port: 8100}}
services:
  app:
    image: example/{id}:1.0
"""

UUID = "0b2e3c4d-1111-2222-3333-444455556666"
JID = "a" * 32


class FakeClient:
    """Answers by (method, path prefix); records the requests."""

    routes: dict = {}
    seen: list = []

    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.port = port

    def call(self, method, path, headers=None, body=None, limit=tiles.MAX_JSON):
        FakeClient.seen.append((self.port, method, path, headers or {}, body))
        for (m, prefix), answer in FakeClient.routes.items():
            if m == method and path.startswith(prefix):
                return answer(headers or {}, body) if callable(answer) else answer
        return 404, {}, b"{}"

    def json(self, method, path, headers=None, body=None):
        return tiles.Client.json(self, method, path, headers, body)


def ok(data, head=None):
    return 200, head or {"Content-Type": "application/json"}, json.dumps(data).encode()


@pytest.fixture
def ctx(tmp_path):
    c = actions.Context(runner=FakeRunner(), etc=tmp_path / "etc", var=tmp_path / "var",
                        pool=tmp_path / "user", system=tmp_path / "srv",
                        clock=lambda: datetime.datetime(2026, 11, 19, 9).timestamp())
    c.tile_client = FakeClient
    FakeClient.routes = {}
    FakeClient.seen = []
    return c


def install(ctx, app_id, port, text=None, secrets=None, enabled=True):
    d = ctx.var / "apps" / app_id
    d.mkdir(parents=True)
    (d / "app.yaml").write_text(text or PLAIN.format(id=app_id))
    (d / "state.json").write_text(json.dumps({"host_port": port, "enabled": enabled}))
    (d / "secrets.json").write_text(json.dumps(secrets or {}))


def test_tiles_need_app_and_setup(ctx):
    assert tiles.tile_data(ctx, "photos")["state"] == "missing"
    install(ctx, "immich", 41001)
    assert tiles.tile_data(ctx, "photos") == {"tile": "photos", "state": "setup",
                                              "apps": ["immich"]}
    got = run(ctx, "tiles.set", {"app": "immich", "settings": {"key": "k1"}})["result"]
    assert got["setup"] == {"immich": {"key": True}} and got["tiles"]["photos"] == ["immich"]
    assert oct(tiles.config_file(ctx).stat().st_mode & 0o777) == "0o600"
    # An empty key keeps the saved one; an empty object removes it.
    run(ctx, "tiles.set", {"app": "immich", "settings": {"key": ""}})
    assert tiles.load_config(ctx)["immich"]["key"] == "k1"
    assert not run(ctx, "tiles.set", {"app": "immich", "settings": {"token": "x"}})["ok"]
    run(ctx, "tiles.set", {"app": "immich", "settings": {}})
    assert tiles.load_config(ctx) == {}


def test_immich_memory_then_random(ctx):
    install(ctx, "immich", 41001)
    run(ctx, "tiles.set", {"app": "immich", "settings": {"key": "k1"}})
    FakeClient.routes = {("GET", "/api/memories"): ok([
        {"data": {"year": 2023}, "assets": [{"id": UUID, "type": "IMAGE",
                                             "fileCreatedAt": "2023-11-19T10:00:00Z"}]}])}
    got = tiles.tile_data(ctx, "photos")
    assert got["state"] == "ok" and got["id"] == UUID and got["years"] == 3
    assert FakeClient.seen[0][:3] == (41001, "GET", "/api/memories?for=2026-11-19")
    assert FakeClient.seen[0][3]["x-api-key"] == "k1"
    FakeClient.routes = {("GET", "/api/memories"): ok([]),
                         ("POST", "/api/search/random"): ok([{"id": UUID, "type": "IMAGE"}])}
    assert tiles.tile_data(ctx, "photos")["years"] is None
    FakeClient.routes = {("GET", "/api/memories"): (401, {}, b"{}")}
    got = tiles.tile_data(ctx, "photos")
    assert got["state"] == "error" and "key" in got["error"]


def test_image_only_known_ids_and_types(ctx):
    install(ctx, "immich", 41001)
    run(ctx, "tiles.set", {"app": "immich", "settings": {"key": "k1"}})
    FakeClient.routes = {("GET", f"/api/assets/{UUID}/thumbnail"):
                         (200, {"Content-Type": "image/jpeg"}, b"\xff\xd8jpeg")}
    got = run(ctx, "tiles.image", {"app": "immich", "item": UUID})["result"]
    assert got == {"type": "image/jpeg", "data": base64.b64encode(b"\xff\xd8jpeg").decode()}
    assert not run(ctx, "tiles.image", {"app": "immich", "item": JID})["ok"]
    FakeClient.routes = {("GET", "/api/assets/"): (200, {"Content-Type": "text/html"}, b"<x>")}
    assert not run(ctx, "tiles.image", {"app": "immich", "item": UUID})["ok"]


def test_jellyfin_latest(ctx):
    install(ctx, "jellyfin", 41002)
    run(ctx, "tiles.set", {"app": "jellyfin", "settings": {"key": "jk"}})
    FakeClient.routes = {
        ("GET", "/Users/u1/Items/Latest"): ok([
            {"Id": JID, "Name": "Ep 1", "SeriesName": "Show", "Type": "Episode",
             "ImageTags": {"Primary": "x"}},
            {"Id": "../etc", "Name": "bad"}]),
        ("GET", "/Users"): ok([{"Id": "u0", "Policy": {}}, {"Id": "u1",
                                                         "Policy": {"IsAdministrator": True}}]),
    }
    got = tiles.tile_data(ctx, "media")
    assert got["items"] == [{"id": JID, "name": "Show", "type": "Episode", "year": None,
                             "image": True}]
    assert 'MediaBrowser Token="jk"' in FakeClient.seen[0][3]["Authorization"]


def test_qbittorrent_downloads(ctx):
    install(ctx, "qbittorrent", 41003)
    run(ctx, "tiles.set", {"app": "qbittorrent",
                           "settings": {"user": "admin", "password": "pw"}})
    FakeClient.routes = {
        ("POST", "/api/v2/auth/login"): (200, {}, b"Ok."),
        ("GET", "/api/v2/transfer/info"): ok({"dl_info_speed": 1000, "up_info_speed": 10}),
        ("GET", "/api/v2/torrents/info"): ok([{"name": "debian.iso", "progress": 0.5,
                                               "dlspeed": 1000, "upspeed": 10, "eta": 60}]),
    }
    got = tiles.tile_data(ctx, "downloads")
    assert got["app"] == "qbittorrent" and got["down"] == 1000
    assert got["items"] == [{"name": "debian.iso", "progress": 0.5, "down": 1000, "up": 10,
                             "eta": 60}]
    assert b"password=pw" in FakeClient.seen[0][4]
    setup = run(ctx, "tiles.get")["result"]["setup"]["qbittorrent"]
    assert setup == {"user": "admin", "password": True}


def test_transmission_uses_install_login(ctx):
    install(ctx, "transmission", 41004, TRANSMISSION, {"SECRET_ADMIN": "s3cret"})
    calls = []

    def rpc(headers, body):
        calls.append(dict(headers))
        if "X-Transmission-Session-Id" not in headers:
            return 409, {"X-Transmission-Session-Id": "sid"}, b""
        method = json.loads(body)["method"]
        if method == "session-stats":
            return ok({"arguments": {"downloadSpeed": 5, "uploadSpeed": 1}})
        return ok({"arguments": {"torrents": [
            {"name": "a", "percentDone": 0.2, "rateDownload": 5, "rateUpload": 0, "eta": 99,
             "status": 4},
            {"name": "done", "percentDone": 1, "rateDownload": 0, "rateUpload": 0, "status": 0}]}})

    FakeClient.routes = {("POST", "/transmission/rpc"): rpc}
    got = tiles.tile_data(ctx, "downloads")
    assert got["state"] == "ok" and got["app"] == "transmission" and got["active"] == 1
    assert calls[0]["Authorization"] == "Basic " + base64.b64encode(b"admin:s3cret").decode()


def test_stopped_app_and_photo_total(ctx):
    install(ctx, "immich", 41001, enabled=False)
    run(ctx, "tiles.set", {"app": "immich", "settings": {"key": "k1"}})
    assert tiles.tile_data(ctx, "photos")["state"] == "error"
    assert tiles.photo_total(ctx) is None
    (ctx.var / "apps/immich/state.json").write_text(json.dumps({"host_port": 41001}))
    FakeClient.routes = {("GET", "/api/assets/statistics"): ok({"images": 9, "videos": 1,
                                                                "total": 10})}
    assert tiles.photo_total(ctx) == 10


UUID2 = "0b2e3c4d-1111-2222-3333-444455557777"


def test_frame_photos_from_album_or_random(ctx):
    assert run(ctx, "tiles.frame", {})["result"] == {"state": "missing", "photos": []}
    install(ctx, "immich", 41001)
    assert run(ctx, "tiles.frame", {})["result"]["state"] == "setup"
    run(ctx, "tiles.set", {"app": "immich", "settings": {"key": "k1"}})
    FakeClient.routes = {("POST", "/api/search/random"):
                         ok([{"id": UUID, "type": "IMAGE"}, {"id": "../etc"}])}
    assert run(ctx, "tiles.frame", {})["result"] == {"state": "ok", "photos": [UUID]}
    assert json.loads(FakeClient.seen[-1][4])["size"] == tiles.FRAME_PHOTOS
    FakeClient.routes = {("GET", f"/api/albums/{UUID2}"): ok({"assets": [
        {"id": UUID, "type": "IMAGE"}, {"id": UUID2, "type": "VIDEO"}]})}
    assert run(ctx, "tiles.frame", {"album": UUID2})["result"]["photos"] == [UUID]
    assert not run(ctx, "tiles.frame", {"album": "../x"})["ok"]
    FakeClient.routes = {("GET", "/api/albums"): (500, {}, b"{}")}
    assert run(ctx, "tiles.frame", {"album": UUID2})["result"]["state"] == "error"


def test_albums_for_the_frame(ctx):
    install(ctx, "immich", 41001)
    run(ctx, "tiles.set", {"app": "immich", "settings": {"key": "k1"}})
    FakeClient.routes = {("GET", "/api/albums"): ok([
        {"id": UUID2, "albumName": "Urlaub", "assetCount": 12},
        {"id": UUID, "albumName": "Dahlia", "assetCount": 40},
        {"id": "bad", "albumName": "x"}])}
    got = run(ctx, "tiles.albums", {})["result"]
    assert got == {"state": "ok", "albums": [{"id": UUID, "name": "Dahlia", "count": 40},
                                             {"id": UUID2, "name": "Urlaub", "count": 12}]}

