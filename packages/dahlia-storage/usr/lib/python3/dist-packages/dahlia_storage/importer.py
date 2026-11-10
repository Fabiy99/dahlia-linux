"""Copy the data of a disk into /mnt/user before it is formatted.

The source is mounted read-only, copied with rsync, then compared with
rsync --checksum. Only a successful import lets `dahlia-disk format` wipe a
disk that has a filesystem. Progress goes to /run/dahlia/import.json for the
dashboard; finished imports are kept in /var/lib/dahlia/imported.json.
"""

from __future__ import annotations

import contextlib
import grp
import json
import os
import re
import subprocess
import tempfile
import time

from . import disks

POOL = os.environ.get("DAHLIA_POOL", "/mnt/user")
STATUS_PATH = os.environ.get("DAHLIA_IMPORT_STATUS", "/run/dahlia/import.json")
IMPORTED_PATH = os.environ.get("DAHLIA_IMPORTED", "/var/lib/dahlia/imported.json")
MOUNT_BASE = os.environ.get("DAHLIA_IMPORT_MOUNT", "/run/dahlia/import")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Filesystems that can be copied: everything Dahlia can mount.
IMPORT_STATUSES = {"candidate", "external-only"}
# Read-only without replaying a journal: the source stays exactly as it is.
MOUNT_OPTIONS = {
    "ext4": ("ext4", "ro,noload"),
    "xfs": ("xfs", "ro,norecovery"),
    "btrfs": ("btrfs", "ro,rescue=nologreplay"),
    "ntfs": ("ntfs3", "ro"),
    "exfat": ("exfat", "ro"),
}
PROGRESS_RE = re.compile(r"^\s*([\d,.]+)\s+(\d+)%")
# Keep 2 % of the pool free on top of the data.
SPACE_MARGIN = 0.98


class ImportError_(RuntimeError):
    pass


def write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".import-")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_json(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def imported_uuids() -> set[str]:
    return set(read_json(IMPORTED_PATH))


def forget(uuids: set[str]) -> None:
    data = read_json(IMPORTED_PATH)
    if uuids & set(data):
        write_json(IMPORTED_PATH, {k: v for k, v in data.items() if k not in uuids})


def check_source(found: list[disks.Device], uuid: str) -> disks.Device:
    dev = next((d for d in found if d.uuid == uuid), None)
    if dev is None:
        raise ImportError_(f"no filesystem with UUID {uuid}")
    if dev.status not in IMPORT_STATUSES:
        raise ImportError_(f"{dev.path} cannot be imported: {dev.reason}")
    if dev.fstype not in MOUNT_OPTIONS:
        raise ImportError_(f"{dev.path}: {dev.fstype} cannot be copied")
    return dev


def check_target(name: str, pool: str = POOL) -> str:
    if not TARGET_RE.match(name):
        raise ImportError_("target: letters, digits, '.', '-' or '_', at most 64 characters")
    if not os.path.ismount(pool):
        raise ImportError_(f"{pool} is not mounted; add a data disk first")
    target = os.path.join(pool, name)
    if os.path.lexists(target):
        raise ImportError_(f"{target} already exists")
    return target


def enough_space(used: int, free: int) -> bool:
    return used <= free * SPACE_MARGIN


def parse_progress(line: str) -> tuple[int, int] | None:
    """rsync --info=progress2: '  1,234,567  42%  10.00MB/s  0:01:02' -> (bytes, percent)."""
    m = PROGRESS_RE.match(line)
    if not m:
        return None
    return int(re.sub(r"[,.]", "", m.group(1))), int(m.group(2))


def rsync_copy(source: str, target: str, group: str | None = "dahlia") -> list[str]:
    # Files land in group 'dahlia' like every share; owner and Windows ACLs are not copied.
    chown = [f"--chown=root:{group}"] if group else []
    return ["rsync", "-rltpH", *chown, "--chmod=D2775,Fug+rw,Fo+r,Fo-w",
            "--info=progress2", "--no-inc-recursive", f"{source}/", f"{target}/"]


def share_group() -> str | None:
    try:
        grp.getgrnam("dahlia")
    except KeyError:
        return None
    return "dahlia"


def rsync_verify(source: str, target: str) -> list[str]:
    return ["rsync", "-rlt", "--checksum", "--dry-run", "--itemize-changes",
            "--out-format=%i %n", f"{source}/", f"{target}/"]


def differences(itemized: str) -> list[str]:
    """Lines of rsync --itemize-changes that mean a file is missing or different.

    Directory time stamps ('.d..t......') are not a difference in content.
    """
    out = []
    for line in itemized.splitlines():
        code, _, name = line.partition(" ")
        if not code or (code.startswith(".d") and set(code[2:]) <= {".", "t", " "}):
            continue
        out.append(name or line)
    return out


def run_with_progress(cmd: list[str], on_progress) -> None:
    """Run rsync and report each progress line; rsync ends those with \\r, not \\n."""
    # stderr to a file: a full pipe would block rsync while stdout is read.
    with tempfile.TemporaryFile("w+") as errfile:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errfile)
        buf = b""
        while chunk := proc.stdout.read1(65536):
            buf += chunk
            *lines, buf = re.split(rb"[\r\n]", buf)
            for line in lines:
                got = parse_progress(line.decode(errors="replace"))
                if got:
                    on_progress(*got)
        code = proc.wait()
        errfile.seek(0)
        err = errfile.read().strip().splitlines()
    if code != 0:
        raise ImportError_(f"rsync failed: {err[-1] if err else code}")


