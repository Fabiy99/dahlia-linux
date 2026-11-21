"""Take over an Unraid setup after the migration helper installed Dahlia.

The helper (migrate/ in the repository) copies the Unraid configuration to
/var/lib/dahlia/unraid/: unraid.json (its parsed summary) and config/
(raw files: smbpasswd, shares, Docker templates, WireGuard). Network,
hostname and time zone were already set by the helper's installer.

unraid.status builds the plan from those files and the disks that are
connected now; unraid.import runs it in the background as
unraid-import.service (/usr/sbin/unraid-import), which writes its progress
to /var/lib/dahlia/unraid/import.json. Every step can run again: what is
already there (registered disk, user, share, app) is left alone, so a
failed import is simply started once more.

Steps, in order:
  disks       data disks in Unraid's slot order and the cache, by serial,
              without formatting. Unraid keeps shares at the top of the
              cache; Dahlia's cache branch is /mnt/cache/shares, so the
              top-level folders are moved there (a rename, same filesystem).
  parity      Unraid parity is not SnapRAID parity. Only with parity=true:
              the parity disk is formatted, added and synced at the end.
  users       SMB users with their NT hash (pdbedit), no new password.
  shares      exported shares with security and user lists.
  wireguard   wg0 with the server key, port, subnet and peers, so phones
              keep their configuration.
  containers  Docker templates as custom containers: same image, ports,
              folders and variables. The appdata folder moves to
              appdata/<id>/<name> on each disk, where Dahlia's apps keep it.

Not taken over: plugins, VMs, bonding/VLAN/bridges, encrypted (LUKS) and
ZFS disks, more than one pool, Docker networks and the host network.
"""

import argparse
import contextlib
import ipaddress
import json
import os
import re
import sys
from pathlib import Path

from .actions import Context, Param, action, is_running
from .protocol import BUSY, FAILED, INVALID_PARAMS, AgentError
from .settings import read_json, write_file

