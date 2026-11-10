#!/bin/bash
# Unattended install test: install the ISO into an empty QEMU disk, then boot
# the installed system and wait for the Dahlia login banner on the serial port.
#
# Usage: iso/test-install.sh <bios|uefi> <path-to-iso>
# Needs: qemu-system-x86_64, qemu-img, xorriso, sshpass, /dev/kvm; for uefi also OVMF
# Logs:  build/test-<mode>/install.log and boot.log
set -euo pipefail

MODE="${1:?usage: $0 <bios|uefi> <iso>}"
ISO="${2:?usage: $0 <bios|uefi> <iso>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
OUTDIR="$ROOT/build/test-$MODE"

INSTALL_TIMEOUT=1800
BOOT_TIMEOUT=600
SSH_PORT=2222
HTTPS_PORT=8443

OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd

rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"
qemu-img create -q -f qcow2 "$OUTDIR/disk.qcow2" 20G

# shellcheck disable=SC2054 # commas are QEMU option syntax
qemu_args=(
    -enable-kvm -cpu host -m 2048 -smp 2
    -display none
    -drive "file=$OUTDIR/disk.qcow2,if=virtio,format=qcow2"
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22,hostfwd=tcp:127.0.0.1:$HTTPS_PORT-:443" -device virtio-net-pci,netdev=net0
)
case "$MODE" in
    bios) ;;
    uefi)
        cp "$OVMF_VARS" "$OUTDIR/OVMF_VARS.fd"
        qemu_args+=(
            -machine q35
            -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
            -drive "if=pflash,format=raw,file=$OUTDIR/OVMF_VARS.fd"
        )
        ;;
    *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac

# The default menu entries use the screen only. Remap both boot menus so the
# serial-console entry is the default and d-i sends its syslog to the host
# (QEMU user networking maps 10.0.2.2 to the host's 127.0.0.1); everything
# else is the release ISO.
TEST_ISO="$OUTDIR/serial.iso"
SYSLOG_PORT=5140
log_opts="priority=critical log_host=10.0.2.2 log_port=$SYSLOG_PORT ---"
sed -e 's/^set default=0$/set default=1/' -e "s/priority=critical ---/$log_opts/" \
    "$ROOT/iso/grub.cfg" > "$OUTDIR/grub.cfg"
sed -e 's/^default dahlia$/default dahlia-serial/' -e "s/priority=critical ---/$log_opts/" \
    "$ROOT/iso/isolinux.cfg" > "$OUTDIR/isolinux.cfg"
grep -q '^set default=1$' "$OUTDIR/grub.cfg"
grep -q '^default dahlia-serial$' "$OUTDIR/isolinux.cfg"
xorriso -indev "$ISO" -outdev "$TEST_ISO" \
    -map "$OUTDIR/grub.cfg" /boot/grub/grub.cfg \
    -map "$OUTDIR/isolinux.cfg" /isolinux/isolinux.cfg \
    -boot_image any replay 2>"$OUTDIR/xorriso.log"

# Collect the installer syslog: the serial console only shows the dialog
# screen, the syslog shows what late_command and apt are doing.
python3 -I -c '
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("127.0.0.1", int(sys.argv[2])))
with open(sys.argv[1], "ab", buffering=0) as f:
    while True:
        f.write(s.recv(65535).rstrip(b"\n") + b"\n")
' "$OUTDIR/installer-syslog.log" "$SYSLOG_PORT" &
syslog_pid=$!
trap 'kill "$syslog_pid" 2>/dev/null || true' EXIT

install_failed() {
    echo "$1" >&2
    echo "--- installer syslog (last 150 lines)" >&2
    tail -n 150 "$OUTDIR/installer-syslog.log" >&2 || true
    # The serial console shows the dialog d-i is waiting on, if any.
    echo "--- installer screen (serial, last 40 lines)" >&2
    sed -e 's/\x1b\[[0-9;?]*[A-Za-z]//g' -e 's/\x1b[()][A-Z0-9]//g' "$OUTDIR/install.log" \
        | tr -s ' ' | grep -av '^ *$' | tail -n 40 >&2 || true
    exit 1
}

echo "== install ($MODE)"
# d-i reboots at the end; -no-reboot turns that into a clean QEMU exit.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 "${qemu_args[@]}" \
    -cdrom "$TEST_ISO" -boot d -no-reboot \
    -serial "file:$OUTDIR/install.log" &
install_pid=$!
trap 'kill "$syslog_pid" "$install_pid" 2>/dev/null || true' EXIT
# A failed late_command leaves d-i waiting at an error dialog: stop early.
while kill -0 "$install_pid" 2>/dev/null; do
    if grep -q "dahlia-install: FAILED" "$OUTDIR/installer-syslog.log" 2>/dev/null; then
        sleep 5 # let the rest of the diagnostics arrive
        kill "$install_pid" 2>/dev/null || true
        install_failed "late_command failed"
    fi
    sleep 5
