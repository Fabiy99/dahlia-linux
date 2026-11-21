"""Start screen tiles with content from apps: photos (Immich), new in the
media library (Jellyfin) and running downloads (qBittorrent, Transmission).

The agent asks the app on its local port (127.0.0.1:<host_port> from the
app's state.json), so nothing leaves the server. Immich and Jellyfin need an
API key that the user makes in the app; qBittorrent needs its web UI login.
Transmission is installed by Dahlia with a generated login, so it needs
nothing. Keys and passwords are kept in /etc/dahlia/tiles.json (root, 0600)
and never sent back to the dashboard.

Pictures go through the agent too (tiles.image): the dashboard serves them
from its own origin, the browser never talks to the app.

The photo frame on the server's monitor (tiles.frame) takes its pictures
from Immich the same way: an album, or random photos of the library.
"""

import base64
import datetime
import http.cookiejar
import json
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import apps
from .actions import Context, Param, action
from .protocol import FAILED, INVALID_PARAMS, AgentError
from .settings import read_json, write_file

TIMEOUT = 8
MAX_JSON = 2 * 1024 * 1024
# Immich previews (about 1440 px) for the photo frame are larger than tile thumbnails.
MAX_IMAGE = 3 * 1024 * 1024
# Tile -> apps that can fill it, in order of preference.
TILES = {"photos": ("immich",), "media": ("jellyfin",),
         "downloads": ("qbittorrent", "transmission")}
# App -> settings it needs; Transmission takes its login from the install.
FIELDS = {"immich": ("key",), "jellyfin": ("key",), "qbittorrent": ("user", "password")}
SECRET_FIELDS = {"key", "password"}
VALUE_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
IMMICH_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
JELLYFIN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ITEM_RE = re.compile(r"^[0-9a-f-]{32,36}$")
MAX_ITEMS = 6
FRAME_PHOTOS = 30
MAX_ALBUMS = 200


class TileError(Exception):
    pass


def config_file(ctx: Context) -> Path:
    return ctx.etc / "dahlia/tiles.json"


def load_config(ctx: Context) -> dict:
    data = read_json(config_file(ctx), {})
    return {k: v for k, v in data.items() if k in FIELDS and isinstance(v, dict)}


def app_paths(ctx: Context) -> apps.Paths:
    return apps.Paths(apps=ctx.var / "apps", pool=ctx.pool, system=ctx.system)


def app_port(ctx: Context, app_id: str) -> int | None:
    """Local web port of an installed, enabled app."""
    state = apps.read_json(app_paths(ctx).app_dir(app_id) / "state.json") or {}
    port = state.get("host_port")
    if not state or state.get("enabled") is False or type(port) is not int:
        return None
    return port


# --- HTTP to the app -----------------------------------------------------------


class Client:
    """Requests to http://127.0.0.1:<port>; tests replace open()."""

    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def open(self, method: str, path: str, headers: dict, body: bytes | None,
             limit: int) -> tuple[int, dict, bytes]:
        # self.base is always http://127.0.0.1:<port>.
        req = urllib.request.Request(self.base + path, data=body, headers=headers,  # noqa: S310
                                     method=method)
        try:
            with self.opener.open(req, timeout=TIMEOUT) as res:  # only 127.0.0.1
                return res.status, dict(res.headers), res.read(limit + 1)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read(4096)
        except (OSError, ValueError) as e:
            raise TileError(f"app not reachable: {e}") from None

    def call(self, method: str, path: str, headers: dict | None = None, body: bytes | None = None,
             limit: int = MAX_JSON) -> tuple[int, dict, bytes]:
        status, head, data = self.open(method, path, headers or {}, body, limit)
        if len(data) > limit:
            raise TileError("answer too large")
        return status, head, data

    def json(self, method: str, path: str, headers: dict | None = None, body=None):
        raw = json.dumps(body).encode() if body is not None else None
        head = {"Accept": "application/json", **(headers or {})}
        if raw is not None:
            head["Content-Type"] = "application/json"
        status, _, data = self.call(method, path, head, raw)
        if status in (401, 403):
            raise TileError("login refused, check the key")
        if status != 200:
            raise TileError(f"answer {status}")
        try:
            return json.loads(data)
        except ValueError:
            raise TileError("answer is not JSON") from None


def client(ctx: Context, app_id: str) -> Client:
    port = app_port(ctx, app_id)
    if port is None:
        raise TileError(f"{app_id} is not installed or stopped")
    factory = getattr(ctx, "tile_client", None) or Client
    return factory(port)


# --- Immich ----------------------------------------------------------------------


