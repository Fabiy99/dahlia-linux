"""File manager: list, download, upload, rename, delete, copy and move.

Works below the same roots as the folder picker: /mnt/user (array), /srv
(system SSD) and /mnt/remotes (shares of other devices). Paths are walked one
directory at a time with O_NOFOLLOW, so a symlink inside a share cannot lead
the agent out of its root; symlinks are listed but not followed.

Rules for changes:
- nothing directly in a root: top-level folders are shares, app folders or
  remote mounts and are managed on their own pages (new folders there go
  through files.mkdir);
- nothing below appdata, that belongs to the apps.

Downloads and uploads do not go through the JSON socket: files.open and
files.upload hand an open file descriptor to the dashboard (SCM_RIGHTS), which
streams the data. Delete, copy and move can take long and run as the transient
unit dahlia-files.service (/usr/sbin/dahlia-files), whose progress files.job
reports.
"""

import errno
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
import unicodedata
from pathlib import Path

from .actions import (
    ARRAY_ROOT,
    ROOTS,
    Context,
    Param,
    action,
    is_active,
    location_root,
    locations,
    unit_state,
)
from .protocol import FAILED, INVALID_PARAMS, AgentError, PassFd

UNIT = "dahlia-files.service"
LIST_MAX = 5000
BATCH_MAX = 500
PATH_MAX = 4096
NAME_MAX = 255
# Any name a Linux filesystem takes, except control characters and "/".
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f/]+$")
REL_RE = re.compile(r"^$|^[^\x00-\x1f\x7f]{1,4096}$")
TMP_RE = re.compile(r"^\.dahlia-upload-[0-9a-f]{16}$")
PROTECTED_TOP = "appdata"
OPS = ("copy", "move", "delete")
PROGRESS_EVERY = 0.5
CHUNK = 1024 * 1024


# --- paths --------------------------------------------------------------------


def check_name(name: str) -> str:
    if not NAME_RE.fullmatch(name) or name in (".", "..") or len(name.encode()) > NAME_MAX:
        raise AgentError(INVALID_PARAMS, "invalid file name")
    return name


def split_rel(path: str) -> list[str]:
    """Components of a relative path; "" is the root itself."""
    if not path:
        return []
    if len(path.encode()) > PATH_MAX:
        raise AgentError(INVALID_PARAMS, "path too long")
    return [check_name(p) for p in path.split("/")]


def root_path(ctx: Context, root: str) -> Path:
    base, ok = location_root(ctx, root)
    if not ok:
        raise AgentError(FAILED, f"{root} is not available")
    return base


