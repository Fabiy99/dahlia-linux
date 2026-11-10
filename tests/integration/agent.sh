#!/bin/bash
# Integration test for dahlia-agent on a real systemd host.
#
# Checks socket activation, socket permissions, peer checks (root, group
# member, other user), parameter rejection, jobs and one real storage
# change (loop disk added and removed through the agent).
#
# Must run as root on a disposable machine (CI runner or test VM) with the
# dahlia-storage and dahlia-agent packages installed.
set -euo pipefail

SOCK=/run/dahlia/agent.sock
DROPIN=/run/systemd/system/dahlia-agent.service.d
WORK="$(mktemp -d /var/tmp/dahlia-agent-test.XXXXXX)"
LOOP=""

diag() {
    echo "--- systemctl list-jobs"
    systemctl list-jobs --no-pager || true
    echo "--- processes"
    ps -eo pid,ppid,stat,wchan:32,args --forest || true
}
fail() { echo "FAIL: $*" >&2; diag >&2; exit 1; }
step() { echo; echo "== $*"; }

cleanup() {
    set +e
    if [ -n "$LOOP" ]; then
        umount /mnt/disk1 2>/dev/null
        losetup -d "$LOOP"
    fi
    rm -rf "$DROPIN" "$WORK"
    systemctl daemon-reload
}
trap cleanup EXIT

call() { dahlia-agent-call --timeout 60 "$@"; }

# expect_error CODE ACTION [PARAMS]: the call must fail with this error code.
expect_error() {
    local code="$1" out
    shift
    if out="$(call "$@" 2>&1)"; then
        fail "$* succeeded, expected $code"
    fi
    grep -q ": $code:" <<<"$out" || fail "$* failed with '$out', expected $code"
}

step "socket unit and permissions"
systemctl is-active --quiet dahlia-agent.socket || fail "dahlia-agent.socket not active"
[ "$(stat -c '%U:%G %a' "$SOCK")" = "root:dahlia-agent 660" ] \
    || fail "socket has $(stat -c '%U:%G %a' "$SOCK")"

# Loop devices are hidden from dahlia-disk unless allowed.
mkdir -p "$DROPIN"
printf '[Service]\nEnvironment=DAHLIA_ALLOW_LOOP=1\n' > "$DROPIN/test.conf"
systemctl daemon-reload
systemctl stop dahlia-agent.service

step "socket activation and system.info as root"
info="$(call system.info)"
echo "$info"
systemctl is-active --quiet dahlia-agent.service || fail "service not started by the socket"
[ "$(jq -r .hostname <<<"$info")" = "$(hostname)" ] || fail "wrong hostname"
jq -e '.uptime_seconds > 0 and .memory.total > 0' <<<"$info" >/dev/null || fail "bad system.info"

step "other users are refused, group members are allowed"
if out="$(sudo -u nobody dahlia-agent-call system.info 2>&1)"; then
    fail "nobody could use the agent"
fi
grep -q forbidden <<<"$out" || fail "unexpected error for nobody: $out"
sudo -u nobody -g dahlia-agent dahlia-agent-call job.status '{"job": "mover"}' >/dev/null \
    || fail "group member was refused"

step "fixed action list and parameter checks"
call agent.actions | jq -e 'map(.name) | index("storage.add")' >/dev/null
expect_error unknown_action system.shell '{"cmd": "id"}'
expect_error invalid_params storage.add '{"uuid": "x; reboot", "role": "data"}'
expect_error invalid_params storage.add '{"uuid": "1234-ABCD", "role": "data", "format": true}'
expect_error invalid_params job.start '{"job": "update"}'
expect_error invalid_params system.set_hostname '{"hostname": "bad.name"}'
journalctl -u dahlia-agent.service --no-pager | grep -q "action=system.shell unknown_action" \
    || fail "refused action not logged"

step "job.start and job.status"
call job.start '{"job": "mover"}' >/dev/null
for _ in $(seq 1 30); do
    state="$(call job.status '{"job": "mover"}')"
    [ "$(jq -r .active <<<"$state")" = "inactive" ] && break
    sleep 1
done
echo "$state"
jq -e '.result == "success"' <<<"$state" >/dev/null || fail "mover job did not succeed"

step "storage.add and storage.remove through the agent"
truncate -s 256M "$WORK/disk1.img"
LOOP="$(losetup --find --show "$WORK/disk1.img")"
mkfs.ext4 -q -L agentdisk "$LOOP"
udevadm settle
uuid="$(blkid -s UUID -o value "$LOOP")"
call storage.list | jq -e --arg u "$uuid" '.disks | map(.uuid) | index($u)' >/dev/null \
    || fail "loop disk not in storage.list"
call storage.add "{\"uuid\": \"$uuid\", \"role\": \"data\"}" >/dev/null \
    || fail "storage.add through the agent failed"
findmnt -n /mnt/disk1 >/dev/null || fail "/mnt/disk1 not mounted after storage.add"
findmnt -n /mnt/storage >/dev/null || fail "/mnt/storage not mounted after storage.add"
expect_error failed storage.add "{\"uuid\": \"$uuid\", \"role\": \"data\"}"
call storage.set_option '{"name": "scrub_percent", "value": 12}' >/dev/null
jq -e '.settings.scrub_percent == 12' /etc/dahlia/storage.json >/dev/null \
    || fail "storage.set_option not saved"
call storage.remove "{\"uuid\": \"$uuid\"}" >/dev/null
if findmnt -n /mnt/disk1 >/dev/null; then fail "/mnt/disk1 still mounted"; fi

echo
echo "agent integration test passed"