def immich_photo(ctx: Context, c: dict, today: datetime.date) -> dict:
    """A memory of this day in earlier years, else a random photo."""
    h = {"x-api-key": c["key"]}
    cl = client(ctx, "immich")
    memories = cl.json("GET", f"/api/memories?for={today.isoformat()}", h)
    for m in memories if isinstance(memories, list) else []:
        assets = [a for a in m.get("assets", []) if a.get("type") == "IMAGE"]
        year = (m.get("data") or {}).get("year")
        if assets and isinstance(year, int):
            a = assets[0]
            return {"id": a["id"], "years": today.year - year, "taken": a.get("fileCreatedAt"),
                    "count": len(assets)}
    found = cl.json("POST", "/api/search/random", h, {"size": 1, "type": "IMAGE"})
    if isinstance(found, list) and found:
        a = found[0]
        return {"id": a["id"], "years": None, "taken": a.get("fileCreatedAt"), "count": 1}
    return {"id": None}


def immich_total(ctx: Context, c: dict) -> int | None:
    stats = client(ctx, "immich").json("GET", "/api/assets/statistics", {"x-api-key": c["key"]})
    total = stats.get("total") if isinstance(stats, dict) else None
    return total if isinstance(total, int) else None


def immich_frame(ctx: Context, c: dict, album: str) -> list[str]:
    """Photo ids for the frame: from [album], else random ones of the library."""
    h = {"x-api-key": c["key"]}
    cl = client(ctx, "immich")
    if album:
        data = cl.json("GET", f"/api/albums/{album}", h)
        assets = data.get("assets", []) if isinstance(data, dict) else []
        ids = [a["id"] for a in assets if isinstance(a, dict) and a.get("type") == "IMAGE"
               and IMMICH_ID_RE.fullmatch(str(a.get("id", "")))]
        return random.sample(ids, min(len(ids), FRAME_PHOTOS))
    found = cl.json("POST", "/api/search/random", h, {"size": FRAME_PHOTOS, "type": "IMAGE"})
    return [a["id"] for a in found if isinstance(a, dict)
            and IMMICH_ID_RE.fullmatch(str(a.get("id", "")))] if isinstance(found, list) else []


def immich_albums(ctx: Context, c: dict) -> list[dict]:
    data = client(ctx, "immich").json("GET", "/api/albums", {"x-api-key": c["key"]})
    out = [{"id": a["id"], "name": str(a.get("albumName") or "")[:120],
            "count": a.get("assetCount") if isinstance(a.get("assetCount"), int) else 0}
           for a in data if isinstance(a, dict) and IMMICH_ID_RE.fullmatch(str(a.get("id", "")))] \
        if isinstance(data, list) else []
    return sorted(out, key=lambda a: a["name"].lower())[:MAX_ALBUMS]


def immich_ready(ctx: Context) -> dict | None:
    """Immich settings when it is installed and has its key, else None."""
    cfg = load_config(ctx).get("immich")
    return cfg if cfg is not None and "immich" in installed(ctx) else None


# --- Jellyfin --------------------------------------------------------------------


def jellyfin_latest(ctx: Context, c: dict) -> dict:
    h = {"Authorization": f'MediaBrowser Token="{c["key"]}"'}
    cl = client(ctx, "jellyfin")
    users = cl.json("GET", "/Users", h)
    admin = next((u for u in users if (u.get("Policy") or {}).get("IsAdministrator")),
                 users[0] if users else None) if isinstance(users, list) else None
    if admin is None:
        return {"items": []}
    latest = cl.json("GET", f"/Users/{admin['Id']}/Items/Latest?Limit={MAX_ITEMS}"
                     "&Fields=ProductionYear&EnableImageTypes=Primary", h)
    items = []
    for i in latest if isinstance(latest, list) else []:
        if not JELLYFIN_ID_RE.fullmatch(str(i.get("Id", ""))):
            continue
        name = i.get("SeriesName") or i.get("Name") or ""
        items.append({"id": i["Id"], "name": str(name)[:120], "type": i.get("Type"),
                      "year": i.get("ProductionYear"),
                      "image": bool((i.get("ImageTags") or {}).get("Primary"))})
    return {"items": items}


# --- downloads --------------------------------------------------------------------


def qbittorrent(ctx: Context, c: dict) -> dict:
    cl = client(ctx, "qbittorrent")
    body = urllib.parse.urlencode({"username": c["user"], "password": c["password"]}).encode()
    status, _, data = cl.call("POST", "/api/v2/auth/login",
                              {"Referer": cl.base, "Content-Type":
                               "application/x-www-form-urlencoded"}, body)
    if status != 200 or data.strip() != b"Ok.":
        raise TileError("login refused, check user and password")
    transfer = cl.json("GET", "/api/v2/transfer/info", {"Referer": cl.base})
    torrents = cl.json("GET", "/api/v2/torrents/info?filter=active&sort=dlspeed&reverse=true",
                       {"Referer": cl.base})
    items = [{"name": str(t.get("name", ""))[:160], "progress": float(t.get("progress", 0)),
              "down": int(t.get("dlspeed", 0)), "up": int(t.get("upspeed", 0)),
              "eta": int(t.get("eta", 0)) if 0 < int(t.get("eta", 0)) < 8640000 else None}
             for t in torrents if isinstance(t, dict)] if isinstance(torrents, list) else []
    return {"app": "qbittorrent", "down": int(transfer.get("dl_info_speed", 0)),
            "up": int(transfer.get("up_info_speed", 0)), "items": items[:MAX_ITEMS],
            "active": len(items)}


