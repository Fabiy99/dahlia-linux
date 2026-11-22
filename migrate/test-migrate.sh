#!/bin/bash
# Migration helper test: build the container, let it install Dahlia onto a
# loop-device disk through its web API (as the browser would), with a fake
# Unraid configuration, then boot the disk in QEMU (UEFI and BIOS) and check
# SSH login, host name, the copied Unraid settings and the preset setup code.
#
# Usage: migrate/test-migrate.sh   (as root, on a host with docker and KVM)
# Needs: docker, qemu-system-x86_64, ovmf, sshpass, curl, gpg, apt-utils
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/test-migrate"
PORT=8484
REPO_PORT=8000
ACCOUNT=fabi
PASSWORD='Dahlia-Migrate-9'
HOST=tower-test
SSH_PORT=2223
HTTPS_PORT=8444

rm -rf "$OUT"
mkdir -p "$OUT"

cleanup() {
    docker rm -f dahlia-migrate-test >/dev/null 2>&1 || true
    if [ -n "${repo_pid:-}" ]; then kill "$repo_pid" 2>/dev/null || true; fi
    if [ -n "${qemu_pid:-}" ]; then kill "$qemu_pid" 2>/dev/null || true; fi
    if [ -n "${LOOP:-}" ]; then losetup -d "$LOOP" 2>/dev/null || true; fi
}
trap cleanup EXIT

echo "== packages and a repository signed with a throwaway key"
"$ROOT/tools/build-packages.sh" >/dev/null
export GNUPGHOME="$OUT/gpg"
mkdir -p "$GNUPGHOME" && chmod 700 "$GNUPGHOME"
gpg --batch --quiet --passphrase '' --quick-gen-key "Dahlia test <test@invalid>" ed25519 sign 1d
gpg --batch --armor --export > "$OUT/test.asc"
DAHLIA_SIGNING_KEY="$(gpg --batch --armor --export-secret-keys)" DAHLIA_KEYRING="$OUT/test.asc" \
    "$ROOT/tools/sign-repo.sh" >/dev/null
(cd "$ROOT/build/repo" && exec python3 -m http.server "$REPO_PORT" --bind 127.0.0.1) \
    >"$OUT/repo.log" 2>&1 &
repo_pid=$!

echo "== fake Unraid configuration"
U="$OUT/unraid"
mkdir -p "$U/config/shares" "$U/config/plugins/dockerMan/templates-user" "$U/config/wireguard" \
    "$U/emhttp"
printf 'NAME="Tower-Test"\ntimeZone="Europe/Vienna"\n' > "$U/config/ident.cfg"
# QEMU user networking: guest 10.0.2.15, gateway 10.0.2.2, DNS 10.0.2.3.
cat > "$U/config/network.cfg" <<'NET'
USE_DHCP[0]="no"
IPADDR[0]="10.0.2.15"
NETMASK[0]="255.255.255.0"
GATEWAY[0]="10.0.2.2"
DNS_SERVER1="10.0.2.3"
NET
printf 'shareComment="Filme"\nshareExport="e"\nshareSecurity="private"\nshareUseCache="yes"\nshareWriteList="anna"\n' \
    > "$U/config/shares/media.cfg"
printf 'root:x:0:0:Console and webGui login account:/root:/bin/bash\nanna:x:1000:100::/:/bin/false\n' \
    > "$U/config/passwd"
printf 'anna:1000:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX:8846F7EAEE8FB117AD06BDD830B7586C:[U          ]:LCT-00000000:\n' \
    > "$U/config/smbpasswd"
cat > "$U/config/plugins/dockerMan/templates-user/my-jellyfin.xml" <<'XML'
<?xml version="1.0"?>
<Container version="2"><Name>jellyfin</Name><Repository>jellyfin/jellyfin:10.10.3</Repository>
<Network>bridge</Network>
<Config Name="Web" Target="8096" Default="8096" Mode="tcp" Type="Port">8096</Config>
<Config Name="Config" Target="/config" Mode="rw" Type="Path">/mnt/user/appdata/jellyfin</Config>
</Container>
XML
printf '[Interface]\nPrivateKey=test\n' > "$U/config/wireguard/wg0.conf"
printf '["parity"]\nname="parity"\ndevice=""\ntype="Parity"\n' > "$U/emhttp/disks.ini"

echo "== target disk"
truncate -s 24G "$OUT/disk.img"
LOOP="$(losetup -fP --show "$OUT/disk.img")"
echo "loop device: $LOOP"

echo "== helper container"
docker build -q -f "$ROOT/migrate/Dockerfile" -t dahlia-migrate-test "$ROOT" >/dev/null
docker run -d --name dahlia-migrate-test --privileged --network host -v /dev:/dev \
    -v "$U/config:/unraid/config:ro" -v "$U/emhttp:/unraid/emhttp:ro" \
    -v "$OUT/test.asc:/keys/test.asc:ro" \
    -e ALLOW_LOOP=1 -e PORT="$PORT" -e DAHLIA_KEYRING=/keys/test.asc \
    -e DAHLIA_REPO="http://127.0.0.1:$REPO_PORT/" dahlia-migrate-test >/dev/null
code=""
for _ in $(seq 1 30); do
    code="$(docker logs dahlia-migrate-test 2>&1 | sed -n 's/.*access code: //p')"
    [ -n "$code" ] && break
    sleep 1
done
[ -n "$code" ] || { docker logs dahlia-migrate-test; echo "FAIL: no access code" >&2; exit 1; }
api() { curl -sf -H "X-Access-Code: $code" -H 'Content-Type: application/json' "$@"; }

