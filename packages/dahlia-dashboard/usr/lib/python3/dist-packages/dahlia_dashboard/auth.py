"""Dashboard accounts, sessions and login rate limiting.

Accounts are separate from Linux users. They live in users.json in the state
directory (owner dahlia-ui, mode 0600) with scrypt password hashes.
Sessions are random tokens; sessions.json keeps their hashes over a restart.
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
    """Login sessions, kept in memory and, with a path, in a file.

    The file lets sessions survive a restart of the dashboard (package
    update). It holds SHA-256 hashes of the tokens, never the tokens, so a
    copy of it (backup, diagnostics) does not log anyone in. Times are wall
    clock seconds. last_seen is written at most every SAVE_EVERY seconds.
    """

    SAVE_EVERY = 60

    def __init__(self, max_age: int = 12 * 3600, idle: int = 2 * 3600, clock=time.time,
                 path: Path | None = None):
        self.max_age = max_age
        self.idle = idle
        self.clock = clock
        self.path = path
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.saved = 0.0
        self._load()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _expired(self, s: Session, now: float) -> bool:
        return now - s.created > self.max_age or now - s.last_seen > self.idle

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text())
            now = self.clock()
            for key, v in data.get("sessions", {}).items():
                s = Session(str(v["username"]), float(v["created"]), float(v["last_seen"]))
                if not self._expired(s, now):
                    self.sessions[key] = s
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, KeyError, AttributeError):
            # A damaged file only costs a new login.
            self.sessions = {}

    def _save(self) -> None:
        if self.path is None:
            return
        data = {"sessions": {k: vars(s) for k, s in self.sessions.items()}}
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        tmp.replace(self.path)
        self.saved = self.clock()

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.lock:
            for k in [k for k, s in self.sessions.items() if self._expired(s, now)]:
                del self.sessions[k]
            self.sessions[self._key(token)] = Session(username, now, now)
            self._save()
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = self.clock()
        key = self._key(token)
        with self.lock:
            s = self.sessions.get(key)
            if s is None:
                return None
            if self._expired(s, now):
                del self.sessions[key]
                self._save()
                return None
            s.last_seen = now
            if now - self.saved >= self.SAVE_EVERY:
                self._save()
            return s

    def drop(self, token: str | None) -> None:
        with self.lock:
            if self.sessions.pop(self._key(token or ""), None) is not None:
                self._save()

    def drop_user(self, username: str, keep: str | None = None) -> None:
        keep_key = self._key(keep) if keep else None
        with self.lock:
            for k in [k for k, s in self.sessions.items()
                      if s.username == username and k != keep_key]:
                del self.sessions[k]
            self._save()


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
