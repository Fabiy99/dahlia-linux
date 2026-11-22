"""Start screen: widget selection, weather places and weather data.

The selection is stored per server in home.json in the state directory, so
every client (browser, desktop app, local screen) shows the same start screen.
Weather comes from Open-Meteo (no API key). The server fetches it, the
browser never talks to a third party.

frame.json holds the photo frame of the monitor attached to the server:
whether it runs, seconds per photo, the Immich album ("" = random photos)
and whether the clock stays on top.
"""

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

WIDGETS = ("weather", "apps", "storage", "system", "jobs", "letter", "photos", "media",
           "downloads", "network", "disks", "health", "cpu", "memory", "history")
# Start screen of the design: weather, apps, storage, system, recently done.
WIDGETS_OFF = ("photos", "media", "downloads", "network", "disks", "health", "cpu", "memory",
               "history")
DEFAULT_WIDGETS = {w: w not in WIDGETS_OFF for w in WIDGETS}
# Tile widths: s small, m medium, l large, xl full row.
SIZES = ("s", "m", "l", "xl")
DEFAULT_SIZES = {"weather": "m", "apps": "l", "storage": "s", "system": "s", "jobs": "s",
                 "network": "s", "disks": "s", "health": "l", "cpu": "s", "memory": "s",
                 "history": "l", "letter": "m", "photos": "m", "media": "l", "downloads": "m"}
# Appearance: colour scheme, accent colour, backdrop, weather effects.
LOOK_CHOICES = {
    "theme": ("auto", "light", "dark"),
    "accent": ("dahlia", "violet", "blue", "teal", "amber"),
    "background": ("bloom", "still", "off"),
}
DEFAULT_LOOK = {"theme": "auto", "accent": "dahlia", "background": "bloom", "weather_fx": True}
# Layout 2 stored only on/off per widget; layout 3 stores order, size and look.
# Files from before layout 2 keep their places, widgets start from the defaults.
# Widgets that a later version adds are appended switched off, so an update
# never changes a start screen the user arranged.
LAYOUT = 3
MAX_PLACES = 6
NAME_MAX = 80

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "Dahlia Linux"
TIMEOUT = 8
CACHE_SECONDS = 15 * 60
RETRY_SECONDS = 2 * 60

# Fetch: url -> parsed JSON. Tests replace it.
Fetch = Callable[[str], dict]


class WeatherError(Exception):
    pass


def http_fetch(url: str) -> dict:
    if not url.startswith("https://"):
        raise WeatherError("https only")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read(1024 * 1024))
    except (OSError, ValueError) as e:
        raise WeatherError(str(e)) from None


