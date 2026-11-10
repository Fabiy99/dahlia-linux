"""Dashboard accounts, sessions and login rate limiting.

Accounts are separate from Linux users. They live in users.json in the state
directory (owner dahlia-ui, mode 0600) with scrypt password hashes.
Sessions are random tokens kept in memory; a restart logs everyone out.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
MIN_PASSWORD = 10
MAX_PASSWORD = 1024

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


class AuthError(Exception):
    pass


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), digest)


def check_new_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.fullmatch(username):
        raise AuthError("username")
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError("password")


class UserStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        # Compared against when the user does not exist, so timing does not
        # tell whether a name is valid.
        self._dummy = hash_password(secrets.token_hex(8))

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"users": {}}

    def _save(self, data: dict) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self.path)

    def has_users(self) -> bool:
        return bool(self._load()["users"])

    def create(self, username: str, password: str) -> None:
        check_new_credentials(username, password)
        with self.lock:
            data = self._load()
            if username in data["users"]:
                raise AuthError("exists")
            data["users"][username] = {"password": hash_password(password)}
            self._save(data)

    def verify(self, username: str, password: str) -> bool:
        user = self._load()["users"].get(username)
        stored = user["password"] if user else self._dummy
        return verify_password(password, stored) and user is not None

    def change_password(self, username: str, old: str, new: str) -> None:
        if not self.verify(username, old):
            raise AuthError("old_password")
        check_new_credentials(username, new)
        with self.lock:
            data = self._load()
            data["users"][username]["password"] = hash_password(new)
            self._save(data)


@dataclass
class Session:
    username: str
    created: float
    last_seen: float


class SessionStore:
    def __init__(self, max_age: int = 12 * 3600, idle: int = 2 * 3600, clock=time.monotonic):
        self.max_age = max_age
        self.idle = idle
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.lock:
            self.sessions[token] = Session(username, now, now)
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = self.clock()
        with self.lock:
            s = self.sessions.get(token)
            if s is None:
                return None
            if now - s.created > self.max_age or now - s.last_seen > self.idle:
                del self.sessions[token]
                return None
            s.last_seen = now
            return s

    def drop(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)

    def drop_user(self, username: str, keep: str | None = None) -> None:
        with self.lock:
            for t in [t for t, s in self.sessions.items() if s.username == username and t != keep]:
                del self.sessions[t]


class RateLimiter:
    """At most `limit` failures per key within `window` seconds."""

    def __init__(self, limit: int = 5, window: int = 300, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self.failures.get(key, []) if now - t < self.window]
        if recent:
            self.failures[key] = recent
        else:
            self.failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        with self.lock:
            return len(self._recent(key, self.clock())) >= self.limit

    def fail(self, key: str) -> None:
        now = self.clock()
        with self.lock:
            self.failures[key] = [*self._recent(key, now), now]

    def reset(self, key: str) -> None:
        with self.lock:
            self.failures.pop(key, None)
