"""Socket server of dahlia-agent.

Started by dahlia-agent.socket (socket activation). For tests it can also
bind a path itself (--socket). Every connection is checked twice: the socket
file is root:dahlia-agent 0660, and the peer credentials (SO_PEERCRED) must
belong to root or to a member of dahlia-agent.
"""

import argparse
import contextlib
import grp
import os
import socket
import struct
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from . import actions
from .protocol import (
    BAD_REQUEST,
    DEFAULT_SOCKET,
    FORBIDDEN,
    INTERNAL,
    MAX_REQUEST,
    SOCKET_GROUP,
    AgentError,
    encode,
    error,
    ok,
    parse_request,
)

SD_LISTEN_FDS_START = 3
READ_TIMEOUT = 10

Authorizer = Callable[[int, int, int], bool]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def peer_groups(proc: Path, pid: int) -> set[int]:
    try:
        status = (proc / str(pid) / "status").read_text()
    except OSError:
        return set()
    for line in status.splitlines():
        if line.startswith("Groups:"):
            return {int(g) for g in line.split()[1:]}
    return set()


def group_authorizer(gid: int, proc: Path = Path("/proc")) -> Authorizer:
    def authorize(pid: int, uid: int, peer_gid: int) -> bool:
        return uid == 0 or peer_gid == gid or gid in peer_groups(proc, pid)

    return authorize


def dispatch(ctx: actions.Context, data: bytes, lock: threading.Lock) -> tuple[str, dict]:
    """Handle one raw request. Returns the action name (for the log) and the reply."""
    name = "-"
    try:
        name, params = parse_request(data)
        act = actions.lookup(name)
        checked = actions.check_params(act, params)
        if act.mutating:
            # One change at a time: storage and update actions must not overlap.
            with lock:
                return name, ok(act.func(ctx, **checked))
        return name, ok(act.func(ctx, **checked))
    except AgentError as e:
        return name, error(e.code, e.message)
    except Exception as e:
        # The client gets a generic reply, the journal the detail.
        log(f"internal error in {name}: {type(e).__name__}: {e}")
        return name, error(INTERNAL, f"internal error in {name}")


def read_request(conn: socket.socket) -> bytes:
    conn.settimeout(READ_TIMEOUT)
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_REQUEST:
            raise AgentError(BAD_REQUEST, f"request larger than {MAX_REQUEST} bytes")
    return buf.split(b"\n", 1)[0]


def peer_credentials(conn: socket.socket) -> tuple[int, int, int]:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)


class Server:
    def __init__(self, sock: socket.socket, ctx: actions.Context, authorize: Authorizer):
        self.sock = sock
        self.ctx = ctx
        self.authorize = authorize
        self.lock = threading.Lock()

    def handle(self, conn: socket.socket) -> None:
        with conn:
            pid, uid, gid = peer_credentials(conn)
            if not self.authorize(pid, uid, gid):
                log(f"denied uid={uid} pid={pid}")
                self._send(conn, error(FORBIDDEN, "not allowed to use the agent"))
                return
            try:
                data = read_request(conn)
            except AgentError as e:
                self._send(conn, error(e.code, e.message))
                return
            except OSError as e:
                log(f"read failed uid={uid} pid={pid}: {e}")
                return
            name, reply = dispatch(self.ctx, data, self.lock)
            status = "ok" if reply["ok"] else reply["error"]["code"]
            log(f"uid={uid} pid={pid} action={name} {status}")
            self._send(conn, reply)

    @staticmethod
    def _send(conn: socket.socket, reply: dict) -> None:
        try:
            conn.settimeout(READ_TIMEOUT)
            conn.sendall(encode(reply))
        except OSError:
            pass

    def serve_forever(self) -> None:
        while True:
            conn, _ = self.sock.accept()
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()


def activated_socket() -> socket.socket | None:
    """The socket systemd passed in, if any."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    if int(os.environ.get("LISTEN_FDS", "0")) < 1:
        return None
    for var in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(var, None)
    return socket.socket(fileno=SD_LISTEN_FDS_START)


def bind_socket(path: str, gid: int | None) -> socket.socket:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_socket():
        p.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o117)
    try:
        sock.bind(path)
    finally:
        os.umask(old)
    if gid is not None and os.geteuid() == 0:
        os.chown(path, 0, gid)
    sock.listen(16)
    return sock


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dahlia-agent", description="Dahlia privileged agent.")
    p.add_argument("--socket", help=f"bind this path instead of using socket activation "
                   f"(default when started by systemd: {DEFAULT_SOCKET})")
    p.add_argument("--group", default=SOCKET_GROUP, help="group allowed to connect")
    args = p.parse_args(argv)

    try:
        gid = grp.getgrnam(args.group).gr_gid
    except KeyError:
        log(f"dahlia-agent: group {args.group} does not exist")
        return 1

    sock = None if args.socket else activated_socket()
    if sock is None:
        sock = bind_socket(args.socket or DEFAULT_SOCKET, gid)

    log(f"dahlia-agent: {len(actions.ACTIONS)} actions, group {args.group} ({gid})")
    with contextlib.suppress(KeyboardInterrupt):
        Server(sock, actions.Context(), group_authorizer(gid)).serve_forever()
    return 0
