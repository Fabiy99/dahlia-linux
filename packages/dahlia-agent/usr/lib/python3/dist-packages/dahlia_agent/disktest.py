"""SMART self-tests and the destructive disk test before a disk is used.

Self-tests run inside the disk (smartctl -t short|long); the disk keeps
working meanwhile. dahlia-smart-test@short|long.timer start them on all
disks on a schedule (settings.set_schedule, jobs smart-short and smart-long).

The disk test (burn-in) is for new disks before they hold data: SMART
values before, badblocks writing and reading back four patterns over the
whole disk, SMART values after. It erases the disk, so it only runs on a
disk with no filesystem and no partition (status "empty" in dahlia-disk
list), and confirm must repeat the disk name. It runs as
dahlia-burnin@<name>.service; files are in /var/lib/dahlia/burnin.
"""

import json
import re

from .actions import DEVICE_RE, Context, Param, action, dahlia_disk
from .hardware import ATA_WARN, list_disks, smart_summary
from .protocol import FAILED, INVALID_PARAMS, AgentError
from .settings import show_props

TEST_KINDS = ("short", "long", "abort")
# badblocks -s: "Testing with pattern 0xaa: 12.34% done, ..." and
# "Reading and comparing: 45.00% done, ...", redrawn with backspaces.
PHASE_RE = re.compile(r"Testing with pattern (0x[0-9a-f]+|random)|Reading and comparing")
PERCENT_RE = re.compile(r"([0-9]+\.[0-9]+)% done")
PATTERNS = ("0xaa", "0x55", "0xff", "0x00")
SMART_KEYS = tuple(ATA_WARN.values()) + ("media_errors", "critical_warning")


def disk_paths(ctx: Context) -> set[str]:
    return {d["path"] for d in list_disks(ctx)}


def burnin_unit(name: str) -> str:
    return f"dahlia-burnin@{name}.service"


@action(
    "disks.smart_test",
    mutating=True,
    params={"device": Param(str, pattern=DEVICE_RE), "kind": Param(str, choices=TEST_KINDS)},
)
def disks_smart_test(ctx: Context, device: str, kind: str):
    """Start a short or long SMART self-test, or abort the running one."""
    if device not in disk_paths(ctx):
        raise AgentError(FAILED, f"{device} is not a disk")
    cmd = ["smartctl", "-X", device] if kind == "abort" else ["smartctl", "-t", kind, device]
    proc = ctx.runner.run(cmd, timeout=60, check=False)
    # Bits 0-2: command line, open, SMART command failed.
    if proc.returncode & 0b111:
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        raise AgentError(FAILED, f"smartctl: {lines[-1] if lines else proc.returncode}")
    return {"device": device, "kind": kind}


# --- burn-in ------------------------------------------------------------------


def burnin_dir(ctx: Context):
    return ctx.var / "burnin"


def read_smart_file(path) -> dict | None:
    try:
        return smart_summary(json.loads(path.read_text()))
    except (OSError, ValueError):
        return None


def progress(log: str) -> dict:
    """Pass (1-8: write and read of four patterns) and percent from badblocks -s."""
    phases = list(PHASE_RE.finditer(log))
    if not phases:
        return {"pass": 0, "percent": 0.0}
    writes = 0
    for m in phases:
        if m.group(1) in PATTERNS:
            writes = PATTERNS.index(m.group(1)) + 1
    last = phases[-1]
    step = writes * 2 - (0 if last.group(0).startswith("Reading") else 1)
    # Percent printed since the last phase started; none yet means 0.
    percents = PERCENT_RE.findall(log, last.end())
    return {"pass": max(step, 1), "percent": float(percents[-1]) if percents else 0.0}


def new_problems(before: dict | None, after: dict | None) -> dict:
    """SMART counters that grew during the test."""
    if not after:
        return {}
    old = (before or {}).get("warnings", {})
    return {k: v for k, v in after.get("warnings", {}).items()
            if k in SMART_KEYS and v > old.get(k, 0)}


