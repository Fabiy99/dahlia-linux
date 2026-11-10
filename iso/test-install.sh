#!/bin/bash
# Install test: install the ISO into an empty QEMU disk, then boot the
# installed system and check banner, SSH, dashboard and certificates.
#
#   auto    "Automatic install" entry: no questions, initial password "dahlia"
#   guided  "Install Dahlia Linux" entry with every question answered by a
#           preseed file (tests guided.sh and apply-answers.sh, not the screens):
#           static address, SSH key, own server name and time zone
#
# Usage: iso/test-install.sh <bios|uefi> <path-to-iso> [auto|guided]
# Needs: qemu-system-x86_64, qemu-img, xorriso, sshpass, ssh-keygen, /dev/kvm;
#        for uefi also OVMF
# Logs:  build/test-<firmware>-<install>/install.log and boot.log
set -euo pipefail

usage="usage: $0 <bios|uefi> <iso> [auto|guided]"
MODE="${1:?$usage}"
ISO="${2:?$usage}"
INSTALL="${3:-auto}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
OUTDIR="$ROOT/build/test-$MODE-$INSTALL"

# Answers of the guided test. QEMU user networking: guest 10.0.2.15,
# gateway 10.0.2.2, DNS 10.0.2.3.
GUIDED_HOST=nas-test
GUIDED_PASS='Dahlia-Test-Pass-9'
GUIDED_TZ=Europe/Vienna

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
# serial-console entry of the tested mode is the default and d-i sends its
# syslog to the host (QEMU user networking maps 10.0.2.2 to the host's
# 127.0.0.1); everything else is the release ISO.
TEST_ISO="$OUTDIR/serial.iso"
SYSLOG_PORT=5140
extra="log_host=10.0.2.2 log_port=$SYSLOG_PORT"
map_answers=()
case "$INSTALL" in
    auto)
        grub_default=3
        syslinux_default=dahlia-auto-serial
        ;;
    guided)
        grub_default=1
        syslinux_default=dahlia-serial
        # The keyboard question comes before the CD (and the preseed file on
        # it) is read, so its answer goes on the kernel line.
        extra="$extra keymap=us preseed/file=/cdrom/dahlia/test-answers.cfg"
        ssh-keygen -q -t ed25519 -N '' -C dahlia-test -f "$OUTDIR/id_test"
        {
            echo "d-i passwd/user-password password $GUIDED_PASS"
            echo "d-i passwd/user-password-again password $GUIDED_PASS"
            echo "d-i dahlia/disk select /dev/vda"
            echo "d-i dahlia/hostname string $GUIDED_HOST"
            echo "d-i dahlia/timezone select $GUIDED_TZ"
            echo "d-i dahlia/network select static"
            echo "d-i dahlia/address string 10.0.2.15/24"
            echo "d-i dahlia/gateway string 10.0.2.2"
            echo "d-i dahlia/dns string 10.0.2.3"
            echo "d-i dahlia/ssh_key string $(cat "$OUTDIR/id_test.pub")"
            echo "d-i dahlia/confirm boolean true"
        } > "$OUTDIR/test-answers.cfg"
        map_answers=(-map "$OUTDIR/test-answers.cfg" /dahlia/test-answers.cfg)
        ;;
    *) echo "unknown install mode: $INSTALL" >&2; exit 2 ;;
esac
sed -e "s/^set default=0\$/set default=$grub_default/" -e "/vmlinuz/s| ---| $extra ---|" \
    "$ROOT/iso/grub.cfg" > "$OUTDIR/grub.cfg"
sed -e "s/^default dahlia\$/default $syslinux_default/" -e "/append/s| ---| $extra ---|" \
    "$ROOT/iso/isolinux.cfg" > "$OUTDIR/isolinux.cfg"
grep -q "^set default=$grub_default\$" "$OUTDIR/grub.cfg"
grep -q "^default $syslinux_default\$" "$OUTDIR/isolinux.cfg"
xorriso -indev "$ISO" -outdev "$TEST_ISO" \
    -map "$OUTDIR/grub.cfg" /boot/grub/grub.cfg \
    -map "$OUTDIR/isolinux.cfg" /isolinux/isolinux.cfg \
    "${map_answers[@]}" \
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

echo "== install ($MODE, $INSTALL)"
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

ssh_opts=(-p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o ConnectTimeout=10)
ssh_fail() {
    echo "FAIL: $*" >&2
    tail -n 40 "$OUTDIR/ssh.log" >&2 || true
    exit 1
}

if [ "$INSTALL" = auto ]; then
    HOST=dahlia
    # SSH with the initial password. It is expired on purpose, so a correct
    # password ends in the forced password change; a wrong one in "denied".
    echo "== ssh password login ($MODE)"
    ssh_ok=0
    for _ in $(seq 1 24); do
        out="$(timeout 30 sshpass -p dahlia ssh "${ssh_opts[@]}" -o PubkeyAuthentication=no \
            dahlia-admin@127.0.0.1 true 2>&1 || true)"
        printf '%s\n' "$out" >> "$OUTDIR/ssh.log"
        if grep -qi "password has expired\|change your password" <<<"$out"; then
            echo "OK: ssh accepts the initial password and forces a change"
            ssh_ok=1
            break
        fi
        if grep -qi "permission denied" <<<"$out"; then
            ssh_fail "ssh rejected the initial password"
        fi
        sleep 5
    done
    [ "$ssh_ok" = 1 ] || ssh_fail "no ssh answer on port $SSH_PORT"
