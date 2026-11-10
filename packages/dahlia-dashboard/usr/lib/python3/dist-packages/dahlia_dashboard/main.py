"""Entry point: dahlia-dashboard [--prepare]."""

import argparse
from pathlib import Path

from . import setupcode
from .app import Config, create_app
from .auth import UserStore

HOST = "127.0.0.1"
PORT = 8080


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dahlia-dashboard", description="Dahlia web dashboard.")
    p.add_argument(
        "--prepare",
        action="store_true",
        help="write the setup code if no account exists yet, then exit",
    )
    args = p.parse_args(argv)
    cfg = Config()

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
