"""Diagnostics bundle for bug reports.

system.diagnostics collects versions, unit states, journals, storage,
network, Docker and app state into one tar.gz and puts it into the
dashboard's runtime directory (/run/dashboard/diagnostics.tar.gz,
owned by the dashboard user), from where the dashboard hands it out once.

Secrets stay out: no compose files or secrets.json of apps, no
/etc/dahlia/backup.pass, no SSH or TLS keys; JSON settings are copied with
every key that looks like a password, token or key replaced by "***".
"""

import io
import json
import os
import re
import tarfile
import time
from pathlib import Path

from .actions import Context, action

BUNDLE = "diagnostics.tar.gz"
SECRET_KEY_RE = re.compile(r"pass|token|secret|key|private", re.I)
# Settings files under /etc/dahlia worth reading; anything else is skipped.
ETC_FILES = ("release", "storage.json", "apps.json", "spindown.json", "updates.json",
             "notify.json", "ups.json", "backup.json", "domains.json", "firewall.json",
             "schedules.json")
# Other files, relative to the context's /etc, /var/lib/dahlia and /proc.
ETC_OTHER = ("os-release", "snapraid.conf", "network/interfaces", "resolv.conf", "hosts",
             "samba/smb.conf", "exports.d/dahlia.exports")
VAR_FILES = ("snapraid.json",)
PROC_FILES = ("mdstat", "cmdline")
# No shared prefix any more (only per-app containers keep "dahlia-app-"), so
# list the units our own packages ship instead of a single glob.
OUR_PACKAGES = ("agent", "dashboard", "storage", "base", "archive-keyring")
OUR_UNITS = (
    "agent.service", "agent.socket", "dashboard.service", "terminal.service",
    "mover.service", "mover.timer", "snapraid-sync.service", "snapraid-sync.timer",
    "snapraid-scrub.service", "snapraid-scrub.timer", "storage.target",
    "update.service", "backup.service", "backup.timer", "backup-restore.service",
    "smart-test@*.service", "smart-short.timer", "smart-long.timer",
    "apps.service", "apps-catalog.service", "apps-catalog.timer", "apps-all.service",
    "domains.service", "domains.timer", "notify.service", "notify.timer",
    "notify-failure@*.service", "recycle.service", "recycle.timer", "spindown.service",
    "mdns.service", "firewall.service", "history.service", "wol@*.service",
    "burnin@*.service", "tailscale-install.service", "tailscale-up.service",
    "rollback.service", "parity.service", "import.service", "network-apply.service",
    "disk-blink.service", "dahlia-app-*.service",
)

COMMANDS = {
    "system/uname.txt": ["uname", "-a"],
    "system/uptime.txt": ["uptime"],
    "system/memory.txt": ["free", "-h"],
    "system/failed-units.txt": ["systemctl", "--failed", "--no-pager"],
    "system/dahlia-units.txt": ["systemctl", "list-units", *OUR_UNITS, "mnt-*", "--all",
                                "--no-pager"],
    "system/timers.txt": ["systemctl", "list-timers", "--all", "--no-pager"],
    "system/packages.txt": ["dpkg-query", "-W", "-f", "${Package} ${Version} ${Status}\n",
                            *OUR_PACKAGES, "docker-ce", "mergerfs", "snapraid", "linux-image-*"],
    "system/snapper.txt": ["snapper", "--no-dbus", "-c", "root", "list"],
    "journal/warnings.txt": ["journalctl", "-b", "-p", "warning", "-n", "3000", "--no-pager"],
    "journal/dahlia.txt": ["journalctl", "-b", *[a for u in OUR_UNITS for a in ("-u", u)],
                           "-n", "3000", "--no-pager"],
    "journal/kernel.txt": ["journalctl", "-b", "-k", "-n", "1500", "--no-pager"],
    "journal/previous-boot.txt": ["journalctl", "-b", "-1", "-p", "err", "-n", "500",
                                  "--no-pager"],
    "storage/lsblk.txt": ["lsblk", "-o",
                          "NAME,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL,TRAN,ROTA"],
    "storage/findmnt.txt": ["findmnt", "-l", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"],
    "storage/df.txt": ["df", "-h", "-x", "tmpfs", "-x", "devtmpfs", "-x", "overlay"],
    "storage/dahlia-disk.txt": ["disk", "list"],
    "storage/parity.json": ["dahlia-snapraid", "status", "--json"],
    "storage/btrfs.txt": ["btrfs", "filesystem", "show"],
    "storage/smart-scan.txt": ["smartctl", "--scan"],
    "network/addresses.txt": ["ip", "-br", "addr"],
    "network/routes.txt": ["ip", "route"],
    "network/nftables.txt": ["nft", "list", "ruleset"],
    "network/listening.txt": ["ss", "-tulpn"],
    "docker/ps.txt": ["docker", "ps", "-a", "--format",
                      "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"],
    "docker/info.txt": ["docker", "info", "--format",
                        "{{.ServerVersion}} {{.Driver}} {{.DockerRootDir}} "
                        "containers={{.Containers}} images={{.Images}}"],
    "docker/df.txt": ["docker", "system", "df"],
    "apt/policy.txt": ["apt-cache", "policy"],
}
LOG_TAILS = {
    "apt/history.log": "apt/history.log",
    "apt/unattended-upgrades.log": "unattended-upgrades/unattended-upgrades.log",
}
TAIL = 200 * 1024


def redact(value):
    """JSON data with secret-looking keys blanked."""
    if isinstance(value, dict):
        return {k: "***" if SECRET_KEY_RE.search(k) and v not in ("", None, False)
                else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def read_tail(path: Path, limit: int = TAIL) -> str | None:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - limit))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return None