state="$(api "http://127.0.0.1:$PORT/api/state")"
python3 - "$state" "$LOOP" <<'PY'
import json, sys
s = json.loads(sys.argv[1])
u = s["unraid"]
assert u["found"] and u["identity"] == {"hostname": "tower-test", "timezone": "Europe/Vienna"}, u
assert u["network"]["address"] == "10.0.2.15/24", u["network"]
assert u["containers"] == ["jellyfin"] and u["users"] == ["anna"], u
disk = [d for d in s["disks"] if d["path"] == sys.argv[2]]
assert disk and not disk[0]["lock"], s["disks"]
print("OK: helper reads the Unraid settings and offers the disk")
PY
curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/state" | grep -qx 401 \
    || { echo "FAIL: API answers without the access code" >&2; exit 1; }

# The guest gets the host's MAC so the pinned interface name applies.
NET="$(api "http://127.0.0.1:$PORT/api/state" | python3 -c 'import json,sys; n=json.load(sys.stdin)["nic"]; print(n["mac"], n["name"])')"
MAC="${NET% *}"
echo "network: $NET"

body="$(python3 -c 'import json,sys; print(json.dumps({"disk": sys.argv[1], "hostname": sys.argv[2], "account": sys.argv[3], "password": sys.argv[4], "timezone": "Europe/Vienna", "keymap": "de", "network": "unraid"}))' \
    "$LOOP" "$HOST" "$ACCOUNT" "$PASSWORD")"
setup="$(api -X POST -d "$body" "http://127.0.0.1:$PORT/api/install" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["setup_code"])')"
echo "setup code: $setup"

# debootstrap, kernel, firmware and the Dahlia dependencies come from the
# Debian mirror: on a CI runner that takes well over 20 minutes.
started=$SECONDS
last=""
for _ in $(seq 1 480); do
    job="$(api "http://127.0.0.1:$PORT/api/state" | python3 -c 'import json,sys; j=json.load(sys.stdin)["job"]; print(j["state"], j["step"])')"
    [ "$job" = "$last" ] || { echo "  $(( SECONDS - started ))s: $job"; last="$job"; }
    case "$job" in
        done*) break ;;
        failed*)
            api "http://127.0.0.1:$PORT/api/state" | python3 -c 'import json,sys; j=json.load(sys.stdin)["job"]; print(j["error"]); print("\n".join(j["log"]))' >&2
            echo "FAIL: install failed" >&2
            exit 1 ;;
    esac
    sleep 5
done
if [[ "$job" != done* ]]; then
    api "http://127.0.0.1:$PORT/api/state" | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["job"]["log"][-60:]))' >&2 || true
    echo "FAIL: install did not finish ($job)" >&2
    exit 1
fi
echo "OK: helper installed Dahlia"
docker rm -f dahlia-migrate-test >/dev/null
losetup -d "$LOOP"
LOOP=""

ssh_opts=(-p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o ConnectTimeout=10 -o PubkeyAuthentication=no)
boot_check() { # <bios|uefi>
    local fw="$1" args=()
    # shellcheck disable=SC2054 # commas are QEMU option syntax
    args=(-enable-kvm -cpu host -m 2048 -smp 2 -display none
          -drive "file=$OUT/disk.img,if=virtio,format=raw"
          -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22,hostfwd=tcp:127.0.0.1:$HTTPS_PORT-:443"
          -device "virtio-net-pci,netdev=n0,mac=$MAC"
          -serial "file:$OUT/boot-$fw.log")
    if [ "$fw" = uefi ]; then
        cp /usr/share/OVMF/OVMF_VARS_4M.fd "$OUT/vars.fd"
        args+=(-machine q35 -drive "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd"
               -drive "if=pflash,format=raw,file=$OUT/vars.fd")
    fi
    echo "== boot ($fw)"
    qemu-system-x86_64 "${args[@]}" &
    qemu_pid=$!
    local out=""
    for _ in $(seq 1 60); do
        if out="$(timeout 30 sshpass -p "$PASSWORD" ssh "${ssh_opts[@]}" "$ACCOUNT@127.0.0.1" \
                "hostname; cat /etc/timezone; echo '$PASSWORD' | sudo -S -p '' test -s /var/lib/dahlia/unraid/unraid.json && echo unraid-ok; grep -c 'inet static' /etc/network/interfaces" 2>&1)"; then
            break
        fi
        sleep 5
    done
    echo "$out"
    grep -qx "$HOST" <<<"$out" || { tail -n 40 "$OUT/boot-$fw.log" >&2; echo "FAIL: no SSH login ($fw)" >&2; exit 1; }
    grep -qx Europe/Vienna <<<"$out" || { echo "FAIL: time zone" >&2; exit 1; }
    grep -qx unraid-ok <<<"$out" || { echo "FAIL: Unraid settings not copied" >&2; exit 1; }
    grep -qx 1 <<<"$out" || { echo "FAIL: static address not taken over" >&2; exit 1; }
    echo "OK: boots with $fw, SSH login, host name, time zone, static address, Unraid settings"
}

boot_check uefi
echo "== dashboard setup with the preset code"
JAR="$OUT/cookies"
ok=0
for _ in $(seq 1 30); do
    r="$(curl -sk --max-time 20 -c "$JAR" -b "$JAR" -H 'X-Dahlia-Request: 1' -H 'Content-Type: application/json' \
        -d "{\"code\":\"$setup\",\"username\":\"$ACCOUNT\",\"password\":\"$PASSWORD\"}" \
        "https://127.0.0.1:$HTTPS_PORT/api/setup" || true)"
    if grep -q "\"user\":\"$ACCOUNT\"" <<<"$r"; then ok=1; break; fi
    sleep 5
done
[ "$ok" = 1 ] || { echo "FAIL: setup with the preset code: $r" >&2; exit 1; }
echo "OK: dashboard accepts the setup code from the helper"
kill "$qemu_pid"; wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""

boot_check bios
echo "PASS"
