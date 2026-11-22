"""Backup of app data and configuration with restic.

What is saved: /etc/dahlia, the installed apps (/var/lib/dahlia/apps:
templates, state, secrets), the dashboard accounts (/var/lib/dashboard),
the appdata folder of every installed app and extra folders the admin picks.
Shares (films, photos) are not included unless picked as a folder.
VMs and system containers (Incus) the admin picks are exported with
`incus export --instance-only --compression none` into
/var/lib/dahlia/backup-machines and saved with the rest (uncompressed, so
restic can deduplicate between runs); the export is removed afterwards.
A restore imports the export again, as "<name>-restored" when an instance
of that name exists.

Target: a folder (another disk, a USB disk, a share of another server
mounted somewhere) or an SFTP server. The repository is encrypted with a
random password in /etc/dahlia/backup.pass; without it a backup cannot be
restored, so the dashboard shows it for safe keeping. For SFTP Dahlia makes
an SSH key (/etc/dahlia/backup_ed25519) whose public part goes into
~/.ssh/authorized_keys on the server.

Running apps are stopped for the backup (databases on disk are then
consistent) and started again afterwards. Old snapshots are thinned out:
7 daily, 4 weekly, 6 monthly.

    /etc/dahlia/backup.json                  settings
    /var/lib/dahlia/backup-status.json       result of the last run
    /var/lib/dahlia/backup-probe.json        result of the last probe
    /var/lib/dahlia/backup-runs.json         short log of the last runs (letters.py)
    backup.service / .timer                  the job and its schedule
    backup-probe.service / .timer            the probe, monthly by default

Probe: a backup is only worth something if it can be restored. Once a month
the probe reads a part of the repository's data (restic check
--read-data-subset), restores a random sample of files from the newest
snapshot into /var/lib/dahlia/backup-probe and lets restic verify their
content (restore --verify). A failed probe is sent as a notification.

CLI: backup run | probe | snapshots | restore SNAPSHOT (config|APP_ID|vm:NAME)
"""

import argparse
import contextlib
import datetime
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import apps

ETC = Path("/etc")
STATE = Path("/var/lib/dahlia")
DASHBOARD_STATE = Path("/var/lib/dashboard")
KEEP = {"daily": 7, "weekly": 4, "monthly": 6}
MAX_FOLDERS = 20
MAX_MACHINES = 20
MACHINE_RE = re.compile(r"^[a-z]([a-z0-9-]{0,30}[a-z0-9])?$")
EXPORT_TIMEOUT = 6 * 3600
# A folder target: an absolute path below /mnt or /media, no "..".
FOLDER_TARGET_RE = re.compile(r"^/(mnt|media)(/[A-Za-z0-9][A-Za-z0-9 ._()+,-]{0,99}){1,10}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,252}[A-Za-z0-9])?$")
SFTP_USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
SFTP_PATH_RE = re.compile(r"^[A-Za-z0-9/._~-]{1,200}$")
SNAPSHOT_RE = re.compile(r"^[0-9a-f]{8,64}$")
# Probe: files restored per run, their total size and the share of pack data read.
PROBE_FILES = 20
PROBE_BYTES = 512 * 1024**2
PROBE_READ = "5%"
RUNS_KEPT = 400


class BackupError(ValueError):
    pass


def _run(cmd: list[str], env: dict | None = None, timeout: int | None = None) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout,
                          env={**os.environ, **(env or {})})
    if proc.returncode != 0:
        lines = [ln for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
        raise BackupError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''}: "
                          f"{lines[-1] if lines else proc.returncode}")
    return proc.stdout