def pack(entries: dict[str, str], mtime: float) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in entries.items():
            data = text.encode("utf-8", "replace")
            info = tarfile.TarInfo(f"dahlia-diagnostics/{name}")
            info.size = len(data)
            info.mtime = int(mtime)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def json_file(path: Path) -> str | None:
    text = read_tail(path, 4 * 1024 * 1024)
    if text is None:
        return None
    if path.suffix != ".json":
        return text
    try:
        return json.dumps(redact(json.loads(text)), indent=2) + "\n"
    except ValueError:
        return "(not valid JSON)\n"


def command_output(ctx: Context, name: str, cmd: list[str]) -> str:
    try:
        proc = ctx.runner.run(cmd, check=False, timeout=120)
    except Exception as e:  # noqa: BLE001 - a missing tool must not stop the bundle
        return f"[not available: {e}]\n"
    if proc.returncode == 0 and name.endswith(".json"):
        try:
            return json.dumps(redact(json.loads(proc.stdout)), indent=2) + "\n"
        except ValueError:
            pass
    out = proc.stdout
    if proc.stderr.strip():
        out += f"\n[stderr]\n{proc.stderr}"
    if proc.returncode:
        out += f"\n[exit code {proc.returncode}]\n"
    return out


def collect(ctx: Context) -> bytes:
    now = time.time()
    out: dict[str, str] = {}
    for name, cmd in COMMANDS.items():
        out[name] = command_output(ctx, name, cmd)
    files = [(f"etc-dahlia/{n}", ctx.etc / "dahlia" / n) for n in ETC_FILES]
    files += [(f"etc/{n}", ctx.etc / n) for n in ETC_OTHER]
    files += [(f"var-lib-dahlia/{n}", ctx.var / n) for n in VAR_FILES]
    files += [(f"proc/{n}", ctx.proc / n) for n in PROC_FILES]
    for name, path in files:
        text = json_file(path)
        if text is not None:
            out[name] = text
    for name, rel in LOG_TAILS.items():
        text = read_tail(ctx.log_dir / rel)
        if text is not None:
            out[name] = text
    apps_dir = ctx.var / "apps"
    if apps_dir.is_dir():
        for d in sorted(apps_dir.iterdir()):
            for f in ("state.json", "app.yaml"):
                text = json_file(d / f)
                if text is not None:
                    out[f"apps/{d.name}/{f}"] = text
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
    out["README.txt"] = (
        f"Dahlia diagnostics from {ctx.hostname()}, {stamp}.\n\n"
        "Left out: app compose files and secrets, backup password, SSH and TLS keys.\n"
        "Settings keys that look like passwords or tokens are replaced by ***.\n"
        "Journals and file lists can still contain names of files, shares and users;\n"
        "look through it before posting it publicly.\n\n"
        + "\n".join(sorted(out)) + "\n")
    return pack(out, now)


@action("system.diagnostics", mutating=True)
def system_diagnostics(ctx: Context):
    """Build the diagnostics bundle for the dashboard to download."""
    data = collect(ctx)
    target = ctx.dashboard_run / BUNDLE
    tmp = target.with_name(BUNDLE + ".tmp")
    tmp.write_bytes(data)
    st = os.stat(ctx.dashboard_run)
    os.chown(tmp, st.st_uid, st.st_gid)
    tmp.chmod(0o600)
    tmp.replace(target)
    return {"size": len(data), "hostname": ctx.hostname(), "created": int(time.time())}
