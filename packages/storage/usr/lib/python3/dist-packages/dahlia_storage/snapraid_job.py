"""dahlia-snapraid: run SnapRAID for the Dahlia array.

    dahlia-snapraid sync [--force]
    dahlia-snapraid scrub
    dahlia-snapraid fix --disk N | --bad
    dahlia-snapraid status [--json]

sync writes /etc/snapraid.conf from storage.json, runs `snapraid diff` and
aborts if more files were removed than the configured threshold (a disk
that failed to mount or an accidental delete would otherwise be synced
into the parity). Then it runs `snapraid sync`.

fix --disk N rebuilds the files of data disk N from the parity, typically
onto a new disk that took the place of a failed one, and verifies them
with `snapraid check`. fix --bad repairs the blocks a scrub found broken
and scrubs them again.

status prints what the parity covers, the scrub state, errors and the
result of the last sync, scrub and fix (/var/lib/dahlia/snapraid.json).

sync, scrub and fix take the array lock, so they never run at the same time
as the mover or each other. Without parity disks they do nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

from . import snapraid, state

SNAPRAID = os.environ.get("DAHLIA_SNAPRAID", "snapraid")
RUNS_PATH = os.environ.get("DAHLIA_SNAPRAID_RUNS", "/var/lib/dahlia/snapraid.json")
FIX_LOG = os.environ.get("DAHLIA_SNAPRAID_FIX_LOG", "/var/log/dahlia/snapraid-fix.log")
HISTORY_MAX = 100
FIX_COUNT = re.compile(r"^\s*(\d+)\s+(recovered|unrecoverable) errors\s*$")


class JobError(RuntimeError):
    pass


def write_config(st: state.State) -> None:
    text = snapraid.render(st)
    os.makedirs(os.path.dirname(snapraid.LOCAL_CONTENT), exist_ok=True)
    tmp = snapraid.CONFIG_PATH + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, snapraid.CONFIG_PATH)


def unmounted(st: state.State) -> list[str]:
    return [
        d.mountpoint
        for d in st.by_role("data") + st.by_role("parity")
        if not os.path.ismount(d.mountpoint)
    ]


def check_mounted(st: state.State) -> None:
    missing = unmounted(st)
    if missing:
        raise JobError(f"not mounted: {', '.join(missing)}")


def run_snapraid(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        [SNAPRAID, "-c", snapraid.CONFIG_PATH, *args],
        check=False,
        capture_output=capture,
        text=True,
    )


def decide(counts: dict[str, int], threshold: int, force: bool) -> str | None:
    """None if sync may run, otherwise the reason it must not."""
    changes = sum(v for k, v in counts.items() if k != "equal")
    if changes == 0:
        return "no changes"
    if counts.get("removed", 0) > threshold and not force:
        return (
            f"{counts['removed']} files removed, more than the threshold of {threshold}; "
            "check the disks, then run 'dahlia-snapraid sync --force'"
        )
    return None


# --- record of the last runs ---------------------------------------------------


def load_runs() -> dict:
    try:
        with open(RUNS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_runs(runs: dict) -> None:
    os.makedirs(os.path.dirname(RUNS_PATH), exist_ok=True)
    tmp = RUNS_PATH + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, RUNS_PATH)


def record(kind: str, entry: dict) -> None:
    """Keep the last run per kind and a history of the last HISTORY_MAX runs."""
    runs = load_runs()
    runs[kind] = entry
    history = runs.get("history") if isinstance(runs.get("history"), list) else []
    runs["history"] = [*history, {"kind": kind, **entry}][-HISTORY_MAX:]
    save_runs(runs)


# --- commands ------------------------------------------------------------------


def cmd_sync(st: state.State, force: bool) -> dict:
    diff = run_snapraid("diff", capture=True)
    sys.stdout.write(diff.stdout)
    # snapraid diff: 0 = no differences, 2 = sync needed, anything else = error.
    if diff.returncode not in (0, 2):
        sys.stderr.write(diff.stderr)
        raise JobError(f"snapraid diff failed with exit code {diff.returncode}")
    counts = snapraid.parse_diff(diff.stdout)
    reason = decide(counts, st.settings.snapraid_delete_threshold, force)
    if reason == "no changes":
        print("dahlia-snapraid: parity is up to date")
        return {"result": "no_changes", "counts": counts}
    if reason:
        raise JobError(reason, {"result": "blocked", "counts": counts,
                                "threshold": st.settings.snapraid_delete_threshold})
    res = run_snapraid("sync")
    if res.returncode != 0:
        raise JobError(f"snapraid sync failed with exit code {res.returncode}",
                       {"counts": counts})
    print("dahlia-snapraid: sync done")
    return {"result": "ok", "counts": counts, "forced": force}


def cmd_scrub(st: state.State) -> dict:
    if not os.path.exists(snapraid.LOCAL_CONTENT):
        print("dahlia-snapraid: no sync yet, nothing to scrub")
        return {"result": "no_changes"}
    res = run_snapraid(
        "scrub",
        "-p",
        str(st.settings.scrub_percent),
        "-o",
        str(st.settings.scrub_older_than_days),
    )
    if res.returncode != 0:
        # Exit code 1 also means "found errors"; the status tells which.
        bad = (read_status() or {}).get("bad", 0)
        if bad:
            raise JobError(f"scrub found {bad} bad blocks; repair them on the parity page "
                           "or with 'dahlia-snapraid fix --bad'", {"bad": bad})
        raise JobError(f"snapraid scrub failed with exit code {res.returncode}")
    print("dahlia-snapraid: scrub done")
    return {"result": "ok"}


def fix_counts(output: str) -> dict[str, int]:
    counts = {"recovered": 0, "unrecoverable": 0}
    for line in output.splitlines():
        m = FIX_COUNT.match(line)
        if m:
            counts[m.group(2)] = int(m.group(1))
    return counts


def run_echoed(*args: str) -> subprocess.CompletedProcess:
    res = run_snapraid(*args, capture=True)
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res


def cmd_fix(st: state.State, disk: int | None) -> dict:
    if not os.path.exists(snapraid.LOCAL_CONTENT):
        raise JobError("no sync yet, the parity holds nothing to restore")
    if disk is None:
        res = run_echoed("-e", "fix")
        counts = fix_counts(res.stdout)
        if res.returncode != 0:
            raise JobError(f"snapraid fix failed with exit code {res.returncode}", counts)
        res = run_snapraid("-p", "bad", "scrub")
        if res.returncode != 0:
            raise JobError(f"snapraid scrub failed with exit code {res.returncode}", counts)
        print("dahlia-snapraid: bad blocks repaired")
        return {"result": "ok", "what": "bad", **counts}
    if not any(d.index == disk for d in st.by_role("data")):
        raise JobError(f"no data disk {disk}")
    name = f"d{disk}"
    os.makedirs(os.path.dirname(FIX_LOG), exist_ok=True)
    res = run_echoed("-d", name, "-l", FIX_LOG, "fix")
    counts = fix_counts(res.stdout)
    if res.returncode != 0:
        raise JobError(f"snapraid fix failed with exit code {res.returncode}, see {FIX_LOG}",
                       {"what": name, **counts})
    res = run_echoed("-d", name, "-a", "check")
    if res.returncode != 0:
        raise JobError(f"snapraid check found differences on {name}", {"what": name, **counts})
    print(f"dahlia-snapraid: /mnt/disk{disk} restored and verified")
    return {"result": "ok", "what": name, **counts}


def read_status() -> dict | None:
    """snapraid status as a dict; None before the first sync."""
    if not os.path.exists(snapraid.LOCAL_CONTENT):
        return None
    res = run_snapraid("-l", ">&1", "status", capture=True)
    if res.returncode != 0:
        raise JobError(f"snapraid status failed with exit code {res.returncode}")
    return snapraid.parse_status(res.stdout)


def refresh_status(runs: dict) -> dict:
    """Run snapraid status when the content file changed since the last time."""
    try:
        changed = os.path.getmtime(snapraid.LOCAL_CONTENT)
    except OSError:
        changed = None
    cached = runs.get("status")
    if changed is None:
        runs.pop("status", None)
        return runs
    if isinstance(cached, dict) and cached.get("checked", 0) >= changed:
        return runs
    runs["status"] = {"checked": time.time(), **(read_status() or {})}
    save_runs(runs)
    return runs


def describe(st: state.State) -> dict:
    """Everything the dashboard shows on the parity page."""
    problems = snapraid.validate(st)

    def disk(d: state.Disk) -> dict:
        return {"index": d.index, "mountpoint": d.mountpoint, "uuid": d.uuid, "size": d.size,
                "mounted": os.path.ismount(d.mountpoint)}

    out = {
        "configured": not problems,
        "problems": problems,
        "parity": [disk(d) for d in st.by_role("parity")],
        "data": [disk(d) for d in st.by_role("data")],
        "settings": {
            "snapraid_delete_threshold": st.settings.snapraid_delete_threshold,
            "scrub_percent": st.settings.scrub_percent,
            "scrub_older_than_days": st.settings.scrub_older_than_days,
        },
        "busy": False,
        "error": "",
    }
    runs = load_runs()
    if not problems and not unmounted(st):
        with state.array_lock_if_free() as free:
            if free:
                try:
                    write_config(st)
                    runs = refresh_status(runs)
                except (JobError, snapraid.SnapraidError, OSError) as e:
                    out["error"] = str(e)
            else:
                out["busy"] = True
    out["status"] = runs.get("status")
    out["runs"] = {k: runs[k] for k in ("sync", "scrub", "fix") if k in runs}
    out["history"] = runs.get("history") if isinstance(runs.get("history"), list) else []
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dahlia-snapraid", description="Run SnapRAID safely.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="diff, check the delete threshold, sync")
    s.add_argument("--force", action="store_true", help="sync even above the delete threshold")
    sub.add_parser("scrub", help="verify part of the array against the parity")
    s = sub.add_parser("fix", help="restore a data disk or repair bad blocks from the parity")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--disk", type=int, metavar="N", help="rebuild the files of /mnt/diskN")
    g.add_argument("--bad", action="store_true", help="repair blocks a scrub marked as bad")
    s = sub.add_parser("status", help="parity state and the last runs")
    s.add_argument("--json", action="store_true", help="print JSON for the dashboard")
    return p


def print_status(info: dict) -> None:
    if info["problems"]:
        print("not set up: " + "; ".join(info["problems"]))
    for kind in ("sync", "scrub", "fix"):
        run = info["runs"].get(kind)
        if run:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(run.get("end", 0)))
            msg = f" ({run['message']})" if run.get("message") else ""
            print(f"last {kind}: {when} {run.get('result')}{msg}")
    s = info["status"]
    if s:
        print(f"files: {s['files']}, bad blocks: {s['bad']}, never scrubbed blocks: "
              f"{s['unscrubbed']}, interrupted sync blocks: {s['unsynced']}")
    elif info["configured"]:
        print("no sync yet")
    if info["busy"]:
        print("a job holds the array lock; status from the last check")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    st = state.load()
    if args.command == "status":
        info = describe(st)
        if args.json:
            print(json.dumps(info))
        else:
            print_status(info)
        return 0
    if not st.by_role("parity"):
        print("dahlia-snapraid: no parity disk configured, nothing to do")
        return 0
    entry = {"start": time.time()}
    try:
        with state.array_locked():
            check_mounted(st)
            write_config(st)
            if args.command == "sync":
                entry.update(cmd_sync(st, args.force))
            elif args.command == "scrub":
                entry.update(cmd_scrub(st))
            else:
                entry.update(cmd_fix(st, None if args.bad else args.disk))
        return 0
    except (JobError, snapraid.SnapraidError) as e:
        entry.setdefault("result", "failed")
        if isinstance(e, JobError) and len(e.args) > 1:
            entry.update({"result": "failed", **e.args[1]})
        entry["message"] = str(e.args[0])
        print(f"dahlia-snapraid: {e.args[0]}", file=sys.stderr)
        return 1
    finally:
        entry["end"] = time.time()
        record(args.command, entry)
