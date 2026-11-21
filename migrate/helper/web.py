"""Web UI of the migration helper (Python standard library only).

Every /api call needs the access code that the helper prints to its log at
start (Unraid: the container's log icon), so nobody else on the network can
start an install.
"""

import hmac
import json
import mimetypes
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import disks, job, net, unraid

ROOT = Path(__file__).resolve().parent.parent


class Helper:
    def __init__(self, config: Path, emhttp: Path, www: Path, script: Path, keyring: Path,
                 access_code: str, allow_loop: bool = False, runner=disks.run, nic=None):
        self.config, self.emhttp, self.www = config, emhttp, www
        self.keyring = keyring
        self.access_code = access_code
        self.allow_loop = allow_loop
        self.runner = runner
        self.nic = nic if nic is not None else net.detect()
        self.job = job.Job(script)
        self.setup_code = ""

    def summary(self) -> dict:
        return unraid.read(self.config, self.emhttp)

    def state(self) -> dict:
        u = self.summary()
        found = self.job.snapshot()
        return {
            "unraid": {k: u[k] for k in ("found", "identity", "network", "disks", "shares",
                                         "appdata", "wireguard", "vms")}
            | {"users": [x["name"] for x in u["users"]],
               "containers": [c["name"] for c in u["containers"]]},
            "disks": disks.scan(u["disks"], self.runner, self.allow_loop),
            "nic": self.nic,
            "job": found,
            "setup_code": self.setup_code if found["state"] in ("running", "done") else "",
        }

    def install(self, body: dict) -> dict:
        if self.job.busy():
            raise job.InvalidSettings("error.busy")
        if not self.nic:
            raise job.InvalidSettings("error.no_network")
        s = job.validate(body)
        u = self.summary()
        found = [d for d in disks.scan(u["disks"], self.runner, self.allow_loop)
                 if d["path"] == body.get("disk")]
        if not found:
            raise job.InvalidSettings("error.disk")
        disk = found[0]
        if disk["lock"]:
            raise job.InvalidSettings("error.disk_locked")
        if not disks.confirm_ok(disk, str(body.get("confirm", ""))):
            raise job.InvalidSettings("error.confirm")
        network = u["network"] if body.get("network") == "unraid" else {"mode": "dhcp"}
        code = job.setup_code()
        work = job.workdir()
        unraid_dir = None
        if u["found"]:
            unraid_dir = work / "unraid"
            job.stage_unraid(self.config, u, unraid_dir)
        settings = job.settings_file(disk, s, self.nic, network, code, work, unraid_dir,
                                     self.keyring)
        self.job.start(settings, work)
        self.setup_code = code
        return {"setup_code": code}


def handler(helper: Helper):
    class Handler(BaseHTTPRequestHandler):
        server_version = "dahlia-migrate"

        def log_message(self, fmt, *args):
            return

        def _json(self, status: int, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            given = self.headers.get("X-Access-Code", "").strip().upper().encode()
            return hmac.compare_digest(given, helper.access_code.encode())

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/"):
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "error.access_code"})
                if path == "/api/state":
                    return self._json(HTTPStatus.OK, helper.state())
                return self._json(HTTPStatus.NOT_FOUND, {"error": "error.not_found"})
            rel = "index.html" if path == "/" else path.lstrip("/")
            f = (helper.www / rel).resolve()
            if helper.www.resolve() not in f.parents or not f.is_file():
                return self._json(HTTPStatus.NOT_FOUND, {"error": "error.not_found"})
            data = f.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != "/api/install":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "error.not_found"})
            if not self._authorized():
                return self._json(HTTPStatus.UNAUTHORIZED, {"error": "error.access_code"})
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 65536)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "error.request"})
            try:
                return self._json(HTTPStatus.OK, helper.install(body))
            except job.InvalidSettings as e:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})

    return Handler


def main() -> int:
    env = os.environ.get
    code = job.setup_code()
    helper = Helper(
        config=Path(env("UNRAID_CONFIG", "/unraid/config")),
        emhttp=Path(env("UNRAID_EMHTTP", "/unraid/emhttp")),
        www=ROOT / "www",
        script=ROOT / "install.sh",
        keyring=Path(env("DAHLIA_KEYRING", "/usr/share/keyrings/dahlia-archive-keyring.asc")),
        access_code=code,
        allow_loop=env("ALLOW_LOOP") == "1",
    )
    port = int(env("PORT", "8484"))
    print(f"Dahlia migration helper on port {port}", flush=True)
    print(f"Zugangscode / access code: {code}", flush=True)
    ThreadingHTTPServer(("", port), handler(helper)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
