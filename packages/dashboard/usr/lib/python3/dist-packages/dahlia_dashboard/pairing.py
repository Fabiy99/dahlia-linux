"""Pairing codes for the desktop app.

When the desktop app lost its connection (new server certificate after a
reinstall, another address), it pairs again without comparing the long CA
fingerprint: the dashboard, usually opened on the phone, shows a short code.
The app sends a random nonce; the server answers with
HMAC-SHA256(code, PREFIX + nonce + sha256(CA)). The app computes the same over
the CA it downloaded. A machine in between that hands out its own CA cannot
produce a matching answer without the code, and the code never crosses the
network. A code answers once and expires after TTL seconds.
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable

# 32 symbols without 0/O and 1/I; 10 symbols are 50 bits.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LENGTH = 10
TTL = 10 * 60
PREFIX = b"dahlia-pairing\0"
NONCE_BYTES = 32


def normalize(code: str) -> str:
    return "".join(c for c in code.upper() if c not in "- ")


def display(code: str) -> str:
    return f"{code[:5]}-{code[5:]}"


def proof(code: str, nonce: bytes, ca_der: bytes) -> str:
    msg = PREFIX + nonce + hashlib.sha256(ca_der).digest()
    return hmac.new(normalize(code).encode(), msg, hashlib.sha256).hexdigest()


class Codes:
    """At most one code at a time, kept in memory only."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.code: str | None = None
        self.expires = 0.0

    def create(self) -> str:
        code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        with self.lock:
            self.code = code
            self.expires = self.clock() + TTL
        return code

    def take(self) -> str | None:
        """The active code, which is used up by this call."""
        with self.lock:
            code, self.code = self.code, None
            return code if code and self.clock() < self.expires else None
