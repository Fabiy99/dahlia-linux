"""dahlia-mover: move files from the cache to the data disks.

    dahlia-mover [--dry-run] [--min-age MINUTES]

New files land on /mnt/cache/shares (first branch of /mnt/user). The mover
copies them to /mnt/storage, where mergerfs picks a data disk, and deletes
the cache copy afterwards. The path under /mnt/user does not change.

Skipped and left on the cache: files open by any process, files modified
less than --min-age minutes ago, files changed during the copy, hard links,
special files, and files that already exist on the data disks.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field

from . import state, units

TMP_SUFFIX = ".dahlia-mover.tmp"


@dataclass
class Result:
    moved: int = 0
    moved_bytes: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def open_files(prefix: str, proc: str = "/proc") -> set[str]:
    """Paths under `prefix` that any process holds open.

    Files opened through /mnt/user show up here too: mergerfs itself holds
    the underlying file on the cache open while a client uses it.
    """
    prefix = prefix.rstrip("/") + "/"
    found = set()
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except FileNotFoundError:
        return found
    for pid in pids:
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith(prefix):
                found.add(target)
    return found


def candidates(cache_root: str):
    """Yield paths relative to cache_root, files before their directories."""
    for root, dirs, files in os.walk(cache_root):
        dirs.sort()
        for name in sorted(files):
            yield os.path.relpath(os.path.join(root, name), cache_root)


def skip_reason(path: str, st: os.stat_result, now: float, min_age: int, busy: set[str]):
    if path.endswith(TMP_SUFFIX):
        return "mover temp file"
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
        return "special file"
    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        return "hard link"
    if path in busy:
        return "open"
    if now - st.st_mtime < min_age * 60:
        return "modified recently"
    return None


def ensure_parents(rel: str, cache_root: str, dest_root: str) -> None:
    """Create missing directories on the destination with cache metadata."""
    parts = os.path.dirname(rel).split(os.sep) if os.path.dirname(rel) else []
    src = cache_root
    dst = dest_root
    for part in parts:
        src = os.path.join(src, part)
        dst = os.path.join(dst, part)
        if os.path.isdir(dst):
            continue
        st = os.stat(src)
        os.mkdir(dst)
        os.chown(dst, st.st_uid, st.st_gid)
        os.chmod(dst, stat.S_IMODE(st.st_mode))


class ChangedError(RuntimeError):
    pass


def move_one(rel: str, cache_root: str, dest_root: str, before: os.stat_result) -> int:
    """Copy one file to dest_root, then remove it from the cache. Returns bytes."""
    src = os.path.join(cache_root, rel)
    dst = os.path.join(dest_root, rel)
    tmp = os.path.join(os.path.dirname(dst), "." + os.path.basename(dst) + TMP_SUFFIX)
    ensure_parents(rel, cache_root, dest_root)
    try:
        if stat.S_ISLNK(before.st_mode):
            os.symlink(os.readlink(src), tmp)
            os.lchown(tmp, before.st_uid, before.st_gid)
        else:
            shutil.copy2(src, tmp, follow_symlinks=False)
            os.chown(tmp, before.st_uid, before.st_gid)
            os.chmod(tmp, stat.S_IMODE(before.st_mode))
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
        after = os.lstat(src)
        if (after.st_mtime_ns, after.st_size, after.st_ino) != (
            before.st_mtime_ns,
            before.st_size,
            before.st_ino,
        ):
            raise ChangedError("changed during copy")
        if os.path.lexists(dst):
            raise ChangedError("appeared on the data disks during copy")
        os.rename(tmp, dst)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    os.unlink(src)
    return before.st_size if stat.S_ISREG(before.st_mode) else 0


def prune_dirs(cache_root: str) -> None:
    """Remove emptied directories, but keep the share directories themselves."""
    for root, _dirs, _files in os.walk(cache_root, topdown=False):
        if os.path.dirname(root.rstrip("/")) == cache_root.rstrip("/") or root == cache_root:
            continue
        # Not empty (a skipped file stays) or in use: leave it.
        with contextlib.suppress(OSError):
            os.rmdir(root)


def run(
    cache_root: str,
    dest_root: str,
    min_age: int = 0,
    dry_run: bool = False,
    busy: set[str] | None = None,
    now: float | None = None,
    log=print,
) -> Result:
    result = Result()
    now = time.time() if now is None else now
    busy = open_files(cache_root) if busy is None else busy
    for rel in candidates(cache_root):
        src = os.path.join(cache_root, rel)
        try:
            st = os.lstat(src)
        except FileNotFoundError:
            continue
        reason = skip_reason(src, st, now, min_age, busy)
        if reason is None and os.path.lexists(os.path.join(dest_root, rel)):
            reason = "already exists on the data disks"
        if reason:
            result.skipped.append((rel, reason))
            log(f"skip {rel}: {reason}")
            continue
        if dry_run:
            log(f"would move {rel}")
            result.moved += 1
            result.moved_bytes += st.st_size
            continue
        try:
            result.moved_bytes += move_one(rel, cache_root, dest_root, st)
            result.moved += 1
        except ChangedError as e:
            result.skipped.append((rel, str(e)))
            log(f"skip {rel}: {e}")
        except OSError as e:
            result.errors.append((rel, e.strerror or str(e)))
            log(f"error {rel}: {e}")
    if not dry_run:
        prune_dirs(cache_root)
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dahlia-mover", description="Move files from cache to disks.")
    p.add_argument("--dry-run", action="store_true", help="only list what would be moved")
    p.add_argument("--min-age", type=int, metavar="MINUTES", help="override the configured age")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    st = state.load()
    if st.cache is None:
        print("dahlia-mover: no cache configured, nothing to do")
        return 0
    if not st.by_role("data"):
        print("dahlia-mover: no data disks, nothing to move to")
        return 0
    if not os.path.ismount(st.cache.mountpoint):
        print(f"dahlia-mover: {st.cache.mountpoint} is not mounted", file=sys.stderr)
        return 1
    if not os.path.ismount(units.STORAGE_POOL):
        print(f"dahlia-mover: {units.STORAGE_POOL} is not mounted", file=sys.stderr)
        return 1
    if not os.path.isdir(units.CACHE_SHARES):
        print(f"dahlia-mover: {units.CACHE_SHARES} missing, nothing to move")
        return 0
    min_age = st.settings.mover_min_age_minutes if args.min_age is None else args.min_age
    if min_age < 0:
        print("dahlia-mover: --min-age must not be negative", file=sys.stderr)
        return 2

    with state.array_locked():
        started = time.monotonic()
        res = run(units.CACHE_SHARES, units.STORAGE_POOL, min_age=min_age, dry_run=args.dry_run)
    verb = "would move" if args.dry_run else "moved"
    print(
        f"dahlia-mover: {verb} {res.moved} file(s), {res.moved_bytes / 1e9:.2f} GB, "
        f"skipped {len(res.skipped)}, errors {len(res.errors)}, "
        f"{time.monotonic() - started:.0f}s"
    )
    return 1 if res.errors else 0
