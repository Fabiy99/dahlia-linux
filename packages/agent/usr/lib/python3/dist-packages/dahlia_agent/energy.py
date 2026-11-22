"""Power draw of the server and what it costs.

The watts come from the UPS (NUT: ups.realpower, else ups.load in % of
ups.realpower.nominal) or from a smart plug in the LAN (Shelly Gen1/Gen2
or Tasmota, local HTTP API without a cloud). history samples them every
minute as "power"; the weekly letter turns the hour averages into kWh and,
with the price per kWh, into money.

    /etc/dahlia/energy.json   {"source", "plug", "host", "price"}

The price is in cents (or the smallest unit of the currency) per kWh.
"""

import json
import re
import urllib.request
from pathlib import Path

from . import ups
from .actions import Context, Param, action
from .protocol import FAILED, INVALID_PARAMS, AgentError
from .settings import read_json, write_file

SOURCES = ("off", "ups", "plug")
PLUGS = ("shelly", "tasmota")
CURRENCIES = ("EUR", "CHF", "GBP", "USD")
# A host name or IPv4 address in the LAN, optional port.
HOST_RE = re.compile(r"^$|^[A-Za-z0-9]([A-Za-z0-9.-]{0,252}[A-Za-z0-9])?(:[0-9]{1,5})?$")
DEFAULTS = {"source": "off", "plug": "shelly", "host": "", "price": 35, "currency": "EUR"}
TIMEOUT = 5
MAX_WATTS = 100000


def config_file(ctx: Context) -> Path:
    return ctx.etc / "dahlia/energy.json"


def load_config(ctx: Context) -> dict:
    data = read_json(config_file(ctx), {})
    out = {k: data[k] if type(data.get(k)) is type(v) else v for k, v in DEFAULTS.items()}
    if out["source"] not in SOURCES:
        out["source"] = "off"
    if out["plug"] not in PLUGS:
        out["plug"] = "shelly"
    if out["currency"] not in CURRENCIES:
        out["currency"] = "EUR"
    return out


def http_json(url: str) -> object:
    # Only http:// URLs built from HOST_RE below.
    with urllib.request.urlopen(url, timeout=TIMEOUT) as res:  # noqa: S310
        return json.loads(res.read(65536))


def _watts(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 1) if 0 <= value <= MAX_WATTS else None


def plug_watts(kind: str, host: str, fetch=None) -> float | None:
    """Current draw from a Shelly (Gen2 RPC, else Gen1 /meter/0) or a Tasmota plug."""
    fetch = fetch or http_json
    urls = ([f"http://{host}/rpc/Switch.GetStatus?id=0", f"http://{host}/meter/0"]
            if kind == "shelly" else [f"http://{host}/cm?cmnd=Status%2010"])
    for url in urls:
        try:
            data = fetch(url)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if kind == "tasmota":
            energy = (data.get("StatusSNS") or {}).get("ENERGY") or {}
            return _watts(energy.get("Power") if isinstance(energy, dict) else None)
        w = _watts(data.get("apower", data.get("power")))
        if w is not None:
            return w
    return None


def ups_watts(ctx: Context) -> float | None:
    cfg = ups.load_config(ctx)
    if cfg["mode"] == "off":
        return None
    status = ups.read_status(ctx, cfg) or {}
    if status.get("realpower") is not None:
        return _watts(status["realpower"])
    load, nominal = status.get("load"), status.get("realpower_nominal")
    if load is None or not nominal:
        return None
    return _watts(load * nominal / 100)


def plug_fetch(ctx: Context):
    """Tests put a fake HTTP client in ctx.senders["energy"]."""
    return ctx.senders.get("energy") if ctx.senders else None


def read_watts(ctx: Context, cfg: dict | None = None, fetch=None) -> float | None:
    """The server's draw now, None without a source or when it cannot be read."""
    cfg = cfg or load_config(ctx)
    if cfg["source"] == "ups":
        return ups_watts(ctx)
    if cfg["source"] == "plug" and cfg["host"]:
        return plug_watts(cfg["plug"], cfg["host"], fetch)
    return None


def cost(kwh: float, cfg: dict) -> dict:
    """kWh and what they cost, as the letter stores them."""
    return {"kwh": round(kwh, 2), "price": cfg["price"], "currency": cfg["currency"],
            "cost": round(kwh * cfg["price"] / 100, 2)}


# --- actions ------------------------------------------------------------------


@action("energy.get", mutating=False)
def energy_get(ctx: Context):
    """Power source, price per kWh and the draw now (watts)."""
    cfg = load_config(ctx)
    return {**cfg, "watts": read_watts(ctx, cfg, plug_fetch(ctx))}


@action(
    "energy.set",
    mutating=True,
    params={
        "source": Param(str, choices=SOURCES),
        "plug": Param(str, required=False, default="shelly", choices=PLUGS),
        "host": Param(str, required=False, default="", pattern=HOST_RE),
        "price": Param(int, required=False, default=35, minimum=0, maximum=1000),
        "currency": Param(str, required=False, default="EUR", choices=CURRENCIES),
    },
)
def energy_set(ctx: Context, source: str, plug: str, host: str, price: int, currency: str):
    """Set where the watts come from and the price per kWh. A plug must answer first."""
    cfg = {"source": source, "plug": plug, "host": host, "price": price, "currency": currency}
    watts = None
    if source == "plug":
        if not host:
            raise AgentError(INVALID_PARAMS, "'host' is missing")
        watts = plug_watts(plug, host, plug_fetch(ctx))
        if watts is None:
            raise AgentError(FAILED, f"no power reading from {host}")
    elif source == "ups":
        if ups.load_config(ctx)["mode"] == "off":
            raise AgentError(FAILED, "no UPS is set up")
        watts = ups_watts(ctx)
    write_file(config_file(ctx), json.dumps(cfg, indent=2) + "\n")
    return {**cfg, "watts": watts}