def check_target(target) -> dict:
    """The target from the dashboard, normalized; BackupError if invalid."""
    if not isinstance(target, dict) or target.get("type") not in ("folder", "sftp"):
        raise BackupError("target: type must be folder or sftp")
    if target["type"] == "folder":
        path = target.get("path")
        if not isinstance(path, str) or not FOLDER_TARGET_RE.fullmatch(path.rstrip("/")) \
                or ".." in path.split("/"):
            raise BackupError("target.path: a folder below /mnt or /media")
        return {"type": "folder", "path": path.rstrip("/")}
    host, user, path = target.get("host"), target.get("user"), target.get("path")
    port = target.get("port", 22)
    if not isinstance(host, str) or not HOST_RE.fullmatch(host):
        raise BackupError("target.host: a host name or IP address")
    if not isinstance(user, str) or not SFTP_USER_RE.fullmatch(user):
        raise BackupError("target.user: a user name on the SFTP server")
    if not isinstance(path, str) or not SFTP_PATH_RE.fullmatch(path) or ".." in path.split("/"):
        raise BackupError("target.path: a path on the SFTP server")
    if type(port) is not int or not 1 <= port <= 65535:
        raise BackupError("target.port: 1-65535")
    return {"type": "sftp", "host": host, "port": port, "user": user, "path": path}


def check_machines(machines) -> list[str]:
    """Names of Incus instances to include."""
    if not isinstance(machines, list) or len(machines) > MAX_MACHINES:
        raise BackupError(f"machines: at most {MAX_MACHINES}")
    if not all(isinstance(m, str) and MACHINE_RE.fullmatch(m) for m in machines):
        raise BackupError("machines: names of VMs or containers")
    return list(dict.fromkeys(machines))


def check_folders(folders, paths: apps.Paths) -> list[str]:
    """Extra folders: absolute paths below /mnt/user or /srv."""
    if not isinstance(folders, list) or len(folders) > MAX_FOLDERS:
        raise BackupError(f"folders: at most {MAX_FOLDERS}")
    out = []
    for ref in folders:
        try:
            root, rel = apps.split_folder(ref, paths)
        except apps.AppError as e:
            raise BackupError(str(e)) from None
        if f"{root}/{rel}" not in out:
            out.append(f"{root}/{rel}")
    return out


