"""Start screen: widget selection, weather places and weather data.

The selection is stored per server in home.json in the state directory, so
every client (browser, desktop app, local screen) shows the same start screen.
Weather comes from Open-Meteo (no API key). The server fetches it, the
browser never talks to a third party.
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

WIDGETS = ("weather", "apps", "storage", "system", "jobs", "network", "disks", "health",
           "cpu", "memory", "history")
# Start screen of the design: weather, apps, storage, system, recently done.
WIDGETS_OFF = ("network", "disks", "health", "cpu", "memory", "history")
DEFAULT_WIDGETS = {w: w not in WIDGETS_OFF for w in WIDGETS}
# Files from before the layout of version 2 keep their places, widgets start from the defaults.
LAYOUT = 2
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


class HomeStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def get(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        widgets = dict(DEFAULT_WIDGETS)
        stored = data.get("widgets") if data.get("layout") == LAYOUT else None
        for k, v in (stored or {}).items():
            if k in widgets and isinstance(v, bool):
                widgets[k] = v
        places = []
        for p in data.get("places") or []:
            try:
                places.append(clean_place(p))
            except (ValueError, AttributeError):
                continue
        return {"widgets": widgets, "places": places[:MAX_PLACES]}

    def set(self, widgets: dict[str, bool], places: list[dict]) -> dict:
        unknown = set(widgets) - set(WIDGETS)
        if unknown:
            raise ValueError("widgets")
        if len(places) > MAX_PLACES:
            raise ValueError("places")
        data = {"layout": LAYOUT, "widgets": {**DEFAULT_WIDGETS, **widgets},
                "places": [clean_place(p) for p in places]}
        with self.lock:
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            tmp.replace(self.path)
        return {"widgets": data["widgets"], "places": data["places"]}


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
