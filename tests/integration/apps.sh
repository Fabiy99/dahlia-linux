#!/bin/bash
# Integration test for Dahlia apps with real Docker, nginx and dahlia-agent.
#
# A loop disk becomes the array, a local catalog (file://) holds a small
# test app. Through the agent: refresh the catalog, install, check the
# container (port on 127.0.0.1 only, data under /mnt/user/appdata, nginx
# port link from the LAN side), stop, start, update, remove.
#
# Must run as root on a disposable machine with dahlia-storage, dahlia-agent,
# docker and nginx installed.
set -euo pipefail

WORK="$(mktemp -d /var/tmp/dahlia-apps-test.XXXXXX)"
LOOP=""
UUID=""
export DAHLIA_ALLOW_LOOP=1

fail() { echo "FAIL: $*" >&2; diag >&2; exit 1; }
step() { echo; echo "== $*"; }
call() { dahlia-agent-call --timeout 120 "$@"; }

diag() {
    echo "--- dahlia-app-whoami journal"
    journalctl -u dahlia-app-whoami.service --no-pager | tail -40 || true
    docker ps -a || true
}

cleanup() {
    set +e
    dahlia-apps remove whoami --delete-data >/dev/null 2>&1
    [ -n "$UUID" ] && dahlia-disk remove "$UUID" >/dev/null 2>&1
    [ -n "$LOOP" ] && losetup -d "$LOOP"
    rm -rf "$WORK"
}
trap cleanup EXIT

wait_job() {
    local state
    for _ in $(seq 1 120); do
        state="$(call apps.job '{"id": "whoami"}')"
        case "$(jq -r .active <<<"$state")" in
            active|activating) sleep 2 ;;
            *) break ;;
        esac
    done
    echo "$state"
    jq -e '.result == "success"' <<<"$state" >/dev/null || fail "app job did not succeed"
}

step "array from a loop disk"
truncate -s 512M "$WORK/disk.img"
LOOP="$(losetup --find --show "$WORK/disk.img")"
mkfs.ext4 -q -L appsdisk "$LOOP"
udevadm trigger --action=change "$LOOP"
udevadm settle
UUID="$(blkid -s UUID -o value "$LOOP")"
dahlia-disk add "$UUID" --role data
# The default keeps 50G free per disk; the test disk is far smaller.
dahlia-disk set-minfreespace 10M
findmnt -n /mnt/user >/dev/null || fail "/mnt/user not mounted"

step "local catalog"
mkdir -p "$WORK/catalog/apps"
echo '{"apps": ["whoami"]}' > "$WORK/catalog/index.json"
cat > "$WORK/catalog/apps/whoami.yaml" <<'YAML'
schema: 1
id: whoami
name: Whoami
description: {de: Testapp, en: test app}
image: traefik/whoami:v1.10.3
web: {port: 80, public_port: 8099}
volumes:
  - {container: /config, appdata: config}
environment: {TZ: "{TZ}", PUID: "{PUID}"}
YAML
cp /etc/dahlia/apps.json "$WORK/apps.json.orig"
printf '{"catalog": "file://%s/catalog/"}\n' "$WORK" > /etc/dahlia/apps.json
call apps.catalog '{"refresh": true}' | jq -e '.apps[0].id == "whoami"' >/dev/null \
    || fail "catalog refresh failed"
cp "$WORK/apps.json.orig" /etc/dahlia/apps.json

step "install through the agent"
call apps.install '{"id": "whoami"}' >/dev/null
wait_job
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = running ] || fail "container not running"
ports="$(docker port dahlia-whoami)"
echo "$ports"
grep -q '^80/tcp -> 127.0.0.1:41000$' <<<"$ports" || fail "web port not on 127.0.0.1:41000"
if grep -q '0.0.0.0' <<<"$ports"; then fail "port published on all interfaces"; fi
[ -d /mnt/user/appdata/whoami/config ] || fail "appdata missing"
[ "$(stat -c %U /mnt/user/appdata/whoami/config)" = dahlia-apps ] || fail "appdata owner wrong"
docker inspect -f '{{.HostConfig.SecurityOpt}}' dahlia-whoami | grep -q no-new-privileges \
    || fail "no-new-privileges missing"
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' dahlia-whoami | grep -qx no \
    || fail "restart policy must be no"

step "port link through nginx"
lan_ip="$(hostname -I | awk '{print $1}')"
for _ in $(seq 1 10); do
    curl -sf "http://$lan_ip:8099/" -o "$WORK/whoami.txt" && break
    sleep 1
done
grep -q '^Hostname:' "$WORK/whoami.txt" || fail "app not reachable on $lan_ip:8099"
if curl -sf --max-time 3 "http://$lan_ip:41000/" >/dev/null; then
    fail "internal port reachable from the LAN side"
fi

step "list, stop, start, update"
call apps.list | jq -e '.apps[0].status == "running"' >/dev/null || fail "apps.list wrong"
call apps.stop '{"id": "whoami"}' >/dev/null
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = exited ] || fail "stop failed"
dahlia-apps start-enabled
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = exited ] \
    || fail "stopped app was started at boot"
call apps.start '{"id": "whoami"}' >/dev/null
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = running ] || fail "start failed"
call apps.update '{"id": "whoami"}' >/dev/null
wait_job

step "remove"
touch /mnt/user/appdata/whoami/config/keep
call apps.remove '{"id": "whoami"}' >/dev/null
if docker inspect dahlia-whoami >/dev/null 2>&1; then fail "container still there"; fi
[ -f /mnt/user/appdata/whoami/config/keep ] || fail "data deleted without asking"
[ ! -e /etc/nginx/conf.d/dahlia-app-whoami.conf ] || fail "nginx config left over"
if curl -sf --max-time 3 "http://$lan_ip:8099/" >/dev/null; then fail "port link still open"; fi

echo
echo "apps integration test passed"