else
    HOST="$GUIDED_HOST"
    # The answers from the preseed file must be in the installed system:
    # password (not expired), SSH key, server name, time zone, static address.
    echo "== ssh login with the guided answers ($MODE)"
    ssh_ok=0
    for _ in $(seq 1 24); do
        if out="$(timeout 30 sshpass -p "$GUIDED_PASS" ssh "${ssh_opts[@]}" \
                -o PubkeyAuthentication=no dahlia-admin@127.0.0.1 hostname 2>&1)"; then
            ssh_ok=1
            break
        fi
        printf '%s\n' "$out" >> "$OUTDIR/ssh.log"
        if grep -qi "permission denied" <<<"$out"; then
            ssh_fail "ssh rejected the guided password"
        fi
        sleep 5
    done
    [ "$ssh_ok" = 1 ] || ssh_fail "no ssh answer on port $SSH_PORT"
    [ "$(tail -n1 <<<"$out")" = "$GUIDED_HOST" ] || ssh_fail "host name is '$out'"
    echo "OK: password login, host name $GUIDED_HOST"
    facts="$(timeout 30 ssh "${ssh_opts[@]}" -i "$OUTDIR/id_test" -o BatchMode=yes \
        -o PasswordAuthentication=no dahlia-admin@127.0.0.1 \
        'cat /etc/timezone; grep -c "inet static" /etc/network/interfaces; cat /etc/resolv.conf' \
        2>>"$OUTDIR/ssh.log")" || ssh_fail "ssh key login failed"
    echo "$facts"
    grep -qx "$GUIDED_TZ" <<<"$facts" || ssh_fail "time zone not $GUIDED_TZ"
    grep -qx 1 <<<"$facts" || ssh_fail "no static address in /etc/network/interfaces"
    grep -q '^nameserver 10.0.2.3$' <<<"$facts" || ssh_fail "DNS server not set"
    echo "OK: SSH key login, time zone, static network"
fi

# Dashboard: nginx with TLS, setup wizard with the code from the console
# banner, login, and one call that goes through dahlia-agent.
echo "== dashboard ($MODE, $INSTALL)"
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
# Live values from /proc and statvfs on the real system: the root
# filesystem is btrfs and the CPU counters are filled.
stats="$(web "$BASE/api/stats")"
echo "$stats"
grep -q '"mountpoint":"/","source":"[^"]*","fstype":"btrfs"' <<<"$stats" \
    || dashboard_fail "system stats lack the root filesystem"
grep -qE '"cpu":\{"model":[^}]*"cores":[1-9]' <<<"$stats" || dashboard_fail "system stats lack the CPU"
# Hardware (lsblk and smartctl, a virtio disk has no SMART), Docker and journal.
web "$BASE/api/hardware" | grep -q '"path":"/dev/vda"' || dashboard_fail "hardware lacks the system disk"
web "$BASE/api/docker" | grep -q '"containers":' || dashboard_fail "docker list via dahlia-agent failed"
web "$BASE/api/log?lines=20" | grep -q '"lines":\["' || dashboard_fail "system log via dahlia-agent failed"
# Settings: time zones from timedatectl, the ifupdown interface, the timers.
web "$BASE/api/settings/time" | grep -q '"timezones":\["' || dashboard_fail "settings.time failed"
web "$BASE/api/settings/network" | grep -q '"iface":"' || dashboard_fail "settings.network failed"
web "$BASE/api/settings/schedules" | grep -q '"mover":{' || dashboard_fail "settings.schedules failed"
web -o /dev/null -w '%{content_type}' "$BASE/manifest.webmanifest" | grep -q 'application/manifest+json' \
    || dashboard_fail "web app manifest not served as application/manifest+json"
echo "OK: dashboard setup, login and agent calls work"

# Local CA: the server certificate must verify against the CA the
# dashboard hands out, under the mDNS name of the host.
echo "== certificates ($MODE, $INSTALL)"
web "$BASE/dahlia-ca.crt" -o "$OUTDIR/dahlia-ca.crt"
grep -q 'BEGIN CERTIFICATE' "$OUTDIR/dahlia-ca.crt" || dashboard_fail "CA not served"
curl -sf --max-time 30 --cacert "$OUTDIR/dahlia-ca.crt" \
    --resolve "$HOST.local:$HTTPS_PORT:127.0.0.1" \
    "https://$HOST.local:$HTTPS_PORT/api/state" >/dev/null \
    || dashboard_fail "server certificate does not verify against the local CA"
domains="$(web "$BASE/api/domains")"
echo "$domains"
grep -q '"domain":"dahlia.home.arpa"' <<<"$domains" || dashboard_fail "domains via dahlia-agent failed"
echo "OK: local CA and server certificate"