def clean_place(p: dict) -> dict:
    """Validate one place; raises ValueError."""
    name = str(p.get("name", "")).strip()
    if not name or len(name) > NAME_MAX:
        raise ValueError("name")
    try:
        lat = float(p["latitude"])
        lon = float(p["longitude"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("coordinates") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinates")
    out = {"name": name, "latitude": round(lat, 4), "longitude": round(lon, 4)}
    country = str(p.get("country", "")).strip()[:NAME_MAX]
    if country:
        out["country"] = country
    return out


def default_tiles() -> list[dict]:
    return [{"id": w, "size": DEFAULT_SIZES[w], "on": DEFAULT_WIDGETS[w]} for w in WIDGETS]


def clean_tiles(tiles: list[dict], strict: bool) -> list[dict]:
    """Order, size and visibility of the tiles.

    strict raises ValueError on unknown widgets, sizes and duplicates (requests);
    otherwise broken entries are dropped (stored file). Widgets missing from the
    list are appended switched off.
    """
    out, seen = [], set()
    for t in tiles:
        if not isinstance(t, dict):
            if strict:
                raise ValueError("tiles")
            continue
        w, size, on = t.get("id"), t.get("size"), t.get("on")
        if w not in WIDGETS or w in seen:
            if strict:
                raise ValueError("tiles")
            continue
        if size not in SIZES:
            if strict:
                raise ValueError("size")
            size = DEFAULT_SIZES[w]
        if not isinstance(on, bool):
            if strict:
                raise ValueError("tiles")
            on = False
        seen.add(w)
        out.append({"id": w, "size": size, "on": on})
    out += [{"id": w, "size": DEFAULT_SIZES[w], "on": False} for w in WIDGETS if w not in seen]
    return out


def clean_look(look: dict, strict: bool) -> dict:
    out = dict(DEFAULT_LOOK)
    for k, v in look.items():
        if k in LOOK_CHOICES:
            ok = isinstance(v, str) and v in LOOK_CHOICES[k]
        else:
            ok = k == "weather_fx" and isinstance(v, bool)
        if ok:
            out[k] = v
        elif strict:
            raise ValueError("look")
    return out


FRAME_DEFAULT = {"enabled": False, "interval": 30, "album": "", "clock": True}
FRAME_INTERVAL = (5, 3600)
ALBUM_RE = re.compile(r"^(|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")


def clean_frame(frame: dict, strict: bool) -> dict:
    out = dict(FRAME_DEFAULT)
    for key, default in FRAME_DEFAULT.items():
        if key not in frame:
            continue
        v = frame[key]
        ok = type(v) is type(default)
        if ok and key == "interval":
            ok = FRAME_INTERVAL[0] <= v <= FRAME_INTERVAL[1]
        if ok and key == "album":
            ok = bool(ALBUM_RE.fullmatch(v))
        if ok:
            out[key] = v
        elif strict:
            raise ValueError(key)
    return out


class FrameStore:
    """Photo frame of the local screen (frame.json)."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def get(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return clean_frame(data if isinstance(data, dict) else {}, strict=False)

    def set(self, change: dict) -> dict:
        with self.lock:
            new = clean_frame({**self.get(), **change}, strict=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(new, f, indent=1)
            tmp.replace(self.path)
        return new


class HomeStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    @staticmethod
    def view(tiles: list[dict], look: dict, places: list[dict]) -> dict:
        return {"tiles": tiles, "look": look, "places": places,
                "widgets": {t["id"]: t["on"] for t in tiles}}

    def get(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        layout = data.get("layout")
        if layout == LAYOUT and isinstance(data.get("tiles"), list):
            tiles = clean_tiles(data["tiles"], strict=False)
        else:
            tiles = default_tiles()
            stored = data.get("widgets") if layout == 2 else None
            if isinstance(stored, dict):
                for t in tiles:
                    if isinstance(stored.get(t["id"]), bool):
                        t["on"] = stored[t["id"]]
        look = data.get("look")
        look = clean_look(look, strict=False) if isinstance(look, dict) else dict(DEFAULT_LOOK)
        places = []
        for p in data.get("places") or []:
            try:
                places.append(clean_place(p))
            except (ValueError, AttributeError):
                continue
        return self.view(tiles, look, places[:MAX_PLACES])

    def set(self, tiles: list[dict] | None = None, places: list[dict] | None = None,
            look: dict | None = None, widgets: dict[str, bool] | None = None) -> dict:
        """Changes the given parts; None keeps the stored value."""
        if places is not None and len(places) > MAX_PLACES:
            raise ValueError("places")
        if widgets is not None and set(widgets) - set(WIDGETS):
            raise ValueError("widgets")
        with self.lock:
            cur = self.get()
            new_tiles = clean_tiles(tiles, strict=True) if tiles is not None else cur["tiles"]
            if widgets is not None:
                new_tiles = [{**t, "on": widgets.get(t["id"], t["on"])} for t in new_tiles]
            new_look = clean_look(look, strict=True) if look is not None else cur["look"]
            new_places = [clean_place(p) for p in places] if places is not None else cur["places"]
            data = {"layout": LAYOUT, "tiles": new_tiles, "look": new_look, "places": new_places}
            self._write(data)
        return self.view(data["tiles"], data["look"], data["places"])

    def reset(self) -> dict:
        """Back to the standard start screen; weather places are kept."""
        with self.lock:
            data = {"layout": LAYOUT, "tiles": default_tiles(), "look": dict(DEFAULT_LOOK),
                    "places": self.get()["places"]}
            self._write(data)
        return self.view(data["tiles"], data["look"], data["places"])

    def _write(self, data: dict) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        tmp.replace(self.path)


def search(query: str, lang: str, fetch: Fetch = http_fetch) -> list[dict]:
    """Places matching query, from the Open-Meteo geocoding API."""
    query = query.strip()
    if len(query) < 2 or len(query) > NAME_MAX:
        return []
    params = {"name": query, "count": 8, "language": lang if lang in ("de", "en") else "en",
              "format": "json"}
    data = fetch(f"{GEOCODE_URL}?{urllib.parse.urlencode(params)}")
    out = []
    for r in data.get("results") or []:
        region = ", ".join(x for x in (r.get("admin1"), r.get("country")) if x)
        try:
            out.append(clean_place({"name": r.get("name"), "latitude": r.get("latitude"),
                                    "longitude": r.get("longitude"), "country": region}))
        except ValueError:
            continue
    return out


def kind(code: int | None) -> str:
    """WMO weather code -> one of the kinds the start screen draws."""
    if code is None:
        return "cloudy"
    if code in (0, 1):
        return "clear"
    if code == 2:
        return "partly"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if 95 <= code <= 99:
        return "storm"
    return "cloudy"


def parse_forecast(place: dict, data: dict) -> dict:
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    days = []
    for i, date in enumerate((daily.get("time") or [])[:4]):
        def at(key, i=i):
            values = daily.get(key) or []
            return values[i] if i < len(values) else None
        days.append({"date": date, "max": at("temperature_2m_max"), "min": at("temperature_2m_min"),
                     "kind": kind(at("weather_code"))})
    return {
        **place,
        "temperature": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "kind": kind(cur.get("weather_code")),
        "is_day": bool(cur.get("is_day", 1)),
        "days": days,
    }


class Weather:
    """Forecasts for the configured places, cached per place."""

    def __init__(self, fetch: Fetch = http_fetch, clock: Callable[[], float] = time.time):
        self.fetch = fetch
        self.clock = clock
        self.cache: dict[tuple[float, float], tuple[float, dict]] = {}
        # A failed fetch is not repeated for a while: without internet every
        # page load would otherwise wait TIMEOUT seconds per place.
        self.failed: dict[tuple[float, float], tuple[float, str]] = {}
        self.lock = threading.Lock()

    def one(self, place: dict) -> dict:
        key = (place["latitude"], place["longitude"])
        now = self.clock()
        with self.lock:
            hit = self.cache.get(key)
            failed = self.failed.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return {**hit[1], **place}
        if failed and now - failed[0] < RETRY_SECONDS:
            raise WeatherError(failed[1])
        params = {
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": "auto", "forecast_days": 4,
        }
        try:
            data = parse_forecast(place,
                                  self.fetch(f"{FORECAST_URL}?{urllib.parse.urlencode(params)}"))
        except WeatherError as e:
            with self.lock:
                self.failed[key] = (now, str(e))
            raise
        with self.lock:
            self.cache[key] = (now, data)
            self.failed.pop(key, None)
        return data

    def all(self, places: list[dict]) -> list[dict]:
        out = []
        for p in places:
            try:
                out.append(self.one(p))
            except WeatherError as e:
                out.append({**p, "error": str(e)})
        return out