def transmission(ctx: Context) -> dict:
    paths = app_paths(ctx)
    t, _ = apps.load_installed(paths, "transmission")
    sec = apps.read_json(paths.app_dir("transmission") / "secrets.json") or {}
    login = {k: apps.PLACEHOLDER_RE.sub(lambda m: sec.get(m.group(1), ""), v)
             for k, v in (t.login or {}).items()}
    auth = base64.b64encode(f"{login.get('user', '')}:{login.get('password', '')}".encode())
    head = {"Authorization": f"Basic {auth.decode()}", "Content-Type": "application/json"}
    cl = client(ctx, "transmission")

    def rpc(method: str, arguments: dict) -> dict:
        body = json.dumps({"method": method, "arguments": arguments}).encode()
        status, h, data = cl.call("POST", "/transmission/rpc", head, body)
        if status == 409:
            head["X-Transmission-Session-Id"] = h.get("X-Transmission-Session-Id", "")
            status, h, data = cl.call("POST", "/transmission/rpc", head, body)
        if status != 200:
            raise TileError(f"answer {status}")
        try:
            return json.loads(data).get("arguments", {})
        except (ValueError, AttributeError):
            raise TileError("answer is not JSON") from None

    stats = rpc("session-stats", {})
    got = rpc("torrent-get", {"fields": ["name", "percentDone", "rateDownload", "rateUpload",
                                         "eta", "status"]}).get("torrents", [])
    # Status 4 downloading, 6 seeding.
    active = [x for x in got if isinstance(x, dict) and x.get("status") in (4, 6)
              and (x.get("rateDownload") or x.get("rateUpload") or x.get("status") == 4)]
    active.sort(key=lambda x: -int(x.get("rateDownload", 0)))
    items = [{"name": str(x.get("name", ""))[:160], "progress": float(x.get("percentDone", 0)),
              "down": int(x.get("rateDownload", 0)), "up": int(x.get("rateUpload", 0)),
              "eta": int(x["eta"]) if isinstance(x.get("eta"), int) and x["eta"] > 0 else None}
             for x in active]
    return {"app": "transmission", "down": int(stats.get("downloadSpeed", 0)),
            "up": int(stats.get("uploadSpeed", 0)), "items": items[:MAX_ITEMS],
            "active": len(items)}


# --- tiles -------------------------------------------------------------------------


def installed(ctx: Context) -> set[str]:
    return set(apps.installed(app_paths(ctx)))


def source(ctx: Context, tile: str, cfg: dict) -> str | None:
    """The app that fills a tile: installed and, where needed, set up."""
    have = installed(ctx)
    for app_id in TILES[tile]:
        if app_id in have and (app_id not in FIELDS or app_id in cfg):
            return app_id
    return None


def tile_data(ctx: Context, tile: str, today: datetime.date | None = None) -> dict:
    cfg = load_config(ctx)
    app_id = source(ctx, tile, cfg)
    if app_id is None:
        have = installed(ctx)
        candidates = [a for a in TILES[tile] if a in have]
        return {"tile": tile, "state": "setup" if candidates else "missing", "apps": candidates}
    today = today or datetime.date.fromtimestamp(ctx.clock())
    try:
        if app_id == "immich":
            data = immich_photo(ctx, cfg["immich"], today)
        elif app_id == "jellyfin":
            data = jellyfin_latest(ctx, cfg["jellyfin"])
        elif app_id == "qbittorrent":
            data = qbittorrent(ctx, cfg["qbittorrent"])
        else:
            data = transmission(ctx)
    except (TileError, KeyError, TypeError, ValueError, AttributeError) as e:
        return {"tile": tile, "state": "error", "app": app_id, "error": str(e)[:300]}
    return {"tile": tile, "state": "ok", "app": app_id, **data}