def burnin_state(ctx: Context, name: str) -> dict | None:
    d = burnin_dir(ctx)
    log = d / f"{name}.log"
    if not log.exists():
        return None
    props = show_props(ctx, ["systemctl", "show", burnin_unit(name), "-p", "ActiveState",
                             "-p", "Result", "-p", "ExecMainStartTimestamp"])
    running = props.get("ActiveState") in ("active", "activating", "deactivating")
    try:
        text = log.read_text(errors="replace")
    except OSError:
        text = ""
    bad_file = d / f"{name}.bad"
    try:
        bad = sum(1 for ln in bad_file.read_text().splitlines() if ln.strip())
    except OSError:
        bad = 0
    before = read_smart_file(d / f"{name}.smart-before.json")
    after = read_smart_file(d / f"{name}.smart-after.json")
    finished = (d / f"{name}.done").exists()
    grown = new_problems(before, after)
    if running:
        result = "running"
    elif not finished:
        result = "stopped"
    elif bad or grown or (after or {}).get("health") == "failed":
        result = "failed"
    else:
        result = "passed"
    return {"device": f"/dev/{name}", "result": result, **progress(text), "bad_blocks": bad,
            "smart_new": grown, "started": props.get("ExecMainStartTimestamp", "")}


@action("disks.burnin_status", mutating=False)
def disks_burnin_status(ctx: Context):
    """Disk tests that run or ran, newest state of each."""
    d = burnin_dir(ctx)
    names = sorted(p.stem for p in d.glob("*.log")) if d.is_dir() else []
    tests = [s for s in (burnin_state(ctx, n) for n in names) if s]
    return {"tests": tests}


def blank_disk(ctx: Context, device: str) -> None:
    entries = [d for d in json.loads(dahlia_disk(ctx, "list", "--json")) if d["disk"] == device]
    if not entries:
        raise AgentError(FAILED, f"{device} is not a disk Dahlia can use")
    for e in entries:
        if e["kind"] != "disk" or e["status"] != "empty":
            raise AgentError(FAILED, f"{device} is not empty ({e['path']}: {e['reason']}); "
                                     "the disk test only runs on new, blank disks")


@action(
    "disks.burnin_start",
    mutating=True,
    params={"device": Param(str, pattern=DEVICE_RE), "confirm": Param(str)},
)
def disks_burnin_start(ctx: Context, device: str, confirm: str):
    """Start the erasing disk test on a blank disk. confirm repeats the disk name."""
    name = device.removeprefix("/dev/")
    if confirm != name:
        raise AgentError(INVALID_PARAMS, f"confirm must be '{name}'")
    if device.startswith("/dev/loop"):
        raise AgentError(INVALID_PARAMS, "loop devices are not tested")
    blank_disk(ctx, device)
    active = ctx.runner.run(["systemctl", "is-active", burnin_unit(name)], check=False)
    if active.stdout.strip() in ("active", "activating"):
        raise AgentError(FAILED, f"the disk test of {device} is already running")
    ctx.runner.run(["systemctl", "reset-failed", burnin_unit(name)], check=False)
    ctx.runner.run(["systemctl", "start", "--no-block", burnin_unit(name)])
    return {"device": device}


@action("disks.burnin_stop", mutating=True, params={"device": Param(str, pattern=DEVICE_RE)})
def disks_burnin_stop(ctx: Context, device: str):
    """Stop a running disk test. The disk is left partly overwritten."""
    ctx.runner.run(["systemctl", "stop", "--no-block", burnin_unit(device.removeprefix("/dev/"))])
    return {"device": device}


@action("disks.burnin_clear", mutating=True, params={"device": Param(str, pattern=DEVICE_RE)})
def disks_burnin_clear(ctx: Context, device: str):
    """Forget the result of a finished disk test."""
    name = device.removeprefix("/dev/")
    active = ctx.runner.run(["systemctl", "is-active", burnin_unit(name)], check=False)
    if active.stdout.strip() in ("active", "activating"):
        raise AgentError(FAILED, f"the disk test of {device} is still running")
    for suffix in ("log", "bad", "done", "smart-before.json", "smart-after.json"):
        (burnin_dir(ctx) / f"{name}.{suffix}").unlink(missing_ok=True)
    return {"device": device}


def burnin_running(ctx: Context, device: str) -> bool:
    proc = ctx.runner.run(["systemctl", "is-active", burnin_unit(device.removeprefix("/dev/"))],
                          check=False)
    return proc.stdout.strip() in ("active", "activating", "deactivating")
