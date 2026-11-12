"""Dashboard accounts, sessions and login rate limiting.

Accounts are separate from Linux users. They live in users.json in the state
directory (owner dahlia-ui, mode 0600) with scrypt password hashes.
Sessions are random tokens; sessions.json keeps their hashes over a restart.

Second factor: TOTP (RFC 6238, SHA-1, 6 digits, 30 s), as every
authenticator app understands it. The secret is stored in users.json next to
the password hash; a code is accepted once (the last used time step is kept)
and one step early or late. Ten recovery codes, stored as SHA-256 hashes
(they are random, so a slow hash adds nothing), each work once.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
MIN_PASSWORD = 10
MAX_PASSWORD = 1024

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


TOTP_STEP = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
RECOVERY_CODES = 10
CODE_RE = re.compile(r"^\d{6}$")
RECOVERY_RE = re.compile(r"^[a-z0-9]{4}-?[a-z0-9]{4}$")
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class AuthError(Exception):
    pass


# --- TOTP ---------------------------------------------------------------------


def new_totp_secret() -> str:
    """160 random bits, base32 without padding (what the apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def totp_step(now: float) -> int:
    return int(now // TOTP_STEP)


def totp_match(secret: str, code: str, now: float, after: int = -1) -> int | None:
    """The time step the code belongs to, if valid and newer than `after`."""
    if not CODE_RE.fullmatch(code):
        return None
    step = totp_step(now)
    for s in range(step - TOTP_WINDOW, step + TOTP_WINDOW + 1):
        if s > after and hmac.compare_digest(hotp(secret, s), code):
            return s
    return None


def totp_uri(secret: str, username: str, issuer: str) -> str:
    label = quote(f"{issuer}:{username}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP}")


def new_recovery_codes() -> list[str]:
    def one() -> str:
        c = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(8))
        return f"{c[:4]}-{c[4:]}"

    return [one() for _ in range(RECOVERY_CODES)]


def recovery_hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()


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
    def __init__(self, path: Path, clock=time.time):
        self.path = path
        self.clock = clock
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

    def totp_enabled(self, username: str) -> bool:
        user = self._load()["users"].get(username)
        return bool(user and user.get("totp"))

    def totp_status(self, username: str) -> dict:
        user = self._load()["users"].get(username) or {}
        return {"enabled": bool(user.get("totp")),
                "recovery_left": len(user.get("recovery") or [])}

    def check_second_factor(self, username: str, code: str) -> bool:
        """A TOTP code (once per time step) or an unused recovery code."""
        code = code.strip().replace(" ", "")
        with self.lock:
            data = self._load()
            user = data["users"].get(username)
            if not user or not user.get("totp"):
                return False
            step = totp_match(user["totp"], code, self.clock(), user.get("totp_last", -1))
            if step is not None:
                user["totp_last"] = step
                self._save(data)
                return True
            if RECOVERY_RE.fullmatch(code.lower()):
                h = recovery_hash(code)
                left = user.get("recovery") or []
                if any(hmac.compare_digest(h, x) for x in left):
                    user["recovery"] = [x for x in left if x != h]
                    self._save(data)
                    return True
            return False

    def totp_begin(self, username: str) -> str:
        """A new secret, pending until confirmed with a code from it."""
        secret = new_totp_secret()
        with self.lock:
            data = self._load()
            data["users"][username]["totp_pending"] = secret
            self._save(data)
        return secret

    def totp_confirm(self, username: str, code: str) -> list[str]:
        """Turn TOTP on; returns the recovery codes (shown once)."""
        with self.lock:
            data = self._load()
            user = data["users"][username]
            secret = user.get("totp_pending")
            if not secret:
                raise AuthError("totp_not_started")
            step = totp_match(secret, code.strip().replace(" ", ""), self.clock())
            if step is None:
                raise AuthError("totp_code")
            codes = new_recovery_codes()
            user.pop("totp_pending")
            user.update(totp=secret, totp_last=step, recovery=[recovery_hash(c) for c in codes])
            self._save(data)
        return codes

    def totp_disable(self, username: str) -> None:
        with self.lock:
            data = self._load()
            user = data["users"].get(username)
            if user is None:
                raise AuthError("unknown_user")
            for key in ("totp", "totp_last", "totp_pending", "recovery"):
                user.pop(key, None)
            self._save(data)

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


TOKEN_PREFIX = "dahlia_"  # noqa: S105 (not a secret, marks Dahlia tokens)
TOKEN_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,64}$")
MAX_TOKENS = 20


class TokenStore:
    """API tokens for read-only access from other programs (Home Assistant,
    Homepage, scripts), sent as "Authorization: Bearer <token>".

    tokens.json keeps SHA-256 hashes only (the tokens are random, so a slow
    hash adds nothing); a token is shown once when it is created.
    last_used is written at most every SAVE_EVERY seconds.
    """

    SAVE_EVERY = 60

    def __init__(self, path: Path, clock=time.time):
        self.path = path
        self.clock = clock
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data.get("tokens"), list) else {"tokens": []}
        except FileNotFoundError:
            return {"tokens": []}
        except (ValueError, AttributeError):
            return {"tokens": []}

    def _save(self, data: dict) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self.path)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def list(self) -> list[dict]:
        return [{k: t.get(k) for k in ("id", "name", "created", "last_used")}
                for t in self._load()["tokens"]]

    def create(self, name: str) -> tuple[dict, str]:
        name = name.strip()
        if not TOKEN_NAME_RE.match(name):
            raise AuthError("token_name")
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        entry = {"id": secrets.token_hex(6), "name": name, "hash": self._hash(token),
                 "created": int(self.clock()), "last_used": None}
        with self.lock:
            data = self._load()
            if len(data["tokens"]) >= MAX_TOKENS:
                raise AuthError("token_limit")
            data["tokens"].append(entry)
            self._save(data)
        return {k: entry[k] for k in ("id", "name", "created", "last_used")}, token

    def delete(self, token_id: str) -> bool:
        with self.lock:
            data = self._load()
            kept = [t for t in data["tokens"] if t.get("id") != token_id]
            if len(kept) == len(data["tokens"]):
                return False
            data["tokens"] = kept
            self._save(data)
            return True

    def verify(self, token: str | None) -> dict | None:
        if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
            return None
        h = self._hash(token)
        with self.lock:
            data = self._load()
            found = None
            for t in data["tokens"]:
                if hmac.compare_digest(str(t.get("hash", "")), h):
                    found = t
            if found is None:
                return None
            now = int(self.clock())
            if not found.get("last_used") or now - found["last_used"] >= self.SAVE_EVERY:
                found["last_used"] = now
                self._save(data)
            return {"id": found["id"], "name": found["name"]}
