#!/bin/sh
# Tells the Dahlia app on the PC how far a headless install is (headless.sh).
# Usage: report.sh <step> [fingerprint]. Does nothing in other installs and
# never fails the install: the app only shows the progress.
step="$1"
fp="${2:-}"
pc="$(cat /tmp/dahlia-headless-pc 2>/dev/null || true)"
token="$(cat /tmp/dahlia-headless-token 2>/dev/null || true)"
[ -n "$pc" ] && [ -n "$token" ] || exit 0
case "$step$fp" in
    *[!a-z0-9_]*) exit 0 ;;
esac
url="http://$pc/headless/$token/progress?step=$step"
[ -n "$fp" ] && url="$url&fingerprint=$fp"
wget -q -T 10 -O /dev/null "$url" || logger -t dahlia-headless "progress report failed: $step"
exit 0
