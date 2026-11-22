"""The install job: validated settings, the Unraid files to copy, and the
install.sh process whose "@@step" lines become the progress shown in the UI.
"""

import json
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from . import unraid

STEPS = ["check", "partition", "format", "debootstrap", "system", "dahlia", "unraid", "boot",
         "finished"]
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
ACCOUNT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
RESERVED = {"root", "nobody", "daemon", "bin", "sys", "adm", "sudo", "users", "staff",
            "docker", "sshd", "messagebus", "ui", "dahlia"}
TZ_RE = re.compile(r"^[A-Za-z]+(/[A-Za-z0-9_+-]+){1,2}$")
KEYMAPS = ("de", "us", "gb", "fr", "ch", "at", "es", "it", "nl", "pl", "se", "dk", "no", "fi")


class InvalidSettings(ValueError):
    """The key is a locale key of the helper's UI."""


def setup_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def validate(body: dict) -> dict:
    """Settings from the form; raises InvalidSettings with a locale key."""
    host = str(body.get("hostname", "")).strip().lower()
    account = str(body.get("account", "")).strip().lower()
    password = str(body.get("password", ""))
    tz = str(body.get("timezone", "")).strip()
    keymap = str(body.get("keymap", "de"))
    if not HOST_RE.match(host):
        raise InvalidSettings("error.hostname")
    if not ACCOUNT_RE.match(account) or account in RESERVED or account.startswith("dahlia"):
        raise InvalidSettings("error.account")
    if len(password) < 10 or len(password) > 200 or any(c in password for c in "\n\r\0"):
        raise InvalidSettings("error.password")
    if not TZ_RE.match(tz) or not (Path("/usr/share/zoneinfo") / tz).is_file():
        raise InvalidSettings("error.timezone")
    if keymap not in KEYMAPS:
        raise InvalidSettings("error.keymap")
    return {"hostname": host, "account": account, "password": password, "timezone": tz,
            "keymap": keymap}


def stage_unraid(config: Path, summary: dict, into: Path) -> None:
    """Copy the raw Unraid files and the parsed summary into one folder."""
    into.mkdir(parents=True, exist_ok=True)
    for rel in unraid.COPY:
        src = config / rel
        dst = into / "config" / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (into / "unraid.json").write_text(json.dumps(summary, indent=1))


def settings_file(disk: dict, s: dict, nic: dict, net: dict, code: str, workdir: Path,
                  unraid_dir: Path | None, keyring: Path) -> Path:
    """KEY=value lines for install.sh; every value is validated or quoted safely."""
    pw = workdir / "password"
    pw.touch(mode=0o600)
    pw.write_text(s["password"])
    values = {
        "DISK": disk["path"], "SIZE": str(disk["size"]), "SERIAL": disk["serial"],
        "HOSTNAME_NEW": s["hostname"], "ACCOUNT": s["account"], "PASSWORD_FILE": str(pw),
        "SETUP_CODE": code, "TIMEZONE": s["timezone"], "KEYMAP": s["keymap"],
        "NET_NAME": nic["name"], "NET_MAC": nic["mac"], "NET_MODE": net["mode"],
        "NET_ADDRESS": net.get("address", ""), "NET_GATEWAY": net.get("gateway", ""),
        "NET_DNS": " ".join(net.get("dns", [])),
        "UNRAID_DIR": str(unraid_dir or ""), "KEYRING": str(keyring),
    }
    lines = []
    for k, v in values.items():
        if not re.fullmatch(r"[A-Za-z0-9 ./:_+@-]*", v):
            raise InvalidSettings("error.value")
        lines.append(f"{k}='{v}'")
    f = workdir / "settings"
    f.write_text("\n".join(lines) + "\n")
    return f


class Job:
    """One install at a time; state is what the UI polls."""

    def __init__(self, script: Path):
        self.script = script
        self.lock = threading.Lock()
        self.state = {"state": "idle", "step": "", "error": "", "log": []}
        self.thread: threading.Thread | None = None

    def busy(self) -> bool:
        return self.state["state"] == "running"

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.state, "log": self.state["log"][-40:]}

    def start(self, settings: Path, workdir: Path) -> None:
        with self.lock:
            if self.busy():
                raise InvalidSettings("error.busy")
            self.state = {"state": "running", "step": "check", "error": "", "log": []}
        self.thread = threading.Thread(target=self._run, args=(settings, workdir), daemon=True)
        self.thread.start()

    def _run(self, settings: Path, workdir: Path) -> None:
        proc = subprocess.Popen(["bash", str(self.script), str(settings)], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout or ():
            line = line.rstrip("\n")
            with self.lock:
                if line.startswith("@@step "):
                    self.state["step"] = line[7:].strip()
                elif line.startswith("@@error "):
                    self.state["error"] = line[8:].strip()
                else:
                    self.state["log"] = (self.state["log"] + [line])[-200:]
        code = proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)
        with self.lock:
            ok = code == 0 and self.state["step"] == "finished"
            self.state["state"] = "done" if ok else "failed"
            if not ok and not self.state["error"]:
                self.state["error"] = f"exit {code}"


def workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="dahlia-migrate-"))