def image(ctx: Context, app_id: str, item: str) -> tuple[str, bytes]:
    cfg = load_config(ctx).get(app_id)
    if cfg is None:
        raise TileError(f"{app_id} is not set up")
    if app_id == "immich" and IMMICH_ID_RE.fullmatch(item):
        path, head = f"/api/assets/{item}/thumbnail?size=preview", {"x-api-key": cfg["key"]}
    elif app_id == "jellyfin" and JELLYFIN_ID_RE.fullmatch(item):
        path = f"/Items/{item}/Images/Primary?maxHeight=360&quality=85"
        head = {"Authorization": f'MediaBrowser Token="{cfg["key"]}"'}
    else:
        raise TileError("no such picture")
    status, h, data = client(ctx, app_id).call("GET", path, head, limit=MAX_IMAGE)
    kind = next((v for k, v in h.items() if k.lower() == "content-type"), "")
    if status != 200 or kind.split(";")[0] not in ("image/jpeg", "image/webp", "image/png"):
        raise TileError(f"no picture (answer {status})")
    return kind.split(";")[0], data


def photo_total(ctx: Context) -> int | None:
    """Photos and videos in Immich, for the weekly letter; None without Immich."""
    cfg = load_config(ctx).get("immich")
    if cfg is None or "immich" not in installed(ctx):
        return None
    try:
        return immich_total(ctx, cfg)
    except (TileError, KeyError, TypeError, ValueError, AttributeError):
        return None


# --- actions ----------------------------------------------------------------------


@action("tiles.get", mutating=False)
def tiles_get(ctx: Context):
    """Which apps can fill the tiles and which are set up (no keys)."""
    cfg = load_config(ctx)
    have = installed(ctx)
    return {"tiles": {tile: [a for a in ids if a in have] for tile, ids in TILES.items()},
            "setup": {a: {k: (bool(v.get(k)) if k in SECRET_FIELDS else v.get(k, ""))
                          for k in FIELDS[a]} for a, v in cfg.items()},
            "fields": {a: list(f) for a, f in FIELDS.items()}}


@action("tiles.set", mutating=True,
        params={"app": Param(str, choices=tuple(FIELDS)), "settings": Param(dict)})
def tiles_set(ctx: Context, app: str, settings: dict):
    """Save the key or login of an app for its tile; an empty settings object removes it."""
    cfg = load_config(ctx)
    if not settings:
        cfg.pop(app, None)
    else:
        unknown = set(settings) - set(FIELDS[app])
        if unknown:
            raise AgentError(INVALID_PARAMS, f"unknown settings: {', '.join(sorted(unknown))}")
        old = cfg.get(app, {})
        new = {}
        for k in FIELDS[app]:
            v = settings.get(k, "")
            if not isinstance(v, str):
                raise AgentError(INVALID_PARAMS, f"'{k}' must be a string")
            if not v and k in SECRET_FIELDS and old.get(k):
                v = old[k]
            if not VALUE_RE.fullmatch(v):
                raise AgentError(INVALID_PARAMS, f"'{k}' is missing or has an invalid format")
            new[k] = v
        cfg[app] = new
    write_file(config_file(ctx), json.dumps(cfg, indent=2) + "\n", 0o600)
    return tiles_get(ctx)


@action("tiles.data", mutating=False, params={"tile": Param(str, choices=tuple(TILES))})
def tiles_data(ctx: Context, tile: str):
    """Content of a tile, or what it needs (setup, missing app, error)."""
    return tile_data(ctx, tile)


@action("tiles.image", mutating=False,
        params={"app": Param(str, choices=("immich", "jellyfin")),
                "item": Param(str, pattern=ITEM_RE)})
def tiles_image(ctx: Context, app: str, item: str):
    """A picture of a tile, base64 encoded."""
    try:
        kind, data = image(ctx, app, item)
    except TileError as e:
        raise AgentError(FAILED, str(e)) from None
    return {"type": kind, "data": base64.b64encode(data).decode()}


@action("tiles.frame", mutating=False,
        params={"album": Param(str, required=False, default="",
                               pattern=re.compile(rf"^(|{IMMICH_ID_RE.pattern[1:-1]})$"))})
def tiles_frame(ctx: Context, album: str):
    """Photo ids for the photo frame on the server's monitor."""
    cfg = immich_ready(ctx)
    if cfg is None:
        return {"state": "setup" if "immich" in installed(ctx) else "missing", "photos": []}
    try:
        return {"state": "ok", "photos": immich_frame(ctx, cfg, album)}
    except (TileError, KeyError, TypeError, ValueError, AttributeError) as e:
        return {"state": "error", "error": str(e)[:300], "photos": []}


@action("tiles.albums", mutating=False)
def tiles_albums(ctx: Context):
    """Immich albums to choose for the photo frame."""
    cfg = immich_ready(ctx)
    if cfg is None:
        return {"state": "setup" if "immich" in installed(ctx) else "missing", "albums": []}
    try:
        return {"state": "ok", "albums": immich_albums(ctx, cfg)}
    except (TileError, KeyError, TypeError, ValueError, AttributeError) as e:
        return {"state": "error", "error": str(e)[:300], "albums": []}
