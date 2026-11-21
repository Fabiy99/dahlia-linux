#!/usr/bin/python3 -I
"""Stand-in for the Dahlia app in the install test without a screen
(iso/test-install.sh headless). Does what app/lib/src/headless.dart does:

  patch <iso> <token> <pc>   fill the slots of the ISO in place
  serve <port> <token> <dir> answer headless.sh: picks /dev/vda, writes
                             the reported steps and fingerprint to <dir>
"""

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SECTOR = 2048
MARKER = b"#DAHLIA-SLOT:"
SIZES = {"headless": 4096, "grub": 512, "isolinux": 512}
ACCOUNT = "fabi"
PASSWORD = "Dahlia-Headless-9"  # noqa: S105 - test account
CODE = "HEAD7-LESS2"
HOSTNAME = "nas-headless"
KEYMAP = "us"
TIMEZONE = "Europe/Vienna"


def slot(body: str, size: int) -> bytes:
    out = body
    while len(out) < size:
        left = size - len(out)
        out += ("#" * min(63, left - 1) + "\n") if left > 1 else "\n"
    return out.encode()


def patch(iso: Path, token: str, pc: str) -> None:
    contents = {
        "headless": slot(f"HOSTNAME={HOSTNAME}\nTIMEZONE={TIMEZONE}\nKEYMAP={KEYMAP}\n"
                         f"PC={pc}\nTOKEN={token}\n", SIZES["headless"]),
        "grub": slot("set default=4\nset timeout=1\n", SIZES["grub"]),
        "isolinux": slot("default dahlia-headless\ntimeout 10\n", SIZES["isolinux"]),
    }
    found = {}
    with iso.open("r+b") as f:
        pos = 0
        while chunk := f.read(SECTOR * 512):
            for off in range(0, len(chunk), SECTOR):
                head = chunk[off:off + 64]
                if head.startswith(MARKER):
                    name, _, size = head[len(MARKER):].split(b"\n")[0].decode().partition(":")
                    if SIZES.get(name) == int(size or 0):
                        found[name] = pos + off
            pos += len(chunk)
        missing = set(SIZES) - set(found)
        if missing:
            sys.exit(f"slots missing in {iso}: {', '.join(sorted(missing))}")
        for name, at in found.items():
            f.seek(at)
            f.write(contents[name])
    print(f"patched {iso}: {found}")


def serve(port: int, token: str, out: Path) -> None:
    answer = (f"DISK=/dev/vda\nACCOUNT={ACCOUNT}\nPASSWORD={PASSWORD.encode().hex()}\n"
              f"SETUP_CODE={CODE}\nCONFIRM=\n")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            url = urlsplit(self.path)
            parts = url.path.strip("/").split("/")
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            body = b""
            if len(parts) != 3 or parts[:2] != ["headless", token]:
                self.send_response(404)
            elif parts[2] == "hello":
                (out / "disks.txt").write_text(q.get("disks", ""))
                body = b"ok\n"
                self.send_response(200)
            elif parts[2] == "answer":
                body = answer.encode()
                self.send_response(200)
            elif parts[2] == "progress":
                with (out / "steps.txt").open("a") as f:
                    f.write(q.get("step", "") + "\n")
                if q.get("fingerprint"):
                    (out / "fingerprint.txt").write_text(q["fingerprint"])
                body = b"ok\n"
                self.send_response(200)
            else:
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            with (out / "app.log").open("a") as f:
                f.write((fmt % args) + "\n")

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "patch":
        patch(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    elif len(sys.argv) == 5 and sys.argv[1] == "serve":
        serve(int(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
    else:
        sys.exit(__doc__)
