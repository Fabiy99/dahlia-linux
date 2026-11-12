"""Remote syslog: send the system log to a syslog server (NAS, Graylog, ...).

journald keeps the local log. When forwarding is on, journald hands every
message to rsyslog (ForwardToSyslog=yes), and rsyslog sends it with omfwd
and then drops it ("stop"), so no second copy lands in /var/log. When it is
off, rsyslog is stopped and disabled.
"""

import json
import re

from .actions import Context, Param, action
from .protocol import FAILED, INVALID_PARAMS, AgentError
from .settings import read_json, write_file

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.:-]{0,252}[A-Za-z0-9])?$")
PROTOCOLS = ("udp", "tcp")
# Lowest severity that is sent; syslog names as rsyslog selectors use them.
SEVERITIES = ("debug", "info", "notice", "warning", "err")
DEFAULTS = {"enabled": False, "host": "", "port": 514, "protocol": "udp", "severity": "info"}


def config_file(ctx: Context):
    return ctx.etc / "dahlia/syslog.json"


def rsyslog_file(ctx: Context):
    return ctx.etc / "rsyslog.d/90-dahlia-remote.conf"


def journald_file(ctx: Context):
    return ctx.etc / "systemd/journald.conf.d/dahlia-remote-syslog.conf"


def load(ctx: Context) -> dict:
    data = read_json(config_file(ctx), DEFAULTS)
    return {k: data.get(k, v) if type(data.get(k, v)) is type(v) else v
            for k, v in DEFAULTS.items()}


def render(cfg: dict) -> str:
    return (
        "# Written by dahlia-agent (Settings > Remote syslog). Changes are overwritten.\n"
        f"*.{cfg['severity']} action(type=\"omfwd\" target=\"{cfg['host']}\" "
        f"port=\"{cfg['port']}\" protocol=\"{cfg['protocol']}\"\n"
        "    queue.type=\"LinkedList\" queue.size=\"10000\" action.resumeRetryCount=\"-1\")\n"
        "# journald keeps the local copy.\n"
        "stop\n"
    )


@action("settings.syslog", mutating=False)
def settings_syslog(ctx: Context):
    """Remote syslog settings and whether rsyslog runs."""
    active = ctx.runner.run(["systemctl", "is-active", "rsyslog.service"], check=False)
    return {**load(ctx), "running": (active.stdout or "").strip() == "active"}


@action(
    "settings.set_syslog",
    mutating=True,
    params={
        "enabled": Param(bool),
        "host": Param(str, required=False, default=""),
        "port": Param(int, required=False, default=514),
        "protocol": Param(str, required=False, default="udp", choices=PROTOCOLS),
        "severity": Param(str, required=False, default="info", choices=SEVERITIES),
    },
)
def settings_set_syslog(ctx: Context, enabled: bool, host: str, port: int, protocol: str,
                        severity: str):
    """Turn forwarding on (host, port, udp/tcp, lowest severity) or off."""
    if enabled and not HOST_RE.fullmatch(host):
        raise AgentError(INVALID_PARAMS, "host: a host name or IP address")
    if not 1 <= port <= 65535:
        raise AgentError(INVALID_PARAMS, "port: 1-65535")
    cfg = {"enabled": enabled, "host": host, "port": port, "protocol": protocol,
           "severity": severity}
    write_file(config_file(ctx), json.dumps(cfg, indent=2) + "\n")
    if enabled:
        write_file(rsyslog_file(ctx), render(cfg))
        write_file(journald_file(ctx), "# Written by dahlia-agent: remote syslog is on.\n"
                                       "[Journal]\nForwardToSyslog=yes\n")
        ctx.runner.run(["systemctl", "restart", "systemd-journald.service"], check=False)
        ctx.runner.run(["systemctl", "enable", "rsyslog.service"], check=False)
        proc = ctx.runner.run(["systemctl", "restart", "rsyslog.service"], check=False)
        if proc.returncode != 0:
            raise AgentError(FAILED, f"rsyslog: {(proc.stderr or proc.stdout or '').strip()}")
        ctx.runner.run(["logger", "-p", "user.notice", "-t", "dahlia",
                        f"remote syslog to {host}:{port}/{protocol} is on"], check=False)
    else:
        ctx.runner.run(["systemctl", "disable", "--now", "rsyslog.service"], check=False)
        rsyslog_file(ctx).unlink(missing_ok=True)
        if journald_file(ctx).exists():
            journald_file(ctx).unlink()
            ctx.runner.run(["systemctl", "restart", "systemd-journald.service"], check=False)
    return settings_syslog(ctx)