UNIT = "unraid-import.service"
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
SLUG_RE = re.compile(r"[^a-z0-9]+")
# Docker Hub style references as Unraid templates have them, tag optional.
REPO_RE = re.compile(r"^(?:[a-z0-9.-]+(?::[0-9]+)?/)?[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
WEBUI_PORT_RE = re.compile(r"\[PORT:([0-9]{1,5})\]")
UNRAID_POOL_RE = re.compile(r"^/mnt/(user0?|cache|disk[0-9]{1,2})/(.+)$")
NT_RE = re.compile(r"^[0-9A-Fa-f]{32}$")
FIRST_FREE_PORT = 8100
MAX_ITEMS = 32


def base(ctx: Context) -> Path:
    return ctx.var / "unraid"


def summary_file(ctx: Context) -> Path:
    return base(ctx) / "unraid.json"


def progress_file(ctx: Context) -> Path:
    return base(ctx) / "import.json"


def request_file(ctx: Context) -> Path:
    return base(ctx) / "request.json"


def dismissed_file(ctx: Context) -> Path:
    return base(ctx) / "dismissed"


def load_summary(ctx: Context) -> dict | None:
    data = read_json(summary_file(ctx), {})
    return data if data.get("found") else None


def note(key: str, value: str = "") -> dict:
    return {"key": key, "value": value}


# --- disks --------------------------------------------------------------------


def matches(unraid_id: str, serial: str | None) -> bool:
    """Unraid's id is model_serial; Dahlia knows the serial lsblk reports."""
    if not serial or not unraid_id:
        return False
    return unraid_id == serial or unraid_id.endswith("_" + serial)


def disk_items(summary: dict, found: list[dict]) -> list[dict]:
    out = []
    pools = sorted({d["pool"] for d in summary.get("disks", []) if d["role"] == "cache"})
    pool = "cache" if "cache" in pools else (pools[0] if pools else "")
    cache_done = False
    for d in summary.get("disks", []):
        if d["role"] == "flash":
            continue
        item = {"kind": "disk", "name": d["slot"], "role": d["role"], "id": d.get("id", ""),
                "fs": d.get("fs", ""), "notes": []}
        out.append(item)
        entries = [f for f in found if matches(d.get("id", ""), f.get("serial"))]
        if d["role"] == "cache" and (d["pool"] != pool or cache_done):
            # Members of a Btrfs pool share one UUID; adding the first adds all.
            same = any(f.get("uuid") and f["uuid"] == (o.get("uuid") or "") for f in entries
                       for o in out if o is not item and o.get("role") == "cache")
            item.update(state="done" if same else "skip",
                        reason="pool_member" if same else "second_pool")
            continue
        if not entries:
            item.update(state="skip", reason="missing")
            continue
        item["device"] = entries[0].get("disk")
        statuses = {f["status"] for f in entries}
        if d["role"] == "parity":
            if statuses == {"registered"}:
                item.update(state="done", reason="")
            elif statuses <= {"empty"}:
                item.update(state="confirm", reason="parity_rebuild")
            else:
                item.update(state="skip", reason="parity_not_empty")
            continue
        usable = [f for f in entries if f["status"] in ("candidate", "registered")]
        if any(f["status"] == "registered" for f in entries):
            item.update(state="done", reason="", uuid=usable[0].get("uuid"))
        elif usable:
            item.update(state="ready", reason="", uuid=usable[0]["uuid"])
        elif "locked" in statuses:
            item.update(state="skip", reason="encrypted")
        else:
            item.update(state="skip", reason="unsupported_fs")
        if d["role"] == "cache":
            cache_done = True
    return out


def cache_layout(ctx: Context) -> list[str]:
    """Move Unraid's top-level cache folders below /mnt/cache/shares."""
    cache = ctx.pool.parent / "cache"
    shares = cache / "shares"
    shares.mkdir(mode=0o2775, exist_ok=True)
    moved = []
    for entry in sorted(cache.iterdir()):
        if entry.name == "shares" or entry.name.startswith(".") or not entry.is_dir() \
                or entry.is_symlink():
            continue
        target = shares / entry.name
        if target.exists():
            continue
        entry.rename(target)
        moved.append(entry.name)
    return moved


# --- users and shares ---------------------------------------------------------


def nt_hashes(ctx: Context) -> dict[str, str]:
    out = {}
    text = ""
    with contextlib.suppress(OSError):
        text = (base(ctx) / "config" / "smbpasswd").read_text(errors="replace")
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and NT_RE.match(parts[3]):
            out[parts[0]] = parts[3].upper()
    return out


def user_items(ctx: Context, summary: dict, existing: set[str]) -> list[dict]:
    from .smb import USER_RE

    hashes = nt_hashes(ctx)
    out = []
    for u in summary.get("users", []):
        name = u["name"]
        item = {"kind": "user", "name": name, "notes": []}
        if not USER_RE.match(name):
            item.update(state="skip", reason="invalid_name")
        elif name in existing:
            item.update(state="done", reason="")
        elif name not in hashes:
            item.update(state="skip", reason="no_password")
        else:
            item.update(state="ready", reason="")
        out.append(item)
    return out


def share_items(summary: dict, users: list[dict], existing: set[str]) -> list[dict]:
    from .smb import COMMENT_RE, RESERVED_SHARES, SHARE_RE

    known = {u["name"] for u in users if u["state"] in ("ready", "done")}
    out = []
    for s in summary.get("shares", []):
        access = {u: "ro" for u in s.get("read", []) if u in known}
        access.update({u: "rw" for u in s.get("write", []) if u in known})
        item = {"kind": "share", "name": s["name"], "security": s.get("security", "public"),
                "comment": s.get("comment", "") if COMMENT_RE.match(s.get("comment", "")) else "",
                "users": access, "notes": []}
        if not SHARE_RE.match(s["name"]) or s["name"].lower() in RESERVED_SHARES:
            item.update(state="skip", reason="invalid_name")
        elif s["name"].lower() in existing:
            item.update(state="done", reason="")
        elif not s.get("smb", True):
            item.update(state="skip", reason="not_exported")
        else:
            item.update(state="ready", reason="")
            if item["security"] == "private" and not access:
                # Nobody left who may log in: secure keeps it readable, writes need a user.
                item["security"] = "secure"
                item["notes"].append(note("private_without_users"))
            if s.get("cache") not in ("no", "yes", ""):
                item["notes"].append(note("cache_mode", s.get("cache", "")))
        out.append(item)
    return out


# --- WireGuard ----------------------------------------------------------------


def parse_wg(text: str) -> dict | None:
    """wg0.conf as Unraid writes it: interface and peers, comments as names."""
    iface: dict[str, str] = {}
    peers: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.lower() == "[interface]":
            current = iface
        elif line.lower() == "[peer]":
            current = {}
            peers.append(current)
        elif line.startswith("#") and current is not None and current is not iface:
            current.setdefault("#", line.lstrip("#").strip())
        elif "=" in line and current is not None:
            key, value = line.split("=", 1)
            current[key.strip().lower()] = value.strip()
    if not iface.get("privatekey"):
        return None
    return {"interface": iface, "peers": peers}


def wireguard_plan(ctx: Context) -> dict | None:
    from . import vpn

    text = ""
    with contextlib.suppress(OSError):
        text = (base(ctx) / "config" / "wireguard" / "wg0.conf").read_text(errors="replace")
    wg = parse_wg(text)
    if wg is None:
        return None
    iface = wg["interface"]
    item = {"kind": "wireguard", "name": "wg0", "notes": [], "peers": []}
    private = iface.get("privatekey", "")
    first = iface.get("address", "").split(",")[0].strip()
    try:
        # Unraid writes the server address without a prefix; its tunnels are /24.
        addr = ipaddress.ip_interface(first if "/" in first else first + "/24")
        port = int(iface.get("listenport", vpn.DEFAULT_PORT))
    except ValueError:
        return {**item, "state": "skip", "reason": "unreadable"}
    subnet = str(addr.network)
    if not vpn.KEY_RE.fullmatch(private) or not vpn.SUBNET_RE.fullmatch(subnet) \
            or not 1024 <= port <= 65535:
        return {**item, "state": "skip", "reason": "unreadable"}
    if str(addr.ip) != vpn.server_address(subnet).split("/")[0]:
        item["notes"].append(note("server_address", vpn.server_address(subnet).split("/")[0]))
    net = addr.network
    used = set()
    for i, p in enumerate(wg["peers"]):
        public = p.get("publickey", "")
        psk = p.get("presharedkey", "")
        name = re.sub(r"[^A-Za-z0-9 ._-]", "", p.get("#", ""))[:32].strip(" ._-")
        name = name if vpn.PEER_NAME_RE.fullmatch(name or "-") else f"Peer {i + 1}"
        address = None
        for a in p.get("allowedips", "").split(","):
            with contextlib.suppress(ValueError):
                ip = ipaddress.ip_interface(a.strip()).ip
                if ip in net and str(ip) not in used:
                    address = f"{ip}/32"
                    break
        if not vpn.KEY_RE.fullmatch(public) or address is None \
                or (psk and not vpn.KEY_RE.fullmatch(psk)):
            item["notes"].append(note("peer_skipped", name))
            continue
        used.add(address.split("/")[0])
        item["peers"].append({"name": name, "address": address, "public_key": public,
                              "psk": psk})
    cfg = vpn.load(ctx)
    item.update(private_key=private, port=port, subnet=subnet)
    if cfg["peers"]:
        # Dahlia's WireGuard already has peers: imported before, or set up by hand.
        mine = {p["public_key"] for p in cfg["peers"]}
        done = mine >= {p["public_key"] for p in item["peers"]}
        item.update(state="done" if done else "skip", reason="" if done else "wireguard_in_use")
    else:
        item.update(state="ready", reason="")
    return item


def import_wireguard(ctx: Context, item: dict) -> None:
    from . import vpn

    cfg = vpn.load(ctx)
    created = int(ctx.clock())
    cfg.update(enabled=True, port=item["port"], subnet=item["subnet"],
               peers=[{**p, "created": created} for p in item["peers"]])
    key = vpn.key_file(ctx)
    key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_file(key, item["private_key"] + "\n", mode=0o600)
    vpn.save(ctx, cfg)
    vpn.apply(ctx, cfg, restart=True)


# --- containers ---------------------------------------------------------------


def slug(text: str, limit: int = 31) -> str:
    s = SLUG_RE.sub("-", text.lower()).strip("-")
    if not s or not s[0].isalpha():
        s = "app-" + s
    return s[:limit].rstrip("-") or "app"


def split_image(image: str) -> tuple[str, str]:
    """"lscr.io/linuxserver/plex:latest" -> ("lscr.io/linuxserver/plex", "latest")."""
    image = image.split("@", 1)[0]
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = image.rsplit(":", 1)
        return repo, tag
    return image, ""


def normal_repo(repo: str) -> str:
    """docker.io/library/nginx and nginx are the same image."""
    r = repo.lower().removeprefix("docker.io/").removeprefix("registry-1.docker.io/")
    return r.removeprefix("library/")


def host_folder(path: str, pools: set[str]) -> str | None:
    """An Unraid path below /mnt as the same path below /mnt/user."""
    path = path.rstrip("/")
    m = UNRAID_POOL_RE.match(path)
    rel = None
    if m:
        rel = m.group(2)
    else:
        for p in pools:
            if path.startswith(f"/mnt/{p}/"):
                rel = path[len(f"/mnt/{p}/"):]
    return rel


def container_item(c: dict, app_id: str, appdata_root: str, pools: set[str],
                   catalog: dict[str, str], tag: str | None, taken_ports: set[int]) -> dict:
    """One Docker template as an apps.custom_install spec, with notes."""
    from . import apps
    from .actions import FOLDER_RE

    notes: list[dict] = []
    item = {"kind": "container", "name": c["name"], "id": app_id, "notes": notes,
            "moves": []}
    repo, image_tag = split_image(c.get("image", ""))
    if not REPO_RE.fullmatch(repo.lower()):
        return {**item, "state": "skip", "reason": "image"}
    repo = repo.lower()
    item["repo"] = repo
    chosen = tag or (image_tag if image_tag and image_tag != "latest" else "")
    if not chosen and normal_repo(repo) in catalog:
        chosen = catalog[normal_repo(repo)]
        notes.append(note("catalog_tag", chosen))
    item["tag"] = chosen
    if c.get("network", "bridge") not in ("bridge", ""):
        notes.append(note("network", c.get("network", "")))
    if c.get("privileged"):
        notes.append(note("privileged"))

    # Ports: the WebUI port becomes the port link, the others LAN ports.
    web_target = None
    m = WEBUI_PORT_RE.search(c.get("webui", ""))
    if m:
        web_target = int(m.group(1))
    web_port = public_port = None
    lan = []
    seen_host: set[int] = set()
    for p in c.get("ports", [])[:MAX_ITEMS]:
        host, target, proto = p["host"], p["container"], p.get("protocol", "tcp")
        if not (1 <= host <= 65535 and 1 <= target <= 65535):
            continue
        is_web = web_target == target and proto == "tcp" and web_port is None
        reserved = apps.RESERVED_PUBLIC if is_web else apps.RESERVED_PORTS
        if host in reserved or host in apps.HOST_PORTS or host in taken_ports \
                or host in seen_host:
            new = FIRST_FREE_PORT
            while new in taken_ports or new in seen_host or new in apps.RESERVED_PUBLIC:
                new += 1
            notes.append(note("port_changed", f"{host}->{new}"))
            host = new
        seen_host.add(host)
        if is_web:
            web_port, public_port = target, host
        else:
            lan.append({"container": target, "host": host, "protocol": proto})
    taken_ports.update(seen_host)

    # Folders: appdata stays appdata, the rest points at the same /mnt/user path.
    volumes = []
    targets = set()
    appdata = appdata_root.rstrip("/")
    appdata_rel = host_folder(appdata, pools) if appdata.startswith("/mnt/") else None
    for p in c.get("paths", [])[:MAX_ITEMS]:
        host, target = p["host"].rstrip("/"), p["container"].rstrip("/") or "/"
        if target in targets or not target.startswith("/") or target == "/":
            continue
        rel = host_folder(host, pools)
        if rel is None:
            notes.append(note("path_skipped", host))
            continue
        old = None
        if appdata_rel and (rel == appdata_rel or rel.startswith(appdata_rel + "/")):
            sub = rel[len(appdata_rel) + 1:]
            if "/" in sub or not sub:
                notes.append(note("path_skipped", host))
                continue
            old = sub
        elif rel.split("/")[0] == "appdata":
            sub = rel.split("/", 1)[1] if "/" in rel else ""
            if "/" in sub or not sub:
                notes.append(note("path_skipped", host))
                continue
            old = sub
        if old is not None:
            name = slug(old, 63)
            name = name if apps.SUBDIR_RE.fullmatch(name) else "data"
            if any(mv["from"] == old for mv in item["moves"]):
                notes.append(note("path_skipped", host))
                continue
            item["moves"].append({"from": old, "to": name})
            volumes.append({"container": target, "appdata": name,
                            **({"readonly": True} if p.get("readonly") else {})})
        else:
            if not FOLDER_RE.fullmatch(rel) or ".." in rel.split("/"):
                notes.append(note("path_skipped", host))
                continue
            volumes.append({"container": target, "folder": f"/mnt/user/{rel}",
                            **({"readonly": True} if p.get("readonly") else {})})
        targets.add(target)

    env = {}
    for key, value in list(c.get("env", {}).items())[:MAX_ITEMS]:
        # {NAME} would be read as a Dahlia placeholder.
        if not apps.ENV_KEY_RE.fullmatch(key) or len(value) > 1000 or "\0" in value \
                or apps.PLACEHOLDER_RE.search(value):
            notes.append(note("variable_skipped", key))
        else:
            env[key] = value
    gpu = False
    for dev in c.get("devices", []):
        if dev.startswith("/dev/dri"):
            gpu = True
        else:
            notes.append(note("device", dev))
    name = re.sub(r"[^\w .+-]", "", c["name"])[:40].strip() or app_id
    spec = {"name": name, "image": f"{repo}:{chosen}" if chosen else repo,
            "lan_ports": lan, "volumes": volumes, "environment": env, "gpu": gpu}
    if web_port is not None:
        spec.update(web_port=web_port, public_port=public_port)
    item["spec"] = spec
    if not chosen:
        return {**item, "state": "needs_tag", "reason": "latest"}
    try:
        apps.custom_template(app_id, spec)
    except apps.AppError as e:
        return {**item, "state": "skip", "reason": "invalid", "error": str(e)}
    return {**item, "state": "ready", "reason": ""}


def catalog_tags(ctx: Context) -> dict[str, str]:
    from .actions import apps_catalog

    try:
        cat = apps_catalog(ctx, False)
    except AgentError:
        return {}
    out = {}
    for a in cat.get("apps", []) if isinstance(cat, dict) else []:
        for s in a.get("services") or [{"image": a.get("image", "")}]:
            repo, tag = split_image(s.get("image") or "")
            if tag:
                out.setdefault(normal_repo(repo), tag)
    return out


def installed_apps(ctx: Context) -> list[dict]:
    from .actions import apps_list

    try:
        return apps_list(ctx).get("apps", [])
    except AgentError:
        return []


def container_items(ctx: Context, summary: dict, tags: dict[str, str]) -> list[dict]:
    from .apps import RESERVED_PUBLIC

    installed = installed_apps(ctx)
    ids = {a.get("id") for a in installed}
    taken = set(RESERVED_PUBLIC)
    for a in installed:
        for p in [a.get("public_port")] + [lp.get("host") for lp in a.get("lan_ports") or []]:
            if isinstance(p, int):
                taken.add(p)
    pools = {d["pool"] for d in summary.get("disks", []) if d.get("pool")}
    catalog = catalog_tags(ctx)
    out = []
    used: set[str] = set()
    for c in summary.get("containers", []):
        app_id = slug(c["name"])
        if app_id in ids and (base(ctx) / "apps" / app_id).exists():
            out.append({"kind": "container", "name": c["name"], "id": app_id, "notes": [],
                        "state": "done", "reason": ""})
            used.add(app_id)
            continue
        n = 2
        candidate = app_id
        while candidate in ids or candidate in used:
            candidate = f"{app_id[:28]}-{n}"
            n += 1
        used.add(candidate)
        tag = tags.get(c["name"])
        out.append(container_item(c, candidate, summary.get("appdata", "/mnt/user/appdata/"),
                                  pools, catalog, tag, taken))
    return out


def move_appdata(ctx: Context, app_id: str, moves: list[dict]) -> int:
    """appdata/<old> -> appdata/<id>/<new> on every branch of /mnt/user."""
    mnt = ctx.pool.parent
    branches = [mnt / "cache" / "shares", *sorted(mnt.glob("disk[0-9]*"))]
    count = 0
    for b in branches:
        appdata = b / "appdata"
        for mv in moves:
            src = appdata / mv["from"]
            if not src.is_dir() or src.is_symlink():
                continue
            dst = appdata / app_id / mv["to"]
            if dst.exists():
                continue
            dst.parent.mkdir(mode=0o775, exist_ok=True)
            src.rename(dst)
            count += 1
    return count


# --- plan, status, import ---------------------------------------------------------


def build_plan(ctx: Context, summary: dict, tags: dict[str, str] | None = None) -> dict:
    from .actions import storage_list
    from .smb import load_shares, smb_users

    found = storage_list(ctx)["disks"]
    users = user_items(ctx, summary, set(smb_users(ctx)))
    plan = {
        "identity": summary.get("identity", {}),
        "network": summary.get("network", {}),
        "disks": disk_items(summary, found),
        "users": users,
        "shares": share_items(summary, users, {s["name"].lower() for s in load_shares(ctx)}),
        "wireguard": wireguard_plan(ctx),
        "containers": container_items(ctx, summary, tags or {}),
        "not_taken": [k for k, v in (("vms", summary.get("vms")), ("plugins", True)) if v],
    }
    if plan["wireguard"] is None and summary.get("wireguard"):
        plan["not_taken"].append("wireguard")
    extra = [w for w in summary.get("wireguard", []) if w != "wg0.conf"]
    if extra:
        plan["not_taken"].append("wireguard_more")
    return plan


def public_plan(plan: dict) -> dict:
    """The plan without keys and specs, for the dashboard."""
    out = dict(plan)
    if plan.get("wireguard"):
        wg = {k: v for k, v in plan["wireguard"].items() if k != "private_key"}
        wg["peers"] = [p["name"] for p in plan["wireguard"].get("peers", [])]
        out["wireguard"] = wg
    out["containers"] = [{k: v for k, v in c.items() if k not in ("spec", "moves")}
                         | ({"image": c["spec"]["image"]} if "spec" in c else {})
                         for c in plan["containers"]]
    return out


def progress(ctx: Context) -> dict | None:
    data = read_json(progress_file(ctx), {})
    if not data:
        return None
    if data.get("state") == "running" and not is_running(ctx, UNIT):
        data["state"] = "failed"
    return data


@action("unraid.status", mutating=False)
def unraid_status(ctx: Context):
    """Unraid setup found by the migration helper, the import plan and its progress."""
    summary = load_summary(ctx)
    if summary is None:
        return {"found": False}
    job = progress(ctx)
    running = bool(job and job.get("state") == "running")
    req = read_json(request_file(ctx), {})
    plan = None if running else public_plan(build_plan(ctx, summary, req.get("tags") or {}))
    return {"found": True, "dismissed": dismissed_file(ctx).exists(), "plan": plan,
            "job": job}


@action(
    "unraid.import",
    mutating=True,
    params={"parity": Param(bool, required=False, default=False),
            "tags": Param(dict, required=False, default={})},
)
def unraid_import(ctx: Context, parity: bool, tags: dict):
    """Take over disks, users, shares, WireGuard and containers in the background.

    parity formats the Unraid parity disk for SnapRAID; tags maps a
    container name to the image version to use instead of "latest".
    """
    if load_summary(ctx) is None:
        raise AgentError(FAILED, "no Unraid setup to import")
    if len(tags) > 100:
        raise AgentError(INVALID_PARAMS, "'tags' is too large")
    for name, tag in tags.items():
        if not isinstance(name, str) or len(name) > 100 or not isinstance(tag, str) \
                or not TAG_RE.fullmatch(tag) or tag == "latest":
            raise AgentError(INVALID_PARAMS, f"'tags.{name}' must be an image version")
    if is_running(ctx, UNIT):
        raise AgentError(BUSY, "the import is running")
    write_file(request_file(ctx), json.dumps({"parity": parity, "tags": tags}), mode=0o600)
    write_file(progress_file(ctx), json.dumps({"state": "running", "step": "start",
                                               "started": int(ctx.clock()), "items": []}))
    ctx.runner.run(["systemctl", "reset-failed", UNIT], check=False)
    ctx.runner.run(["systemd-run", f"--unit={UNIT}", "--description=Dahlia: Unraid import",
                    "--property=After=docker.service", "--", "/usr/sbin/unraid-import"])
    dismissed_file(ctx).unlink(missing_ok=True)
    return {"started": True}


@action("unraid.dismiss", mutating=True, params={"dismissed": Param(bool)})
def unraid_dismiss(ctx: Context, dismissed: bool):
    """Hide or show the Unraid import offer on the start page."""
    if dismissed:
        write_file(dismissed_file(ctx), "")
    else:
        dismissed_file(ctx).unlink(missing_ok=True)
    return {"dismissed": dismissed}


class Progress:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.data = {"state": "running", "step": "start", "started": int(ctx.clock()),
                     "items": []}

    def step(self, name: str) -> None:
        self.data["step"] = name
        self.save()

    def item(self, kind: str, name: str, state: str, error: str = "") -> None:
        self.data["items"].append({"kind": kind, "name": name, "state": state,
                                   **({"error": error} if error else {})})
        self.save()

    def finish(self, state: str) -> None:
        self.data.update(state=state, step="finished", finished=int(self.ctx.clock()))
        self.save()

    def save(self) -> None:
        write_file(progress_file(self.ctx), json.dumps(self.data, indent=1))


def add_users(ctx: Context, names: list[str]) -> None:
    """useradd like shares.user_add, then the Unraid NT hash via pdbedit."""
    from .smb import DATA_GROUP, SMB_GROUP

    hashes = nt_hashes(ctx)
    for name in names:
        ctx.runner.run(["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent",
                        "--shell", "/usr/sbin/nologin", "--groups", f"{DATA_GROUP},{SMB_GROUP}",
                        name])
    lines = []
    for name in names:
        uid = ctx.runner.run(["id", "-u", name]).stdout.strip()
        lines.append(f"{name}:{uid}:{'X' * 32}:{hashes[name]}:[U          ]:LCT-00000000:")
    f = ctx.run_dir / "unraid-smbpasswd"
    write_file(f, "\n".join(lines) + "\n", mode=0o600)
    try:
        ctx.runner.run(["pdbedit", "-i", f"smbpasswd:{f}", "-e", "tdbsam"])
    finally:
        f.unlink(missing_ok=True)


def run_import(ctx: Context) -> int:
    from .actions import (
        dahlia_apps,
        parity_sync,
        storage_add,
        storage_format,
        write_spec,
    )
    from .smb import shares_set

    summary = load_summary(ctx)
    if summary is None:
        print("unraid-import: no Unraid setup found", file=sys.stderr)
        return 1
    req = read_json(request_file(ctx), {})
    plan = build_plan(ctx, summary, req.get("tags") or {})
    prog = Progress(ctx)
    failed = False

    def attempt(kind: str, name: str, fn) -> bool:
        nonlocal failed
        try:
            fn()
        except (AgentError, OSError, ValueError, KeyError) as e:
            failed = True
            prog.item(kind, name, "failed", getattr(e, "message", str(e)))
            return False
        prog.item(kind, name, "done")
        return True

    prog.step("disks")
    parity_added = False
    for d in plan["disks"]:
        if d["state"] == "ready":
            def add(d=d):
                storage_add(ctx, d["uuid"], "cache" if d["role"] == "cache" else "data")
                if d["role"] == "cache":
                    cache_layout(ctx)
            attempt("disk", d["name"], add)
        elif d["state"] == "confirm" and req.get("parity"):
            def add_parity(d=d):
                dev = d["device"]
                res = storage_format(ctx, dev, "xfs", dev.removeprefix("/dev/"), False, None)
                storage_add(ctx, res["uuid"], "parity")
            parity_added = attempt("disk", d["name"], add_parity) or parity_added

    prog.step("users")
    names = [u["name"] for u in plan["users"] if u["state"] == "ready"]
    if names:
        ok = attempt("user", ", ".join(names), lambda: add_users(ctx, names))
        if not ok:
            for s in plan["shares"]:
                s["users"] = {u: m for u, m in s["users"].items() if u not in names}

    prog.step("shares")
    for s in plan["shares"]:
        if s["state"] != "ready":
            continue
        security = s["security"] if s["users"] or s["security"] != "private" else "secure"
        attempt("share", s["name"], lambda s=s, security=security: shares_set(
            ctx, s["name"], s["comment"], security, s["users"], False, 0, False, 0))

    wg = plan.get("wireguard")
    if wg and wg["state"] == "ready":
        prog.step("wireguard")
        attempt("wireguard", "wg0", lambda: import_wireguard(ctx, wg))

    prog.step("containers")
    for c in plan["containers"]:
        if c["state"] != "ready":
            continue

        def install(c=c):
            move_appdata(ctx, c["id"], c["moves"])
            path = write_spec(ctx, c["id"], c["spec"])
            extra = ["--spec", str(path), "--location", "/mnt/user"]
            dahlia_apps(ctx, "precheck", "install", c["id"], *extra)
            ctx.runner.run(["/usr/sbin/apps", "install", c["id"], *extra], timeout=3600)
            (base(ctx) / "apps").mkdir(exist_ok=True)
            (base(ctx) / "apps" / c["id"]).touch()
        # Directly, not as apps.custom_install: one pull after the other.
        attempt("container", c["name"], install)

    if parity_added:
        prog.step("parity")
        attempt("parity", "sync", lambda: parity_sync(ctx, False))

    prog.finish("failed" if failed else "done")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="unraid-import",
                                description="Take over an Unraid setup (unraid.import).")
    p.parse_args(argv)
    ctx = Context()
    os.umask(0o022)
    try:
        return run_import(ctx)
    except Exception as e:  # noqa: BLE001 - the dashboard has to see any failure
        prog = Progress(ctx)
        prog.data.update(read_json(progress_file(ctx), {}))
        prog.item("import", "import", "failed", str(e))
        prog.finish("failed")
        raise
