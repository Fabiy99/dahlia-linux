"""Client for dahlia-agent, used by the dashboard and by dahlia-agent-call."""

import argparse
import json
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
    request = encode({"action": action, "params": params or {}})
    if len(request) > MAX_REQUEST:
        raise AgentError(BAD_REQUEST, "request too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(path)
            sock.sendall(request)
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(65536)
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
    if not buf:
        raise AgentError(INTERNAL, "agent closed the connection without an answer")
    return parse_response(buf.split(b"\n", 1)[0])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="dahlia-agent-call",
        description="Run one dahlia-agent action and print the result as JSON.",
        epilog="Example: dahlia-agent-call storage.add '{\"uuid\": \"...\", \"role\": \"data\"}'",
    )
    p.add_argument("action", help="e.g. system.info; agent.actions lists all")
    p.add_argument("params", nargs="?", default="{}", help="parameters as a JSON object")
    p.add_argument("--socket", default=DEFAULT_SOCKET)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = p.parse_args(argv)

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(f"dahlia-agent-call: params are not valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(params, dict):
        print("dahlia-agent-call: params must be a JSON object", file=sys.stderr)
        return 2
    try:
        result = call(args.action, params, path=args.socket, timeout=args.timeout)
    except AgentError as e:
        print(f"dahlia-agent-call: {e.code}: {e.message}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0
