"""Client for agent, used by the dashboard and by agent-call."""

import argparse
import json
import os
import socket
import sys

from .protocol import (
    BAD_REQUEST,
    DEFAULT_SOCKET,
    FORBIDDEN,
    INTERNAL,
    MAX_REQUEST,
    TIMEOUT,
    UNAVAILABLE,
    AgentError,
    encode,
    parse_response,
)

# updates.check runs apt-get update; leave room for slow mirrors.
DEFAULT_TIMEOUT = 900
MAX_RESPONSE = 16 * 1024 * 1024


def call(action: str, params: dict | None = None, *, path: str = DEFAULT_SOCKET,
         timeout: float = DEFAULT_TIMEOUT):
    """Run one action and return its result. Raises AgentError."""
    result, fd = exchange(action, params, path, timeout)
    if fd is not None:
        os.close(fd)
    return result


def call_fd(action: str, params: dict | None = None, *, path: str = DEFAULT_SOCKET,
            timeout: float = DEFAULT_TIMEOUT) -> tuple[object, int]:
    """Run an action that hands over a file descriptor; returns (result, fd).
    The caller closes fd."""
    result, fd = exchange(action, params, path, timeout)
    if fd is None:
        raise AgentError(INTERNAL, f"{action} sent no file descriptor")
    return result, fd


def exchange(action: str, params: dict | None, path: str, timeout: float):
    request = encode({"action": action, "params": params or {}})
    if len(request) > MAX_REQUEST:
        raise AgentError(BAD_REQUEST, "request too large")
    fds: list[int] = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(path)
            sock.sendall(request)
            buf = b""
            while b"\n" not in buf:
                chunk, got, _flags, _addr = socket.recv_fds(sock, 65536, 1)
                fds += got
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_RESPONSE:
                    raise AgentError(INTERNAL, "response too large")
        except PermissionError:
            raise AgentError(FORBIDDEN, f"no permission for {path}") from None
        except (FileNotFoundError, ConnectionRefusedError):
            raise AgentError(UNAVAILABLE, f"agent is not running ({path})") from None
        except TimeoutError:
            raise AgentError(TIMEOUT, f"no answer within {timeout:.0f}s") from None
        except BaseException:
            for fd in fds:
                os.close(fd)
            raise
    for extra in fds[1:]:
        os.close(extra)
    fd = fds[0] if fds else None
    if not buf:
        if fd is not None:
            os.close(fd)
        raise AgentError(INTERNAL, "agent closed the connection without an answer")
    try:
        return parse_response(buf.split(b"\n", 1)[0]), fd
    except AgentError:
        if fd is not None:
            os.close(fd)
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="agent-call",
        description="Run one agent action and print the result as JSON.",
        epilog="Example: agent-call storage.add '{\"uuid\": \"...\", \"role\": \"data\"}'",
    )
    p.add_argument("action", help="e.g. system.info; agent.actions lists all")
    p.add_argument("params", nargs="?", default="{}", help="parameters as a JSON object")
    p.add_argument("--socket", default=DEFAULT_SOCKET)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = p.parse_args(argv)

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(f"agent-call: params are not valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(params, dict):
        print("agent-call: params must be a JSON object", file=sys.stderr)
        return 2
    try:
        result = call(args.action, params, path=args.socket, timeout=args.timeout)
    except AgentError as e:
        print(f"agent-call: {e.code}: {e.message}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0
