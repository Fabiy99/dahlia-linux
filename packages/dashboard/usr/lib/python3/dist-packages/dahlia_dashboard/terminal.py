"""terminal: shell sessions for the dashboard's terminal page.

Runs as the unprivileged user term (terminal.service) and
listens on /run/terminal/terminal.sock (group ui, 0660). For
each connection it starts `su -l <user>` in a pseudo-terminal, so the
session begins with the Linux password prompt of that account; Dahlia
itself never runs a command as root. Only accounts with UID 1000 or higher
and a real login shell are accepted.

Protocol on the socket: one JSON line from the dashboard
    {"user": "fabian", "cols": 120, "rows": 32, "by": "<dashboard user>", "ip": "..."}
then frames from the dashboard, each a type byte, a 4-byte big-endian
length and the payload: b"d" input bytes, b"r" {"cols", "rows"} as JSON.
The service sends the raw terminal output back. A session ends when the
shell exits, the connection closes or nobody typed for IDLE seconds.
"""

import argparse
import contextlib
import fcntl
import json
import os
import pwd
import re
import selectors
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

SOCKET = Path("/run/terminal/terminal.sock")
SOCKET_GROUP = "ui"
IDLE = 15 * 60
MAX_SESSIONS = 4
MAX_FRAME = 64 * 1024
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
NO_LOGIN = ("nologin", "false")


class TerminalError(ValueError):
    pass


def check_user(name, getpw=pwd.getpwnam) -> str:
    """The account to log in to; TerminalError for system and service accounts."""
    if not isinstance(name, str) or not USER_RE.fullmatch(name):
        raise TerminalError("invalid user name")
    try:
        pw = getpw(name)
    except KeyError:
        raise TerminalError(f"no user {name}") from None
    if pw.pw_uid < 1000 or pw.pw_uid == 65534 or Path(pw.pw_shell).name in NO_LOGIN:
        raise TerminalError(f"{name} is not a login account")
    return name


def size(value, low: int, high: int, default: int) -> int:
    return value if isinstance(value, int) and low <= value <= high else default


def set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def read_line(conn: socket.socket, limit: int = 4096) -> bytes:
    data = b""
    while not data.endswith(b"\n"):
        chunk = conn.recv(1)
        if not chunk or len(data) >= limit:
            raise TerminalError("no handshake")
        data += chunk
    return data


class Frames:
    """Splits the dashboard's byte stream into (type, payload) frames."""

    def __init__(self):
        self.buf = b""

    def feed(self, data: bytes) -> list[tuple[bytes, bytes]]:
        self.buf += data
        out = []
        while len(self.buf) >= 5:
            kind, length = self.buf[:1], struct.unpack(">I", self.buf[1:5])[0]
            if length > MAX_FRAME or kind not in (b"d", b"r"):
                raise TerminalError("bad frame")
            if len(self.buf) < 5 + length:
                break
            out.append((kind, self.buf[5:5 + length]))
            self.buf = self.buf[5 + length:]
        return out


def frame(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack(">I", len(payload)) + payload


def log(msg: str) -> None:
    print(f"terminal: {msg}", file=sys.stderr, flush=True)


class Server:
    def __init__(self, path: Path = SOCKET, command=None, idle: int = IDLE,
                 getpw=pwd.getpwnam, clock=time.monotonic):
        self.path = path
        # Tests replace su with a harmless program.
        self.command = command or (lambda user: ["su", "-l", "--", user])
        self.idle = idle
        self.getpw = getpw
        self.clock = clock
        self.sessions = 0
        self.lock = threading.Lock()

    def bind(self, group: str | None = SOCKET_GROUP) -> socket.socket:
        self.path.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.path))
        if group:
            import grp

            os.chown(self.path, -1, grp.getgrnam(group).gr_gid)
        self.path.chmod(0o660)
        srv.listen(8)
        return srv

    def serve(self, srv: socket.socket) -> None:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn: socket.socket) -> None:
        with conn:
            with self.lock:
                if self.sessions >= MAX_SESSIONS:
                    conn.sendall(b"\r\nToo many terminal sessions.\r\n")
                    return
                self.sessions += 1
            try:
                self.session(conn)
            except (TerminalError, OSError, ValueError) as e:
                with contextlib.suppress(OSError):
                    conn.sendall(f"\r\n{e}\r\n".encode())
            finally:
                with self.lock:
                    self.sessions -= 1

    def session(self, conn: socket.socket) -> None:
        hello = json.loads(read_line(conn))
        if not isinstance(hello, dict):
            raise TerminalError("no handshake")
        user = check_user(hello.get("user"), self.getpw)
        who = f"{user} (dashboard user {str(hello.get('by'))[:64]}, {str(hello.get('ip'))[:64]})"
        # setsid -c makes the pty the controlling terminal of su; this avoids
        # fork() in a threaded process.
        fd, slave = os.openpty()
        env = {"TERM": "xterm-256color", "LANG": "C.UTF-8",
               "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        try:
            set_size(slave, size(hello.get("cols"), 10, 500, 80),
                     size(hello.get("rows"), 5, 200, 24))
            proc = subprocess.Popen(["setsid", "-c", *self.command(user)], stdin=slave,
                                    stdout=slave, stderr=slave, env=env, close_fds=True)
        except OSError:
            os.close(fd)
            raise
        finally:
            os.close(slave)
        log(f"session started for {who}")
        reason = "closed"
        try:
            reason = self.relay(conn, fd)
        finally:
            os.close(fd)
            if proc.poll() is None:
                proc.send_signal(signal.SIGHUP)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            log(f"session ended for {who}: {reason}")

    def relay(self, conn: socket.socket, fd: int) -> str:
        frames = Frames()
        sel = selectors.DefaultSelector()
        sel.register(conn, selectors.EVENT_READ, "conn")
        sel.register(fd, selectors.EVENT_READ, "pty")
        last_input = self.clock()
        while True:
            if self.clock() - last_input > self.idle:
                conn.sendall(b"\r\n[idle timeout]\r\n")
                return "idle timeout"
            for key, _ in sel.select(timeout=min(5, self.idle)):
                if key.data == "pty":
                    try:
                        data = os.read(fd, 65536)
                    except OSError:  # EIO: the shell exited
                        return "shell exited"
                    if not data:
                        return "shell exited"
                    conn.sendall(data)
                    continue
                data = conn.recv(65536)
                if not data:
                    return "closed"
                for kind, payload in frames.feed(data):
                    if kind == b"d":
                        last_input = self.clock()
                        os.write(fd, payload)
                    else:
                        r = json.loads(payload)
                        if isinstance(r, dict):
                            set_size(fd, size(r.get("cols"), 10, 500, 80),
                                     size(r.get("rows"), 5, 200, 24))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="terminal", description=__doc__.splitlines()[0])
    p.add_argument("--socket", type=Path, default=SOCKET)
    args = p.parse_args(argv)
    server = Server(args.socket)
    server.serve(server.bind())
    return 0