@dataclass
class Backup:
    etc: Path = ETC
    state: Path = STATE
    dashboard_state: Path = DASHBOARD_STATE
    paths: apps.Paths = field(default_factory=apps.Paths)
    run: Callable[..., str] = _run
    app_manager: object = None  # apps.Apps; tests pass a fake
    clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC)

    # --- files ----------------------------------------------------------------

    @property
    def config_file(self) -> Path:
        return self.etc / "dahlia/backup.json"

    @property
    def password_file(self) -> Path:
        return self.etc / "dahlia/backup.pass"

    @property
    def key_file(self) -> Path:
        return self.etc / "dahlia/backup_ed25519"

    @property
    def status_file(self) -> Path:
        return self.state / "backup-status.json"

    @property
    def probe_file(self) -> Path:
        return self.state / "backup-probe.json"

    @property
    def probe_dir(self) -> Path:
        return self.state / "backup-probe"

    @property
    def machines_dir(self) -> Path:
        return self.state / "backup-machines"

    def settings(self) -> dict:
        try:
            data = json.loads(self.config_file.read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        try:
            target = check_target(data.get("target"))
        except BackupError:
            target = None
        try:
            folders = check_folders(data.get("folders", []), self.paths)
        except BackupError:
            folders = []
        try:
            machines = check_machines(data.get("machines", []))
        except BackupError:
            machines = []
        return {"target": target, "stop_apps": data.get("stop_apps", True) is not False,
                "folders": folders, "machines": machines}

    def status(self) -> dict:
        try:
            data = json.loads(self.status_file.read_text())
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def probe_status(self) -> dict:
        try:
            data = json.loads(self.probe_file.read_text())
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def public_key(self) -> str | None:
        pub = self.key_file.with_name(self.key_file.name + ".pub")
        return pub.read_text().strip() if pub.is_file() else None

    def password(self) -> str:
        return self.password_file.read_text().strip()

    # --- settings -------------------------------------------------------------

    def configure(self, target, stop_apps: bool, folders, machines=()) -> dict:
        settings = {"target": check_target(target), "stop_apps": bool(stop_apps),
                    "folders": check_folders(folders, self.paths),
                    "machines": check_machines(list(machines))}
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.password_file.exists():
            fd = os.open(self.password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32) + "\n")
        if settings["target"]["type"] == "sftp" and not self.key_file.exists():
            self.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "backup",
                      "-f", str(self.key_file)])
        tmp = self.config_file.with_name("backup.json.tmp")
        tmp.write_text(json.dumps(settings, indent=2) + "\n")
        tmp.replace(self.config_file)
        return settings

    # --- restic ---------------------------------------------------------------

    def repository(self, target: dict) -> str:
        if target["type"] == "folder":
            return target["path"]
        return f"sftp:{target['user']}@{target['host']}:{target['path']}"

    def restic(self, *args: str, timeout: int | None = None) -> str:
        target = self.settings()["target"]
        if target is None:
            raise BackupError("no backup target set")
        cmd = ["restic", "--repo", self.repository(target),
               "--password-file", str(self.password_file)]
        if target["type"] == "sftp":
            ssh = (f"ssh -i {self.key_file} -p {target['port']} -o BatchMode=yes "
                   f"-o StrictHostKeyChecking=accept-new "
                   f"-o UserKnownHostsFile={self.etc}/dahlia/backup_known_hosts "
                   f"{target['user']}@{target['host']} -s sftp")
            cmd += ["-o", f"sftp.command={ssh}"]
        return self.run([*cmd, *args], env={"RESTIC_CACHE_DIR": str(self.state / "restic-cache")},
                        timeout=timeout)

    def ensure_repository(self) -> None:
        try:
            self.restic("cat", "config", timeout=120)
        except BackupError:
            target = self.settings()["target"]
            if target and target["type"] == "folder":
                Path(target["path"]).mkdir(parents=True, exist_ok=True)
            self.restic("init", timeout=120)

    def sources(self) -> list[str]:
        out = [str(self.etc / "dahlia"), str(self.paths.apps), str(self.dashboard_state)]
        for app_id in apps.installed(self.paths):
            try:
                t, state = apps.load_installed(self.paths, app_id)
            except (apps.AppError, OSError):
                continue
            location = state.get("location", str(self.paths.pool))
            d = self.paths.root(location) / "appdata" / app_id
            if d.is_dir():
                out.append(str(d))
        out += self.settings()["folders"]
        if any(self.machines_dir.glob("*.tar")):
            out.append(str(self.machines_dir))
        return [p for p in dict.fromkeys(out) if Path(p).exists()]

    # --- VMs and system containers (Incus) ---------------------------------------

    def machines(self) -> dict[str, str]:
        """Incus instances and their status; empty without Incus."""
        try:
            data = json.loads(self.run(["incus", "list", "--format", "json"], timeout=60) or "[]")
        except (BackupError, ValueError, OSError):
            return {}
        return {m["name"]: m.get("status", "") for m in data
                if isinstance(m, dict) and isinstance(m.get("name"), str)}

    def export_machines(self, names: list[str], stop: bool) -> list[str]:
        """Exports the picked instances into machines_dir; returns the exported names."""
        present = self.machines()
        shutil.rmtree(self.machines_dir, ignore_errors=True)
        self.machines_dir.mkdir(mode=0o700, parents=True)
        done = []
        for name in names:
            if name not in present:
                continue
            running = present[name] == "Running"
            if stop and running:
                self.run(["incus", "stop", name], timeout=600)
            try:
                self.run(["incus", "export", name, str(self.machines_dir / f"{name}.tar"),
                          "--instance-only", "--compression", "none"], timeout=EXPORT_TIMEOUT)
            finally:
                if stop and running:
                    self.run(["incus", "start", name], timeout=600)
            done.append(name)
        return done

    def _manager(self):
        if self.app_manager is None:
            self.app_manager = apps.Apps(paths=self.paths)
        return self.app_manager

    def _running_apps(self) -> list[str]:
        mgr = self._manager()
        running = []
        for app_id in apps.installed(self.paths):
            with contextlib.suppress(apps.AppError, OSError):
                t, _ = apps.load_installed(self.paths, app_id)
                if mgr.status(t) in ("running", "partial"):
                    running.append(app_id)
        return running

    def _compose(self, app_id: str, *args: str) -> None:
        self._manager().docker.compose(self.paths.app_dir(app_id), *args)

    def _write_status(self, status: dict) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        tmp = self.status_file.with_name("backup-status.json.tmp")
        tmp.write_text(json.dumps(status) + "\n")
        tmp.replace(self.status_file)

    def _log_run(self, status: dict) -> None:
        path = self.state / "backup-runs.json"
        try:
            runs = json.loads(path.read_text()).get("runs", [])
        except (OSError, ValueError, AttributeError):
            runs = []
        runs.append({"time": status.get("finished"), "result": status.get("result"),
                     "added": status.get("added")})
        tmp = path.with_name("backup-runs.json.tmp")
        tmp.write_text(json.dumps({"runs": runs[-RUNS_KEPT:]}) + "\n")
        tmp.replace(path)

    def backup(self) -> dict:
        """One run: stop apps, restic backup, start apps, thin out old snapshots."""
        settings = self.settings()
        if settings["target"] is None:
            raise BackupError("no backup target set")
        started = self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        stopped: list[str] = []
        status = {"started": started, "result": "failed"}
        try:
            self.ensure_repository()
            if settings["stop_apps"]:
                stopped = self._running_apps()
                for app_id in stopped:
                    self._compose(app_id, "stop")
            if settings["machines"]:
                status["machines"] = self.export_machines(settings["machines"],
                                                          settings["stop_apps"])
            out = self.restic("backup", "--json", "--tag", "dahlia", *self.sources())
            summary = next((json.loads(ln) for ln in out.splitlines()
                            if '"message_type":"summary"' in ln.replace(" ", "")), {})
            status.update(result="success", snapshot=summary.get("snapshot_id"),
                          added=summary.get("data_added"),
                          files=summary.get("total_files_processed"))
        except (BackupError, apps.AppError, subprocess.TimeoutExpired) as e:
            status["error"] = str(e)
            raise BackupError(str(e)) from None
        finally:
            shutil.rmtree(self.machines_dir, ignore_errors=True)
            errors = []
            for app_id in stopped:
                try:
                    self._compose(app_id, "up", "-d")
                except apps.AppError as e:
                    errors.append(f"{app_id}: {e}")
            if errors:
                status["error"] = "; ".join([status.get("error", ""), *errors]).strip("; ")
            status["finished"] = self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")
            status["apps_stopped"] = stopped
            self._write_status(status)
            self._log_run(status)
        keep = [a for k, v in KEEP.items() for a in (f"--keep-{k}", str(v))]
        try:
            self.restic("forget", "--tag", "dahlia", *keep, "--prune")
        except BackupError as e:
            status["warning"] = f"thinning out old snapshots failed: {e}"
            self._write_status(status)
        return status

    # --- probe ----------------------------------------------------------------

    def sample(self, snapshot: str, pick: random.Random) -> list[dict]:
        """Random files of a snapshot, at most PROBE_FILES and PROBE_BYTES."""
        files = []
        for line in self.restic("ls", "--json", snapshot, timeout=1800).splitlines():
            try:
                node = json.loads(line)
            except ValueError:
                continue
            if node.get("struct_type") == "node" and node.get("type") == "file" \
                    and isinstance(node.get("path"), str) and type(node.get("size")) is int:
                files.append({"path": node["path"], "size": node["size"]})
        pick.shuffle(files)
        out, total = [], 0
        for f in files:
            if len(out) == PROBE_FILES:
                break
            if total + f["size"] <= PROBE_BYTES:
                out.append(f)
                total += f["size"]
        return out

    def probe(self, pick: random.Random | None = None) -> dict:
        """Check the repository and restore a sample of the newest snapshot."""
        pick = pick or random.SystemRandom()
        status = {"started": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"), "result": "failed"}
        try:
            data = json.loads(self.restic("snapshots", "--json", "--tag", "dahlia", "--latest",
                                          "1", timeout=300) or "[]")
            if not data:
                raise BackupError("the repository has no snapshot yet")
            newest = data[-1]
            snapshot = newest.get("short_id") or newest.get("id", "")[:8]
            status.update(snapshot=snapshot, snapshot_time=newest.get("time"))
            self.restic("check", f"--read-data-subset={PROBE_READ}", timeout=6 * 3600)
            files = self.sample(snapshot, pick)
            shutil.rmtree(self.probe_dir, ignore_errors=True)
            self.probe_dir.mkdir(mode=0o700, parents=True)
            if files:
                self.restic("restore", snapshot, "--target", str(self.probe_dir), "--verify",
                            *[a for f in files for a in ("--include", f["path"])],
                            timeout=3 * 3600)
            missing = []
            for f in files:
                got = self.probe_dir / f["path"].lstrip("/")
                if not got.is_file() or got.stat().st_size != f["size"]:
                    missing.append(f["path"])
            if missing:
                raise BackupError(f"{len(missing)} of {len(files)} files came back wrong, "
                                  f"for example {missing[0]}")
            status.update(result="success", files=len(files),
                          bytes=sum(f["size"] for f in files), read=PROBE_READ)
        except (BackupError, ValueError, subprocess.TimeoutExpired) as e:
            status["error"] = str(e)
        finally:
            shutil.rmtree(self.probe_dir, ignore_errors=True)
            status["finished"] = self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")
            self.state.mkdir(parents=True, exist_ok=True)
            tmp = self.probe_file.with_name("backup-probe.json.tmp")
            tmp.write_text(json.dumps(status) + "\n")
            tmp.replace(self.probe_file)
        return status

    def snapshots(self) -> list[dict]:
        data = json.loads(self.restic("snapshots", "--json", "--tag", "dahlia", timeout=300)
                          or "[]")
        return [{"id": s.get("short_id") or s.get("id", "")[:8], "time": s.get("time"),
                 "paths": s.get("paths", [])} for s in data if isinstance(s, dict)]

    def restore(self, snapshot: str, what: str) -> dict:
        """Put back the app data of one app ("config": /etc/dahlia and the app list)."""
        if not SNAPSHOT_RE.fullmatch(snapshot):
            raise BackupError("invalid snapshot id")
        if what == "config":
            includes = [str(self.etc / "dahlia"), str(self.paths.apps)]
            self.restic("restore", snapshot, "--target", "/",
                        *[a for p in includes for a in ("--include", p)])
            return {"restored": "config", "snapshot": snapshot}
        if what.startswith("vm:"):
            return self.restore_machine(snapshot, what[3:])
        if what not in apps.installed(self.paths):
            raise BackupError(f"{what} is not installed")
        t, state = apps.load_installed(self.paths, what)
        location = state.get("location", str(self.paths.pool))
        data = self.paths.root(location) / "appdata" / what
        was_running = self._manager().status(t) in ("running", "partial")
        self._compose(what, "stop")
        try:
            self.restic("restore", snapshot, "--target", "/", "--include", str(data))
        finally:
            if was_running:
                self._compose(what, "up", "-d")
        return {"restored": what, "snapshot": snapshot}


    def restore_machine(self, snapshot: str, name: str) -> dict:
        """Import a VM or container from its export; beside an existing one of that name."""
        if not MACHINE_RE.fullmatch(name):
            raise BackupError("invalid instance name")
        export = self.machines_dir / f"{name}.tar"
        shutil.rmtree(self.machines_dir, ignore_errors=True)
        try:
            self.restic("restore", snapshot, "--target", "/", "--include", str(export),
                        timeout=EXPORT_TIMEOUT)
            if not export.is_file():
                raise BackupError(f"the snapshot holds no export of {name}")
            new = name if name not in self.machines() else f"{name[:24]}-restored"
            if new in self.machines():
                raise BackupError(f"{new} exists already; delete or rename it first")
            self.run(["incus", "import", str(export), new], timeout=EXPORT_TIMEOUT)
        finally:
            shutil.rmtree(self.machines_dir, ignore_errors=True)
        return {"restored": f"vm:{name}", "as": new, "snapshot": snapshot}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="backup", description="Dahlia backup (restic).")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="back up now (backup.service)")
    sub.add_parser("probe", help="check the repository and restore a sample (backup-probe.service)")
    sub.add_parser("snapshots", help="list snapshots as JSON")
    r = sub.add_parser("restore", help="restore app data or the configuration")
    r.add_argument("snapshot")
    r.add_argument("what", help="config or an app id")
    args = p.parse_args(argv)
    b = Backup()
    try:
        if args.command == "run":
            status = b.backup()
            print(f"backup: snapshot {status.get('snapshot')}")
        elif args.command == "probe":
            return probe_main(b)
        elif args.command == "snapshots":
            print(json.dumps(b.snapshots(), indent=2))
        else:
            print(json.dumps(b.restore(args.snapshot, args.what)))
    except BackupError as e:
        print(f"backup: {e}", file=sys.stderr)
        return 1
    return 0


