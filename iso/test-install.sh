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

INSTALL_TIMEOUT=3600
BOOT_TIMEOUT=600
SSH_PORT=2222

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
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22" -device virtio-net-pci,netdev=net0
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
# Echo the late-command and apt part of the syslog live, so it ends up in
# the job log even if the job is cancelled.
touch "$OUTDIR/installer-syslog.log"
tail -F "$OUTDIR/installer-syslog.log" 2>/dev/null \
    | grep --line-buffered -E 'in-target|preseed|late_command|dahlia|apt-get|dpkg|Setting up|Unpacking' \
    | sed -u 's/^/[d-i] /' &
tail_pid=$!
trap 'kill "$syslog_pid" "$tail_pid" 2>/dev/null || true' EXIT

echo "== install ($MODE)"
# d-i reboots at the end; -no-reboot turns that into a clean QEMU exit.
if ! timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 "${qemu_args[@]}" \
        -cdrom "$TEST_ISO" -boot d -no-reboot \
        -serial "file:$OUTDIR/install.log"; then
    echo "install did not finish within ${INSTALL_TIMEOUT}s or QEMU failed" >&2
    echo "--- installer syslog (last 150 lines)" >&2
    tail -n 150 "$OUTDIR/installer-syslog.log" >&2 || true
    exit 1
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
for _ in $(seq 1 24); do
    out="$(timeout 30 sshpass -p dahlia ssh -p "$SSH_PORT" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o PubkeyAuthentication=no -o ConnectTimeout=10 \
        dahlia-admin@127.0.0.1 true 2>&1 || true)"
    printf '%s\n' "$out" >> "$OUTDIR/ssh.log"
    if grep -qi "password has expired\|change your password" <<<"$out"; then
        echo "OK: ssh accepts the initial password and forces a change"
        exit 0
    fi
    if grep -qi "permission denied" <<<"$out"; then
        echo "FAIL: ssh rejected the initial password" >&2
        cat "$OUTDIR/ssh.log" >&2
        exit 1
    fi
    sleep 5
done
echo "FAIL: no ssh answer on port $SSH_PORT" >&2
tail -n 40 "$OUTDIR/ssh.log" >&2
exit 1