done
if ! wait "$install_pid"; then
    install_failed "install did not finish within ${INSTALL_TIMEOUT}s or QEMU failed"
fi
kill "$syslog_pid" 2>/dev/null || true

echo "== first boot ($MODE)"
qemu-system-x86_64 "${qemu_args[@]}" -boot c \
    -serial "file:$OUTDIR/boot.log" &
qemu_pid=$!
trap 'kill "$qemu_pid" 2>/dev/null || true' EXIT

deadline=$((SECONDS + BOOT_TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
    if grep -q "Dahlia Linux $VERSION" "$OUTDIR/boot.log" 2>/dev/null \
            && grep -q "login:" "$OUTDIR/boot.log"; then
        echo "OK: installed system booted and shows the Dahlia banner"
        grep -a "Dahlia Linux $VERSION" "$OUTDIR/boot.log" | head -n1
        break
    fi
    if ! kill -0 "$qemu_pid" 2>/dev/null; then
        echo "QEMU exited before the login prompt appeared" >&2
        break
    fi
    sleep 5
done

if ! grep -q "login:" "$OUTDIR/boot.log" 2>/dev/null; then
    echo "FAIL: no Dahlia banner/login prompt within ${BOOT_TIMEOUT}s" >&2
    tail -n 80 "$OUTDIR/boot.log" >&2 || true
    exit 1
fi

# SSH with the initial password. It is expired on purpose, so a correct
# password ends in the forced password change; a wrong one in "denied".
echo "== ssh password login ($MODE)"
ssh_ok=0
for _ in $(seq 1 24); do
    out="$(timeout 30 sshpass -p dahlia ssh -p "$SSH_PORT" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o PubkeyAuthentication=no -o ConnectTimeout=10 \
        dahlia-admin@127.0.0.1 true 2>&1 || true)"
    printf '%s\n' "$out" >> "$OUTDIR/ssh.log"
    if grep -qi "password has expired\|change your password" <<<"$out"; then
        echo "OK: ssh accepts the initial password and forces a change"
        ssh_ok=1
        break
    fi
    if grep -qi "permission denied" <<<"$out"; then
        echo "FAIL: ssh rejected the initial password" >&2
        cat "$OUTDIR/ssh.log" >&2
        exit 1
    fi
    sleep 5
done
if [ "$ssh_ok" != 1 ]; then
    echo "FAIL: no ssh answer on port $SSH_PORT" >&2
    tail -n 40 "$OUTDIR/ssh.log" >&2
    exit 1
fi

# Dashboard: nginx with TLS, setup wizard with the code from the console
# banner, login, and one call that goes through dahlia-agent.
echo "== dashboard ($MODE)"
BASE="https://127.0.0.1:$HTTPS_PORT"
JAR="$OUTDIR/cookies.txt"
web() { curl -sk --max-time 30 -b "$JAR" -c "$JAR" -H 'X-Dahlia-Request: 1' "$@"; }
dashboard_fail() {
    echo "FAIL: $*" >&2
    tail -n 30 "$OUTDIR/boot.log" | sed 's/\x1b\[[0-9;?]*[A-Za-z]//g' >&2 || true
    exit 1
}

for _ in $(seq 1 24); do
    web "$BASE/" -o "$OUTDIR/index.html" && grep -q 'app.js' "$OUTDIR/index.html" && break
    sleep 5
done
grep -q 'app.js' "$OUTDIR/index.html" 2>/dev/null || dashboard_fail "no dashboard page on $BASE"
web "$BASE/api/state" | grep -q '"setup_required":true' || dashboard_fail "setup not offered"

code="$(grep -aoE 'enter the code [2-9A-Z]{5}-[2-9A-Z]{5}' "$OUTDIR/boot.log" \
    | tail -n1 | awk '{print $4}')"
[ -n "$code" ] || dashboard_fail "setup code not shown on the console"
echo "setup code from console: $code"

if web "$BASE/api/setup" -H 'Content-Type: application/json' \
        -d '{"code": "WRONG-CODE0", "username": "admin", "password": "dahlia-test-pass"}' \
        | grep -q '"user"'; then
    dashboard_fail "setup accepted a wrong code"
fi
web "$BASE/api/setup" -H 'Content-Type: application/json' \
    -d "{\"code\": \"$code\", \"username\": \"admin\", \"password\": \"dahlia-test-pass\"}" \
    | grep -q '"user":"admin"' || dashboard_fail "setup with the console code failed"

info="$(web "$BASE/api/system")"
echo "$info"
grep -q "\"version\":\"$VERSION\"" <<<"$info" || dashboard_fail "system info via dahlia-agent failed"
web "$BASE/api/jobs" | grep -q '"mover"' || dashboard_fail "job status via dahlia-agent failed"
echo "OK: dashboard setup, login and agent calls work"