def open_dir(ctx: Context, root: str, parts: list[str]) -> int:
    """fd of root/parts, refusing symlinks on the way. The caller closes it."""
    fd = os.open(root_path(ctx, root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts:
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=fd)
            except FileNotFoundError:
                raise AgentError(FAILED, "folder does not exist") from None
            except (NotADirectoryError, OSError) as e:
                if e.errno in (errno.ENOTDIR, errno.ELOOP):
                    raise AgentError(FAILED, "not a folder") from None
                raise
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd


def check_writable(root: str, parts: list[str]) -> None:
    """parts is the entry that changes (or is created)."""
    if len(parts) < 2:
        raise AgentError(INVALID_PARAMS, "entries directly in a root cannot be changed here")
    if root != "/mnt/remotes" and parts[0] == PROTECTED_TOP:
        raise AgentError(INVALID_PARAMS, "files below appdata belong to apps")


def lexists(name: str, dir_fd: int) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def sort_key(name: str) -> str:
    """Case and accents ignored, so "Ärger" sorts next to "Arger"."""
    plain = unicodedata.normalize("NFKD", name.casefold())
    return "".join(c for c in plain if not unicodedata.combining(c))


def entry_kind(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


# --- reading ------------------------------------------------------------------


@action("files.list", mutating=False, params={
    "root": Param(str, required=False, default=ARRAY_ROOT, choices=ROOTS),
    "path": Param(str, required=False, default="", pattern=REL_RE)})
def files_list(ctx: Context, root: str, path: str):
    """Folders and files in <root>/<path>, folders first."""
    parts = split_rel(path)
    base, ok = location_root(ctx, root)
    out = {"root": root, "path": path, "locations": locations(ctx), "mounted": ok,
           "entries": [], "truncated": False, "skipped": 0}
    if not ok:
        return out
    fd = open_dir(ctx, root, parts)
    entries, skipped = [], 0
    try:
        with os.scandir(fd) as it:
            for e in it:
                try:
                    e.name.encode()
                except UnicodeEncodeError:
                    skipped += 1  # not UTF-8, cannot go into JSON
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    skipped += 1
                    continue
                kind = entry_kind(st.st_mode)
                entries.append({"name": e.name, "kind": kind,
                                "size": st.st_size if kind == "file" else None,
                                "mtime": int(st.st_mtime)})
    finally:
        os.close(fd)
    entries.sort(key=lambda x: (x["kind"] != "dir", sort_key(x["name"])))
    writable = len(parts) >= 1 and not (root != "/mnt/remotes" and parts[0] == PROTECTED_TOP)
    return {**out, "entries": entries[:LIST_MAX], "truncated": len(entries) > LIST_MAX,
            "skipped": skipped, "writable": writable}


@action("files.open", mutating=False, params={
    "root": Param(str, choices=ROOTS),
    "path": Param(str, pattern=REL_RE)})
def files_open(ctx: Context, root: str, path: str):
    """Open a file for download; the descriptor goes to the caller."""
    parts = split_rel(path)
    if not parts:
        raise AgentError(INVALID_PARAMS, "not a file")
    dfd = open_dir(ctx, root, parts[:-1])
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                         dir_fd=dfd)
        except FileNotFoundError:
            raise AgentError(FAILED, "file does not exist") from None
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise AgentError(FAILED, "not a file") from None
            raise
    finally:
        os.close(dfd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise AgentError(FAILED, "not a file")
    os.set_blocking(fd, True)
    return PassFd({"name": parts[-1], "size": st.st_size}, fd)


# --- small changes ------------------------------------------------------------


def own_like_parent(fd: int, dfd: int, mode: int) -> None:
    pst = os.fstat(dfd)
    os.fchown(fd, pst.st_uid, pst.st_gid)
    os.fchmod(fd, mode)


@action("files.create_dir", mutating=True, params={
    "root": Param(str, choices=ROOTS),
    "path": Param(str, pattern=REL_RE),
    "name": Param(str, pattern=NAME_RE)})
def files_create_dir(ctx: Context, root: str, path: str, name: str):
    """New folder in <root>/<path>, owner and group like its parent."""
    parts = split_rel(path)
    check_name(name)
    check_writable(root, [*parts, name])
    dfd = open_dir(ctx, root, parts)
    try:
        if lexists(name, dfd):
            raise AgentError(FAILED, f"{name} already exists")
        os.mkdir(name, 0o2775, dir_fd=dfd)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
        try:
            own_like_parent(fd, dfd, 0o2775)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)
    return {"root": root, "path": "/".join([*parts, name])}


@action("files.rename", mutating=True, params={
    "root": Param(str, choices=ROOTS),
    "path": Param(str, pattern=REL_RE),
    "name": Param(str, pattern=NAME_RE)})
def files_rename(ctx: Context, root: str, path: str, name: str):
    """Rename a file or folder in place."""
    parts = split_rel(path)
    check_name(name)
    check_writable(root, parts)
    check_writable(root, [*parts[:-1], name])
    dfd = open_dir(ctx, root, parts[:-1])
    try:
        if not lexists(parts[-1], dfd):
            raise AgentError(FAILED, "entry does not exist")
        if name != parts[-1] and lexists(name, dfd):
            raise AgentError(FAILED, f"{name} already exists")
        os.rename(parts[-1], name, src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        os.close(dfd)
    return {"root": root, "path": "/".join([*parts[:-1], name])}


# --- upload -------------------------------------------------------------------


@action("files.upload", mutating=True, params={
    "root": Param(str, choices=ROOTS),
    "path": Param(str, pattern=REL_RE),
    "name": Param(str, pattern=NAME_RE),
    "overwrite": Param(bool, required=False, default=False)})
def files_upload(ctx: Context, root: str, path: str, name: str, overwrite: bool):
    """Create a temporary file for an upload; the descriptor goes to the caller.

    files.upload_finish moves it to its name, files.upload_abort removes it.
    """
    parts = split_rel(path)
    check_name(name)
    check_writable(root, [*parts, name])
    dfd = open_dir(ctx, root, parts)
    try:
        existing = target_state(name, dfd)
        if existing == "dir" or (existing and not overwrite):
            raise AgentError(FAILED, f"{name} already exists")
        tmp = f".dahlia-upload-{secrets.token_hex(8)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dfd)
        try:
            own_like_parent(fd, dfd, 0o664)
        except BaseException:
            os.close(fd)
            os.unlink(tmp, dir_fd=dfd)
            raise
    finally:
        os.close(dfd)
    return PassFd({"tmp": tmp}, fd)


def target_state(name: str, dfd: int) -> str | None:
    try:
        st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return "dir" if stat.S_ISDIR(st.st_mode) else "other"


@action("files.upload_finish", mutating=True, params={
    "root": Param(str, choices=ROOTS),
    "path": Param(str, pattern=REL_RE),
    "name": Param(str, pattern=NAME_RE),
    "tmp": Param(str, pattern=TMP_RE),
    "overwrite": Param(bool, required=False, default=False)})
def files_upload_finish(ctx: Context, root: str, path: str, name: str, tmp: str, overwrite: bool):
    """Give an uploaded temporary file its name."""
    parts = split_rel(path)
    check_name(name)
    check_writable(root, [*parts, name])
    dfd = open_dir(ctx, root, parts)
    try:
        if target_state(tmp, dfd) != "other":
            raise AgentError(FAILED, "upload not found")
        existing = target_state(name, dfd)
        if existing == "dir" or (existing and not overwrite):
            os.unlink(tmp, dir_fd=dfd)
            raise AgentError(FAILED, f"{name} already exists")
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        os.close(dfd)
    return {"root": root, "path": "/".join([*parts, name])}


@action("files.upload_abort", mutating=True, params={
    "root": Param(str, choices=ROOTS),
    "path": Param(str, pattern=REL_RE),
    "tmp": Param(str, pattern=TMP_RE)})
def files_upload_abort(ctx: Context, root: str, path: str, tmp: str):
    """Remove the temporary file of a failed upload."""
    dfd = open_dir(ctx, root, split_rel(path))
    try:
        if target_state(tmp, dfd) == "other":
            os.unlink(tmp, dir_fd=dfd)
    finally:
        os.close(dfd)
    return {}


# --- delete, copy, move (background) ------------------------------------------


def spec_file(ctx: Context) -> Path:
    return ctx.run_dir / "files-job.json"


def progress_file(ctx: Context) -> Path:
    return ctx.run_dir / "files-progress.json"


def checked_entry(ctx: Context, root: str, path: str) -> tuple[list[str], Path]:
    """Existing entry below root, reached without symlinks; returns parts and path."""
    if not isinstance(path, str) or not REL_RE.fullmatch(path):
        raise AgentError(INVALID_PARAMS, "invalid path")
    parts = split_rel(path)
    if not parts:
        raise AgentError(INVALID_PARAMS, "invalid path")
    dfd = open_dir(ctx, root, parts[:-1])
    try:
        if not lexists(parts[-1], dfd):
            raise AgentError(FAILED, f"{path} does not exist")
    finally:
        os.close(dfd)
    return parts, root_path(ctx, root).joinpath(*parts)


def job_running(state: dict) -> bool:
    # RemainAfterExit: a finished job stays "active (exited)".
    return is_active(state) and state["sub"] != "exited"


@action("files.start", mutating=True, params={
    "op": Param(str, choices=OPS),
    "root": Param(str, choices=ROOTS),
    "paths": Param(list),
    "dest_root": Param(str, required=False, default=ARRAY_ROOT, choices=ROOTS),
    "dest": Param(str, required=False, default="", pattern=REL_RE)})
def files_start(ctx: Context, op: str, root: str, paths: list, dest_root: str, dest: str):
    """Delete, copy or move entries, in the background (dahlia-files.service)."""
    if not 1 <= len(paths) <= BATCH_MAX:
        raise AgentError(INVALID_PARAMS, f"'paths' must hold 1 to {BATCH_MAX} entries")
    if job_running(unit_state(ctx, UNIT)):
        raise AgentError(FAILED, "another file operation is running")
    sources = []
    for p in paths:
        parts, full = checked_entry(ctx, root, p)
        if op != "copy":
            check_writable(root, parts)
        sources.append((parts, full))
    target = None
    if op != "delete":
        dparts = split_rel(dest)
        check_writable(dest_root, [*dparts, "x"])
        dfd = open_dir(ctx, dest_root, dparts)
        try:
            for parts, _full in sources:
                if lexists(parts[-1], dfd):
                    raise AgentError(FAILED, f"{parts[-1]} already exists in the target folder")
        finally:
            os.close(dfd)
        target = root_path(ctx, dest_root).joinpath(*dparts)
        for _, full in sources:
            if target == full or full in target.parents:
                raise AgentError(INVALID_PARAMS, "a folder cannot go into itself")
    spec = {"op": op, "sources": [str(full) for _, full in sources],
            "dest": str(target) if target else None}
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    tmp = spec_file(ctx).with_suffix(".tmp")
    tmp.write_text(json.dumps(spec))
    tmp.chmod(0o600)
    tmp.replace(spec_file(ctx))
    progress_file(ctx).unlink(missing_ok=True)
    ctx.runner.run(["systemctl", "stop", UNIT], check=False)
    ctx.runner.run(["systemctl", "reset-failed", UNIT], check=False)
    ctx.runner.run(["systemd-run", "--unit", UNIT, "--no-block",
                    "--property", "Type=oneshot", "--property", "RemainAfterExit=yes",
                    "--description", "Dahlia file operation",
                    "/usr/sbin/dahlia-files", "run", str(spec_file(ctx))])
    return {"started": True, "op": op, "count": len(sources)}


@action("files.job", mutating=False)
def files_job(ctx: Context):
    """State and progress of the last delete, copy or move."""
    state = unit_state(ctx, UNIT)
    try:
        progress = json.loads(progress_file(ctx).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        progress = {}
    if job_running(state):
        result = "running"
    elif not progress and state["active"] in ("", "inactive"):
        result = "none"
    elif progress.get("error") or state["result"] not in ("", "success"):
        result = "failed"
    else:
        result = "done"
    return {"state": result, "op": progress.get("op"), "done": progress.get("done", 0),
            "total": progress.get("total", 0), "current": progress.get("current", ""),
            "error": progress.get("error", "")}


@action("files.cancel", mutating=True)
def files_cancel(ctx: Context):
    """Stop a running delete, copy or move. What is done stays done."""
    ctx.runner.run(["systemctl", "stop", UNIT], check=False)
    return {}


# --- the job itself (/usr/sbin/dahlia-files) ----------------------------------


class Progress:
    def __init__(self, path: Path, op: str, total: int, clock=time.monotonic):
        self.path, self.op, self.total, self.clock = path, op, total, clock
        self.done = 0
        self.current = ""
        self.last = 0.0

    def write(self, error: str = "", force: bool = False) -> None:
        now = self.clock()
        if not force and now - self.last < PROGRESS_EVERY:
            return
        self.last = now
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"op": self.op, "done": self.done, "total": self.total,
                                   "current": self.current, "error": error}))
        tmp.replace(self.path)

    def step(self, current: str, amount: int) -> None:
        self.current = current
        self.done += amount
        self.write()


