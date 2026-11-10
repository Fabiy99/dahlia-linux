"""Docker actions: list, stats, start/stop/restart, logs, image cleanup.

These cover every container on the host, not only Dahlia apps, like the
Docker tab of a NAS. Containers are addressed by name; the agent never
passes free arguments to docker.
"""

import json
import re

from .actions import Context, Param, action
from .protocol import FAILED, AgentError

# Docker container names: [a-zA-Z0-9][a-zA-Z0-9_.-]+
CONTAINER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
CONTAINER_OPS = ("start", "stop", "restart")
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
SIZE_RE = re.compile(r"^([\d.]+)\s*([kKMGTP]?i?B)$")
UNITS = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "PB": 1e15,
         "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4, "PiB": 1024**5}


def docker(ctx: Context, *args: str, timeout: int = 60, check: bool = True):
    return ctx.runner.run(["docker", *args], timeout=timeout, check=check)


def json_lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def parse_size(text: str) -> int | None:
    """'1.5GiB' or '12.3MB' as bytes; None if it is not a size."""
    m = SIZE_RE.match(text.strip())
    if not m or m.group(2) not in UNITS:
        return None
    return round(float(m.group(1)) * UNITS[m.group(2)])


def parse_labels(text: str) -> dict[str, str]:
    labels = {}
    for item in (text or "").split(","):
        key, sep, value = item.partition("=")
        if sep:
            labels[key] = value
    return labels


def dahlia_app(labels: dict[str, str]) -> str | None:
    """App id for containers of a Dahlia app (compose project dahlia-<id>)."""
    project = labels.get(COMPOSE_PROJECT, "")
    return project[len("dahlia-"):] if project.startswith("dahlia-") else None


@action("docker.list", mutating=False)
def docker_list(ctx: Context):
    """All containers with image, state, ports and the Dahlia app they belong to."""
    proc = docker(ctx, "ps", "-a", "--no-trunc", "--format", "{{json .}}", check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise AgentError(FAILED, f"docker: {detail[-1] if detail else 'not running'}")
    out = []
    for c in json_lines(proc.stdout):
        labels = parse_labels(c.get("Labels", ""))
        out.append({
            "id": c.get("ID", "")[:12],
            "name": c.get("Names", ""),
            "image": c.get("Image", ""),
            "state": c.get("State", ""),
            "status": c.get("Status", ""),
            "ports": c.get("Ports", ""),
            "created": c.get("CreatedAt", ""),
            "app": dahlia_app(labels),
            "service": labels.get(COMPOSE_SERVICE),
        })
    out.sort(key=lambda c: (c["app"] is None, c["app"] or "", c["name"]))
    return {"containers": out}


@action("docker.stats", mutating=False)
def docker_stats(ctx: Context):
    """CPU, memory and I/O of the running containers (one sample)."""
    proc = docker(ctx, "stats", "--no-stream", "--format", "{{json .}}", timeout=30)
    out = {}
    for s in json_lines(proc.stdout):
        mem_used, _, mem_limit = s.get("MemUsage", "").partition("/")
        net_rx, _, net_tx = s.get("NetIO", "").partition("/")
        blk_r, _, blk_w = s.get("BlockIO", "").partition("/")
        try:
            cpu = float(s.get("CPUPerc", "0").rstrip("%") or 0)
        except ValueError:
            cpu = 0.0
        out[s.get("Name", "")] = {
            "cpu_percent": cpu,
            "memory": parse_size(mem_used),
            "memory_limit": parse_size(mem_limit),
            "net_rx": parse_size(net_rx),
            "net_tx": parse_size(net_tx),
            "block_read": parse_size(blk_r),
            "block_write": parse_size(blk_w),
        }
    return {"stats": out}


@action("docker.disk", mutating=False)
def docker_disk(ctx: Context):
    """Space used by images, containers, volumes and build cache."""
    proc = docker(ctx, "system", "df", "--format", "{{json .}}", timeout=60)
    out = []
    for row in json_lines(proc.stdout):
        out.append({
            "type": row.get("Type", ""),
            "total": int(row.get("TotalCount") or 0),
            "active": int(row.get("Active") or 0),
            "size": parse_size(row.get("Size", "")),
            "reclaimable": parse_size(str(row.get("Reclaimable", "")).split(" ")[0]),
        })
    return {"usage": out}


@action(
    "docker.container",
    mutating=True,
    params={
        "name": Param(str, pattern=CONTAINER_RE),
        "op": Param(str, choices=CONTAINER_OPS),
    },
)
def docker_container(ctx: Context, name: str, op: str):
    """Start, stop or restart one container."""
    docker(ctx, op, "--", name, timeout=120)
    return {"name": name, "op": op}


@action(
    "docker.logs",
    mutating=False,
    params={
        "name": Param(str, pattern=CONTAINER_RE),
        "lines": Param(int, required=False, default=200, minimum=10, maximum=5000),
    },
)
def docker_logs(ctx: Context, name: str, lines: int):
    """The last lines of a container's output (stdout and stderr)."""
    proc = docker(ctx, "logs", "--tail", str(lines), "--timestamps", "--", name, timeout=30)
    # docker logs writes the container's stderr to its own stderr; the
    # timestamps keep the merged lines in order.
    merged = sorted((proc.stdout + proc.stderr).splitlines(), key=lambda ln: ln[:35])
    return {"name": name, "lines": merged[-lines:]}


@action("docker.prune", mutating=True)
def docker_prune(ctx: Context):
    """Delete images no container uses and the build cache."""
    out = docker(ctx, "image", "prune", "-a", "-f", timeout=600).stdout
    docker(ctx, "builder", "prune", "-f", timeout=600, check=False)
    m = re.search(r"Total reclaimed space:\s*(\S+)", out)
    return {"reclaimed": parse_size(m.group(1)) if m else 0}