class Status:
    def __init__(self, dev: disks.Device, target: str):
        self.data = {"uuid": dev.uuid, "device": dev.path, "disk": dev.disk, "fstype": dev.fstype,
                     "target": target, "phase": "mount", "percent": 0, "bytes": 0, "total": 0,
                     "error": "", "started": int(time.time()), "finished": 0}
        self.save()

    def set(self, **kw) -> None:
        self.data.update(kw)
        self.save()

    def save(self) -> None:
        write_json(STATUS_PATH, self.data)


def run_import(dev: disks.Device, target: str) -> dict:
    status = Status(dev, target)
    mnt = os.path.join(MOUNT_BASE, dev.uuid)
    mounted = False
    try:
        os.makedirs(mnt, exist_ok=True)
        fstype, options = MOUNT_OPTIONS[dev.fstype]
        subprocess.run(["mount", "-t", fstype, "-o", options, dev.path, mnt],
                       check=True, capture_output=True, text=True)
        mounted = True
        st = os.statvfs(mnt)
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        pool = os.statvfs(os.path.dirname(target))
        free = pool.f_bavail * pool.f_frsize
        if not enough_space(used, free):
            raise ImportError_(f"not enough space in {os.path.dirname(target)}: "
                               f"{used // 10**9} GB to copy, {free // 10**9} GB free")
        os.mkdir(target)
        with contextlib.suppress(KeyError):
            os.chown(target, 0, grp.getgrnam("dahlia").gr_gid)
        os.chmod(target, 0o2775)  # noqa: S103 (shares are group-writable)
        status.set(phase="copy", total=used)
        last = [0.0]

        def progress(done: int, pct: int) -> None:
            if time.monotonic() - last[0] > 1:
                status.set(bytes=done, percent=pct)
                last[0] = time.monotonic()

        run_with_progress(rsync_copy(mnt, target, share_group()), progress)
        status.set(phase="verify", percent=100)
        res = subprocess.run(rsync_verify(mnt, target), capture_output=True, text=True)
        diff = differences(res.stdout)
        if res.returncode != 0 or diff:
            raise ImportError_("verify failed: " + (", ".join(diff[:5]) or res.stderr.strip()))
        data = read_json(IMPORTED_PATH)
        data[dev.uuid] = {"device": dev.path, "serial": dev.serial, "target": target,
                          "time": int(time.time())}
        write_json(IMPORTED_PATH, data)
        status.set(phase="done", finished=int(time.time()))
        return status.data
    except Exception as e:  # noqa: BLE001 (any failure must end in phase 'failed')
        msg = str(e)
        if isinstance(e, subprocess.CalledProcessError):
            msg = (e.stderr or "").strip() or f"{e.cmd[0]} failed"
        status.set(phase="failed", error=msg, finished=int(time.time()))
        raise ImportError_(msg) from None
    finally:
        if mounted:
            subprocess.run(["umount", mnt], check=False)
        if os.path.isdir(mnt):
            with contextlib.suppress(OSError):
                os.rmdir(mnt)