def tree_size(path: Path, count_entries: bool) -> int:
    """Bytes of regular files (copy/move) or number of entries (delete)."""
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode):
        return 1 if count_entries else (st.st_size if stat.S_ISREG(st.st_mode) else 0)
    total = 1 if count_entries else 0
    for top, dirs, files in os.walk(path, followlinks=False):
        names = dirs + files
        if count_entries:
            total += len(names)
            continue
        for n in files:
            try:
                fst = os.lstat(os.path.join(top, n))
            except OSError:
                continue
            if stat.S_ISREG(fst.st_mode):
                total += fst.st_size
    return total


def copy_file(src: Path, dst: Path, progress: Progress) -> None:
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        while chunk := fin.read(CHUNK):
            fout.write(chunk)
            progress.step(str(src), len(chunk))
    shutil.copystat(src, dst, follow_symlinks=False)
    st = src.lstat()
    os.lchown(dst, st.st_uid, st.st_gid)


def copy_entry(src: Path, dst: Path, progress: Progress) -> None:
    """Copy like cp -a: owner, mode and times kept, symlinks copied as links."""
    st = src.lstat()
    if stat.S_ISLNK(st.st_mode):
        os.symlink(os.readlink(src), dst)
        os.lchown(dst, st.st_uid, st.st_gid)
    elif stat.S_ISDIR(st.st_mode):
        dst.mkdir()
        for child in sorted(os.listdir(src)):
            copy_entry(src / child, dst / child, progress)
        shutil.copystat(src, dst, follow_symlinks=False)
        os.lchown(dst, st.st_uid, st.st_gid)
    elif stat.S_ISREG(st.st_mode):
        copy_file(src, dst, progress)
    # sockets, fifos and device nodes are skipped