def probe_main(b: Backup) -> int:
    """Run the probe and report a failure, and the first success after one."""
    from . import notify

    before = b.probe_status().get("result")
    status = b.probe()
    ctx = Context()
    if status["result"] != "success":
        print(f"backup: probe failed: {status.get('error')}", file=sys.stderr)
        notify.send(ctx, "warning", "Backup probe failed",
                    f"Restoring a sample of the newest backup failed: {status.get('error')}",
                    "jobs")
        return 1
    print(f"backup: probe restored {status['files']} files of snapshot {status['snapshot']}")
    if before == "failed":
        notify.send(ctx, "info", "Backup probe works again",
                    f"{status['files']} files of snapshot {status['snapshot']} were restored "
                    "and verified.", "jobs")
    return 0


# --- agent actions --------------------------------------------------------------

from .actions import Context, Param, action, unit_state  # noqa: E402
from .protocol import FAILED, INVALID_PARAMS, AgentError  # noqa: E402

RESTORE_UNIT = "backup-restore.service"
WHAT_RE = re.compile(r"^(config|[a-z][a-z0-9-]{1,30}|vm:[a-z]([a-z0-9-]{0,30}[a-z0-9])?)$")


def backup_of(ctx: Context) -> Backup:
    def run(cmd: list[str], env: dict | None = None, timeout: int | None = None) -> str:
        try:
            return ctx.runner.run(cmd, timeout=timeout or 600,
                                  env={**os.environ, **env} if env else None).stdout
        except AgentError as e:
            raise BackupError(e.message) from None

    return Backup(etc=ctx.etc, state=ctx.var, paths=apps.Paths(apps=ctx.var / "apps",
                  pool=ctx.pool, system=ctx.system, remotes=ctx.remotes), run=run)


