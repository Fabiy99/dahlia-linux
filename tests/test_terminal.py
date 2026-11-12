"""terminal and the dashboard's /api/terminal WebSocket.

The server runs with `cat` instead of `su`; nothing logs in anywhere.
"""

import json
import socket
import struct
import threading
from types import SimpleNamespace

import pytest
from dahlia_dashboard import terminal
from dahlia_dashboard.terminal import Frames, Server, TerminalError, frame
from starlette.websockets import WebSocketDisconnect
from test_dashboard import env, logged_in  # noqa: F401 (fixture)

USERS = {
    "dahlia-admin": SimpleNamespace(pw_uid=1001, pw_shell="/bin/bash"),
    "www-data": SimpleNamespace(pw_uid=33, pw_shell="/usr/sbin/nologin"),
    "nobody": SimpleNamespace(pw_uid=65534, pw_shell="/usr/sbin/nologin"),
    "locked": SimpleNamespace(pw_uid=1002, pw_shell="/usr/sbin/nologin"),
}


def getpw(name):
    return USERS[name]


def test_check_user():
    assert terminal.check_user("dahlia-admin", getpw) == "dahlia-admin"
    for bad in ("www-data", "nobody", "locked", "unknown", "root; id", "", None, "A"):
        with pytest.raises(TerminalError):
            terminal.check_user(bad, getpw)


def test_frames():
    f = Frames()
    data = frame(b"d", b"ls\r") + frame(b"r", b'{"cols": 100, "rows": 30}')
    assert f.feed(data[:3]) == []
    assert f.feed(data[3:]) == [(b"d", b"ls\r"), (b"r", b'{"cols": 100, "rows": 30}')]
    with pytest.raises(TerminalError):
        Frames().feed(b"x" + struct.pack(">I", 1) + b"a")
    with pytest.raises(TerminalError):
        Frames().feed(b"d" + struct.pack(">I", terminal.MAX_FRAME + 1))


def start_server(tmp_path, **kw):
    server = Server(tmp_path / "t.sock", command=lambda user: ["cat"], getpw=getpw, **kw)
    srv = server.bind(group=None)
    threading.Thread(target=server.serve, args=(srv,), daemon=True).start()
    return server


def connect(path, hello):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5)
    c.connect(str(path))
    c.sendall(json.dumps(hello).encode() + b"\n")
    return c


def read_until(c, needle):
    out = b""
    while needle not in out:
        chunk = c.recv(4096)
        if not chunk:
            break
        out += chunk
    return out


def test_session_relays_through_pty(tmp_path, capfd):
    server = start_server(tmp_path)
    assert oct(server.path.stat().st_mode & 0o777) == "0o660"
    c = connect(server.path, {"user": "dahlia-admin", "cols": 100, "rows": 30, "by": "anna"})
    c.sendall(frame(b"r", b'{"cols": 120, "rows": 40}') + frame(b"d", b"hello dahlia\n"))
    assert b"hello dahlia" in read_until(c, b"hello dahlia")
    c.close()
    assert "session started for dahlia-admin (dashboard user anna" in capfd.readouterr().err


def test_session_refuses_service_account(tmp_path):
    server = start_server(tmp_path)
    c = connect(server.path, {"user": "www-data"})
    assert b"not a login account" in read_until(c, b"account")


def test_idle_timeout(tmp_path):
    server = start_server(tmp_path, idle=0.3)
    c = connect(server.path, {"user": "dahlia-admin"})
    assert b"[idle timeout]" in read_until(c, b"[idle timeout]")


# --- dashboard ----------------------------------------------------------------


def fake_terminal(path, seen):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        with conn:
            buf = b""
            while b"\n" not in buf:
                buf += conn.recv(1)
            seen["hello"] = json.loads(buf)
            conn.sendall(b"Password: ")
            seen["frames"] = Frames().feed(conn.recv(4096))

    threading.Thread(target=run, daemon=True).start()


def test_websocket_logs_in_as_the_dashboard_account(env, tmp_path):  # noqa: F811
    client, _, cfg = logged_in(env)
    cfg.terminal_socket = str(tmp_path / "term.sock")
    seen = {}
    fake_terminal(tmp_path / "term.sock", seen)
    # No other account can be asked for; su still wants that account's password.
    with client.websocket_connect("/api/terminal?user=dahlia-admin&cols=90&rows=20",
                                  headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_bytes() == b"Password: "
        ws.send_bytes(b"secret\r")
    assert seen["hello"]["user"] == "fabian" and seen["hello"]["cols"] == 90
    assert seen["hello"]["by"] == "fabian"
    assert seen["frames"] == [(b"d", b"secret\r")]


def test_websocket_needs_login_and_origin(env, tmp_path):  # noqa: F811
    client, *_ = env
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect(
            "/api/terminal", headers={"origin": "http://testserver"}):
        pass
    assert e.value.code == 4401
    client, *_ = logged_in(env)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            "/api/terminal", headers={"origin": "https://evil.example"}):
        pass


def test_websocket_without_service(env, tmp_path):  # noqa: F811
    client, _, cfg = logged_in(env)
    cfg.terminal_socket = str(tmp_path / "missing.sock")
    with client.websocket_connect("/api/terminal", headers={"origin": "http://testserver"}) as ws:
        assert json.loads(ws.receive_text()) == {"error": "terminal.unavailable"}
