"""One-time code that protects the setup wizard.

While no dashboard account exists, `dashboard --prepare` (ExecStartPre)
writes a fresh code to the runtime directory. It is shown on tty1 through
/run/issue.d (symlink from tmpfiles.d) and in the login banner. The wizard
only accepts it once; afterwards the files are removed.

An installer that set the code in advance (Unraid migration helper,
headless install from the USB Creator) writes it to PRESET_FILE in the state
directory; prepare() then uses that code instead of a random one, so the
person already knows it without reading a screen.
"""

import hmac
import re
import os
import secrets
from pathlib import Path

# No 0/O, 1/I/L: the code is read off a screen and typed in.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_FILE = "setup-code"
ISSUE_FILE = "setup.issue"
PRESET_FILE = "setup-code.preset"
CODE_RE = re.compile(r"^[2-9A-HJKMNP-Z]{5}-[2-9A-HJKMNP-Z]{5}$")


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def normalize(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def _write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def preset(state_dir: Path | None) -> str | None:
    """The code an installer set in advance, if it is well-formed."""
    if state_dir is None:
        return None
    try:
        code = (state_dir / PRESET_FILE).read_text().strip().upper()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return None
    return code if CODE_RE.match(code) else None


def prepare(run_dir: Path, state_dir: Path | None = None) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    code = preset(state_dir) or new_code()
    _write(run_dir / CODE_FILE, code + "\n")
    # agetty expands \4 to the IPv4 address.
    _write(
        run_dir / ISSUE_FILE,
        f"Dahlia setup: open https://\\4/ and enter the code {code}\n\n",
    )
    return code


def current(run_dir: Path) -> str | None:
    try:
        return (run_dir / CODE_FILE).read_text().strip() or None
    except FileNotFoundError:
        return None


def check(run_dir: Path, code: str) -> bool:
    expected = current(run_dir)
    if expected is None:
        return False
    return hmac.compare_digest(normalize(expected).encode(), normalize(code).encode())


def clear(run_dir: Path, state_dir: Path | None = None) -> None:
    for name in (CODE_FILE, ISSUE_FILE):
        (run_dir / name).unlink(missing_ok=True)
    if state_dir is not None:
        (state_dir / PRESET_FILE).unlink(missing_ok=True)