BACKUP_DIR = "dahlia-backup"
CANDIDATE_RE = re.compile(r"^/(mnt/(disk[0-9]+|cache|user)|media/[^/]+(/[^/]+)?)$")


def target_candidates(ctx: Context) -> list[dict]:
    """Mounted disks a folder backup can go to; the target is <mount>/dahlia-backup."""
    out = ctx.runner.run(["findmnt", "-J", "-l", "-b", "-o", "TARGET,FSTYPE,SIZE,AVAIL"],
                         check=False).stdout
    try:
        rows = json.loads(out or "{}").get("filesystems", [])
    except ValueError:
        return []
    return [{"mount": r["target"], "path": f"{r['target']}/{BACKUP_DIR}", "fstype": r.get("fstype"),
             "size": r.get("size"), "avail": r.get("avail")}
            for r in rows if isinstance(r, dict) and CANDIDATE_RE.fullmatch(r.get("target", ""))]


@action("backup.get", mutating=False)
def backup_get(ctx: Context):
    """Backup settings, last result, SSH public key, folder targets, backup and restore state."""
    b = backup_of(ctx)
    return {
        **b.settings(),
        "candidates": target_candidates(ctx),
        "last": b.status(),
        "probe": b.probe_status(),
        "probe_job": unit_state(ctx, "backup-probe.service"),
        "public_key": b.public_key(),
        "job": unit_state(ctx, "backup.service"),
        "restore": unit_state(ctx, RESTORE_UNIT),
        # VMs and containers that can be included (Incus; none without it).
        "instances": sorted(b.machines()) if ctx.runner.run(
            ["test", "-x", "/usr/bin/incus"], check=False).returncode == 0 else [],
    }