def remove_entry(path: Path, progress: Progress | None = None) -> None:
    """rm -r without following symlinks."""
    def count(p: str) -> None:
        if progress:
            progress.step(p, 1)

    if stat.S_ISDIR(path.lstat().st_mode):
        for top, dirs, files in os.walk(path, topdown=False, followlinks=False):
            for n in files:
                os.unlink(os.path.join(top, n))
                count(top)
            for n in dirs:
                p = os.path.join(top, n)
                if os.path.islink(p):
                    os.unlink(p)
                else:
                    os.rmdir(p)
                count(top)
        os.rmdir(path)
    else:
        path.unlink()
    count(str(path))


def run_job(spec: dict, progress_path: Path) -> None:
    op, sources = spec["op"], [Path(s) for s in spec["sources"]]
    dest = Path(spec["dest"]) if spec.get("dest") else None
    total = sum(tree_size(s, op == "delete") for s in sources)
    progress = Progress(progress_path, op, total)
    progress.write(force=True)
    try:
        for src in sources:
            if op == "delete":
                remove_entry(src, progress)
                continue
            target = dest / src.name
            if os.path.lexists(target):
                raise FileExistsError(f"{target} already exists")
            if op == "move":
                try:
                    os.rename(src, target)
                    progress.step(str(src), tree_size(target, False))
                    continue
                except OSError as e:
                    if e.errno != errno.EXDEV:
                        raise
            copy_entry(src, target, progress)
            if op == "move":
                remove_entry(src)
    except OSError as e:
        progress.write(error=str(e), force=True)
        raise
    progress.current = ""
    progress.write(force=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "run":
        print("usage: dahlia-files run <spec.json>", file=sys.stderr)
        return 2
    spec_path = Path(args[1])
    spec = json.loads(spec_path.read_text())
    try:
        run_job(spec, spec_path.with_name("files-progress.json"))
    except OSError as e:
        print(f"dahlia-files: {e}", file=sys.stderr)
        return 1
    return 0
