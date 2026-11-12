"""Wire format between agent and clients.

One request per connection. The client sends one JSON object terminated by a
newline, the agent answers with one JSON object terminated by a newline:

    -> {"action": "storage.add", "params": {"uuid": "...", "role": "data"}}
    <- {"ok": true, "result": ...}
    <- {"ok": false, "error": {"code": "invalid_params", "message": "..."}}
"""

import json

DEFAULT_SOCKET = "/run/dahlia/agent.sock"
SOCKET_GROUP = "dahlia-agent"
MAX_REQUEST = 64 * 1024

# Error codes a client can rely on.
BAD_REQUEST = "bad_request"
UNKNOWN_ACTION = "unknown_action"
INVALID_PARAMS = "invalid_params"
FORBIDDEN = "forbidden"
BUSY = "busy"
FAILED = "failed"
INTERNAL = "internal"
# Client side only.
UNAVAILABLE = "unavailable"
TIMEOUT = "timeout"


class AgentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PassFd:
    """Result of an action that also hands an open file descriptor to the client.

    The server sends the descriptor with the reply (SCM_RIGHTS) and closes its
    own copy; client.call_fd returns it.
    """

    def __init__(self, result, fd: int):
        self.result = result
        self.fd = fd


def encode(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode() + b"\n"


def ok(result) -> dict:
    return {"ok": True, "result": result}


def error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def parse_request(data: bytes) -> tuple[str, dict]:
    try:
        req = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AgentError(BAD_REQUEST, "request is not valid JSON") from None
    if not isinstance(req, dict):
        raise AgentError(BAD_REQUEST, "request must be a JSON object")
    extra = set(req) - {"action", "params"}
    if extra:
        raise AgentError(BAD_REQUEST, f"unknown request fields: {', '.join(sorted(extra))}")
    action = req.get("action")
    if not isinstance(action, str):
        raise AgentError(BAD_REQUEST, "'action' must be a string")
    params = req.get("params", {})
    if not isinstance(params, dict):
        raise AgentError(BAD_REQUEST, "'params' must be a JSON object")
    return action, params


def parse_response(data: bytes):
    """Return the result of a response or raise AgentError."""
    try:
        resp = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AgentError(INTERNAL, "agent sent invalid JSON") from None
    if not isinstance(resp, dict) or not isinstance(resp.get("ok"), bool):
        raise AgentError(INTERNAL, "agent sent a malformed response")
    if resp["ok"]:
        return resp.get("result")
    err = resp.get("error") or {}
    raise AgentError(str(err.get("code", INTERNAL)), str(err.get("message", "unknown error")))