@action(
    "backup.set",
    mutating=True,
    params={"target": Param(dict), "stop_apps": Param(bool, required=False, default=True),
            "folders": Param(list, required=False, default=[]),
            "machines": Param(list, required=False, default=[])},
)
def backup_set(ctx: Context, target: dict, stop_apps: bool, folders: list, machines: list):
    """Set the target (folder or SFTP), extra folders and VMs; switches the daily timer on."""
    from . import settings as schedules

    b = backup_of(ctx)
    try:
        result = b.configure(target, stop_apps, folders, machines)
    except BackupError as e:
        raise AgentError(INVALID_PARAMS, str(e)) from None
    saved = schedules.saved_schedules(ctx)
    for job in ("backup", "backup-probe"):
        if saved[job]["frequency"] != "off":
            schedules.settings_set_schedule(ctx, job, **saved[job])
    return {**result, "public_key": b.public_key()}


@action("backup.key", mutating=False)
def backup_key(ctx: Context):
    """The repository password: needed to restore on another machine."""
    try:
        return {"password": backup_of(ctx).password()}
    except OSError:
        raise AgentError(FAILED, "no backup configured yet") from None


@action("backup.snapshots", mutating=False)
def backup_snapshots(ctx: Context):
    """Snapshots in the repository, newest last."""
    try:
        return {"snapshots": backup_of(ctx).snapshots()}
    except (BackupError, ValueError) as e:
        raise AgentError(FAILED, str(e)) from None


