"""Entry point: dashboard [--prepare]."""

import argparse
import os
import sys
from pathlib import Path

from . import setupcode
from .app import Config, create_app
from .auth import UserStore

HOST = "127.0.0.1"
PORT = 8080


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dashboard", description="Dahlia web dashboard.")
    p.add_argument(
        "--prepare",
        action="store_true",
        help="write the setup code if no account exists yet, then exit",
    )
    p.add_argument(
        "--disable-totp",
        metavar="USER",
        help="turn off the second factor of a dashboard account (lost phone), then exit",
    )
    args = p.parse_args(argv)
    cfg = Config()

    if args.disable_totp:
        return disable_totp(Path(cfg.state_dir), args.disable_totp)

    if args.prepare:
        if UserStore(Path(cfg.state_dir) / "users.json").has_users():
            setupcode.clear(cfg.run_dir)
        else:
            setupcode.prepare(cfg.run_dir)
        return 0

    import uvicorn

    uvicorn.run(
        create_app(cfg),
        host=HOST,
        port=PORT,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        server_header=False,
        access_log=False,
    )
    return 0


def disable_totp(state_dir: Path, username: str) -> int:
    from .auth import AuthError

    path = state_dir / "users.json"
    store = UserStore(path)
    try:
        store.totp_disable(username)
    except AuthError:
        print(f"dashboard: no account '{username}'", file=sys.stderr)
        return 1
    # Run as root: the file must stay readable for the service user.
    st = state_dir.stat()
    os.chown(path, st.st_uid, st.st_gid)
    print(f"Second factor of '{username}' is off.")
    return 0
