"""snapper snapshots of the root filesystem and rollback.

base sets up the snapper config "root" on a Btrfs root; snapper's apt
hook takes a pre/post pair around every apt run. Rollback is
/usr/lib/dahlia/rollback.sh (see there): it runs as the transient unit
rollback.service and reboots into the chosen snapshot. The previous
root stays as a subvolume <root>.pre-rollback-<time> until it is deleted.
"""

import json
import re

from .actions import Context, Param, action, unit_state
from .protocol import BUSY, FAILED, AgentError

SNAPPER = ["snapper", "--no-dbus", "-c", "root"]
ROLLBACK = "/usr/lib/dahlia/rollback.sh"
ROLLBACK_UNIT = "rollback.service"
DESCRIPTION_RE = re.compile(r"^[\w .,:()/+-]{1,80}$")
OLD_ROOT_RE = re.compile(r"^[A-Za-z0-9@._-]+\.pre-rollback-[0-9]{8}-[0-9]{6}$")
SUBVOL_LINE_RE = re.compile(r"^ID \d+ gen \d+ top level 5 path (?:<FS_TREE>/)?(\S+)$")


def available(ctx: Context) -> bool:
    return (ctx.etc / "snapper" / "configs" / "root").is_file()


def parse_list(text: str) -> list[dict]:
    data = json.loads(text or "{}")
    rows = data.get("root", []) if isinstance(data, dict) else []
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("number"):
            continue  # 0 is the running system
        out.append({
            "number": int(r["number"]),
            "type": r.get("type", "single"),
            "pre_number": r.get("pre-number"),
            "date": r.get("date", ""),
            "description": r.get("description", ""),
            "cleanup": r.get("cleanup", ""),
            "userdata": r.get("userdata") or {},
        })
    return out


def old_roots(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        m = SUBVOL_LINE_RE.match(line.strip())
        if m and OLD_ROOT_RE.match(m.group(1)):
            out.append(m.group(1))
    return sorted(out)


def last_rollback(ctx: Context) -> dict | None:
    try:
        data = json.loads((ctx.var / "rollback.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def numbers(ctx: Context) -> set[int]:
    out = ctx.runner.run([*SNAPPER, "--jsonout", "list", "--disable-used-space"]).stdout
    return {s["number"] for s in parse_list(out)}


@action("snapshots.list", mutating=False)
def snapshots_list(ctx: Context):
    """snapper snapshots of /, roots kept by earlier rollbacks and the last rollback."""
    if not available(ctx):
        return {"available": False, "snapshots": [], "old_roots": [], "rollback": None,
                "job": None}
    out = ctx.runner.run([*SNAPPER, "--jsonout", "list", "--disable-used-space"]).stdout
    subvols = ctx.runner.run(["btrfs", "subvolume", "list", "/"], check=False).stdout
    return {
        "available": True,
        "snapshots": parse_list(out),
        "old_roots": old_roots(subvols),
        "rollback": last_rollback(ctx),
        "job": unit_state(ctx, ROLLBACK_UNIT),
    }


@action(
    "snapshots.create",
    mutating=True,
    params={"description": Param(str, pattern=DESCRIPTION_RE)},
)
def snapshots_create(ctx: Context, description: str):
    """Take a snapshot now. It is cleaned up like the apt snapshots (number algorithm)."""
    if not available(ctx):
        raise AgentError(FAILED, "snapper is not set up (root is not Btrfs)")
    out = ctx.runner.run([*SNAPPER, "create", "--cleanup-algorithm", "number",
                          "--description", description, "--print-number"]).stdout
    return {"number": int(out.strip())}


@action(
    "snapshots.delete",
    mutating=True,
    params={"number": Param(int, minimum=1, maximum=999999)},
)
def snapshots_delete(ctx: Context, number: int):
    """Delete one snapshot."""
    if number not in numbers(ctx):
        raise AgentError(FAILED, f"snapshot {number} does not exist")
    ctx.runner.run([*SNAPPER, "delete", str(number)])
    return {"number": number}


@action(
    "snapshots.rollback",
    mutating=True,
    params={"number": Param(int, minimum=1, maximum=999999)},
)
def snapshots_rollback(ctx: Context, number: int):
    """Make a snapshot the root and reboot (rollback.service)."""
    if number not in numbers(ctx):
        raise AgentError(FAILED, f"snapshot {number} does not exist")
    state = unit_state(ctx, ROLLBACK_UNIT)
    if state.get("active") in ("active", "activating"):
        raise AgentError(BUSY, "a rollback is running")
    ctx.runner.run(["systemctl", "reset-failed", ROLLBACK_UNIT], check=False)
    ctx.runner.run(["systemd-run", "--unit", ROLLBACK_UNIT, "--no-block",
                    "--property", "Type=oneshot",
                    "--property", f"OnFailure=notify-failure@{ROLLBACK_UNIT}.service",
                    "--description", f"Dahlia rollback to snapshot {number}",
                    ROLLBACK, str(number), "--reboot"])
    return {"started": True, "number": number}


@action(
    "snapshots.delete_old_root",
    mutating=True,
    params={"name": Param(str, pattern=OLD_ROOT_RE)},
)
def snapshots_delete_old_root(ctx: Context, name: str):
    """Delete a root that an earlier rollback kept."""
    subvols = ctx.runner.run(["btrfs", "subvolume", "list", "/"]).stdout
    if name not in old_roots(subvols):
        raise AgentError(FAILED, f"{name} does not exist")
    ctx.runner.run([ROLLBACK, "--delete-old", name], timeout=600)
    return {"name": name}
