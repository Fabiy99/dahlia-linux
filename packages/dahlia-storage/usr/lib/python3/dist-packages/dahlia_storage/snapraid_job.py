"""dahlia-snapraid: run SnapRAID for the Dahlia array.

    dahlia-snapraid sync [--force]
    dahlia-snapraid scrub

sync writes /etc/snapraid.conf from storage.json, runs `snapraid diff` and
aborts if more files were removed than the configured threshold (a disk
that failed to mount or an accidental delete would otherwise be synced
into the parity). Then it runs `snapraid sync`.

Both commands take the array lock, so they never run at the same time as
the mover or each other. Without parity disks they do nothing.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from . import snapraid, state

SNAPRAID = os.environ.get("DAHLIA_SNAPRAID", "snapraid")


class JobError(RuntimeError):
    pass


def write_config(st: state.State) -> None:
    text = snapraid.render(st)
    os.makedirs(os.path.dirname(snapraid.LOCAL_CONTENT), exist_ok=True)
    tmp = snapraid.CONFIG_PATH + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, snapraid.CONFIG_PATH)


def check_mounted(st: state.State) -> None:
    missing = [
        d.mountpoint
        for d in st.by_role("data") + st.by_role("parity")
        if not os.path.ismount(d.mountpoint)
    ]
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


def cmd_sync(st: state.State, force: bool) -> int:
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
        return 0
    if reason:
        raise JobError(reason)
    res = run_snapraid("sync")
    if res.returncode != 0:
        raise JobError(f"snapraid sync failed with exit code {res.returncode}")
    print("dahlia-snapraid: sync done")
    return 0


def cmd_scrub(st: state.State) -> int:
    if not os.path.exists(snapraid.LOCAL_CONTENT):
        print("dahlia-snapraid: no sync yet, nothing to scrub")
        return 0
    res = run_snapraid(
        "scrub",
        "-p",
        str(st.settings.scrub_percent),
        "-o",
        str(st.settings.scrub_older_than_days),
    )
    if res.returncode != 0:
        raise JobError(f"snapraid scrub failed with exit code {res.returncode}")
    print("dahlia-snapraid: scrub done")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dahlia-snapraid", description="Run SnapRAID safely.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="diff, check the delete threshold, sync")
    s.add_argument("--force", action="store_true", help="sync even above the delete threshold")
    sub.add_parser("scrub", help="verify part of the array against the parity")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    st = state.load()
    if not st.by_role("parity"):
        print("dahlia-snapraid: no parity disk configured, nothing to do")
        return 0
    try:
        with state.array_locked():
            check_mounted(st)
            write_config(st)
            if args.command == "sync":
                return cmd_sync(st, args.force)
            return cmd_scrub(st)
    except (JobError, snapraid.SnapraidError) as e:
        print(f"dahlia-snapraid: {e}", file=sys.stderr)
        return 1