@action(
    "backup.restore",
    mutating=True,
    params={"snapshot": Param(str, pattern=SNAPSHOT_RE), "what": Param(str, pattern=WHAT_RE)},
)
def backup_restore(ctx: Context, snapshot: str, what: str):
    """Restore one app's data or the configuration, in the background."""
    if what != "config" and not what.startswith("vm:") \
            and what not in apps.installed(apps.Paths(apps=ctx.var / "apps")):
        raise AgentError(INVALID_PARAMS, f"{what} is not installed")
    state = unit_state(ctx, RESTORE_UNIT)
    if state["active"] in ("active", "activating"):
        raise AgentError(FAILED, "a restore is already running")
    # The unit stays after it finished (RemainAfterExit) so its result can be read.
    ctx.runner.run(["systemctl", "stop", RESTORE_UNIT], check=False)
    ctx.runner.run(["systemctl", "reset-failed", RESTORE_UNIT], check=False)
    ctx.runner.run(["systemd-run", "--unit", RESTORE_UNIT, "--no-block",
                    "--property", "Type=oneshot", "--property", "RemainAfterExit=yes",
                    "--description", "Dahlia backup restore",
                    "/usr/sbin/backup", "restore", snapshot, what])
    return {"started": True, "snapshot": snapshot, "what": what}
