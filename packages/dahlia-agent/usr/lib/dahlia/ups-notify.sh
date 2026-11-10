#!/bin/bash
# NOTIFYCMD of upsmon: it passes the message as argument, the event in NOTIFYTYPE.
set -euo pipefail
exec /usr/sbin/dahlia-notify ups-event "${1:-}"
