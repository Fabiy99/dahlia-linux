"""Persistent storage layout in /etc/dahlia/storage.json.

The file is the single source of truth. Mount units, mergerfs pools and the
SnapRAID config are generated from it and can be regenerated at any time
(`dahlia-disk apply`).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

STATE_PATH = os.environ.get("DAHLIA_STORAGE_STATE", "/etc/dahlia/storage.json")
LOCK_PATH = os.environ.get("DAHLIA_STORAGE_LOCK", "/run/dahlia/storage.lock")
# Held for the whole run of the mover and of SnapRAID: they never overlap.
ARRAY_LOCK_PATH = os.environ.get("DAHLIA_ARRAY_LOCK", "/run/dahlia/array.lock")

ROLES = ("data", "parity", "cache", "external")
CACHE_MOUNTPOINT = "/mnt/cache"
SIZE_RE = re.compile(r"^\d+[KMGT]?$")


@dataclass
class Disk:
    uuid: str
    role: str
    index: int  # data: /mnt/diskN, parity: /mnt/parityN, cache/external: unused
    fstype: str
    name: str = ""  # external: directory name under /mnt/external
    serial: str | None = None
    model: str | None = None
    size: int = 0

    @property
    def mountpoint(self) -> str:
        if self.role == "data":
            return f"/mnt/disk{self.index}"
        if self.role == "parity":
            return f"/mnt/parity{self.index}"
        if self.role == "cache":
            return CACHE_MOUNTPOINT
        return f"/mnt/external/{self.name}"


@dataclass
class Settings:
    # mergerfs stops placing new files on a branch below this free space.
    minfreespace: str = "50G"
    # The mover leaves files alone that were modified less than this ago.
    mover_min_age_minutes: int = 0
    # SnapRAID sync is aborted when diff reports more removed files.
    snapraid_delete_threshold: int = 50
    # Weekly scrub: percentage of the array, only blocks older than N days.
    scrub_percent: int = 8
    scrub_older_than_days: int = 10

    def validate(self) -> None:
        if not SIZE_RE.match(self.minfreespace):
            raise ValueError(f"invalid minfreespace: {self.minfreespace!r}")
        for name in ("mover_min_age_minutes", "snapraid_delete_threshold", "scrub_older_than_days"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"invalid {name}: {value!r}")
        if not isinstance(self.scrub_percent, int) or not 0 < self.scrub_percent <= 100:
            raise ValueError(f"invalid scrub_percent: {self.scrub_percent!r}")


@dataclass
class State:
    disks: list[Disk] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    def by_role(self, role: str) -> list[Disk]:
        return sorted((d for d in self.disks if d.role == role), key=lambda d: d.index)

    @property
    def cache(self) -> Disk | None:
        return next((d for d in self.disks if d.role == "cache"), None)

    def find(self, uuid: str) -> Disk | None:
        return next((d for d in self.disks if d.uuid == uuid), None)

    def next_index(self, role: str) -> int:
        used = {d.index for d in self.disks if d.role == role}
        index = 1
        while index in used:
            index += 1
        return index

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "disks": [asdict(d) for d in self.disks],
            "settings": asdict(self.settings),
        }

    @classmethod
    def from_dict(cls, data: dict) -> State:
        settings = Settings(**(data.get("settings") or {}))
        settings.validate()
        disks = [Disk(**d) for d in data.get("disks") or []]
        for d in disks:
            if d.role not in ROLES:
                raise ValueError(f"invalid role {d.role!r} for {d.uuid}")
        if sum(d.role == "cache" for d in disks) > 1:
            raise ValueError("more than one cache filesystem")
        return cls(disks=disks, settings=settings)


def load(path: str = STATE_PATH) -> State:
    try:
        with open(path, encoding="utf-8") as f:
            return State.from_dict(json.load(f))
    except FileNotFoundError:
        return State()


def save(state: State, path: str = STATE_PATH) -> None:
    # Write to a temp file in the same directory, then rename: never leaves
    # a half-written state file behind.
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".storage.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@contextmanager
def locked(path: str = LOCK_PATH):
    """Serialise changes to the storage layout (CLI, agent)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def array_locked():
    """Exclusive lock for long array jobs (mover, SnapRAID sync and scrub)."""
    return locked(ARRAY_LOCK_PATH)


@contextmanager
def array_lock_if_free():
    """Take the array lock without waiting; yields False if a job holds it."""
    os.makedirs(os.path.dirname(ARRAY_LOCK_PATH), exist_ok=True)
    with open(ARRAY_LOCK_PATH, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
